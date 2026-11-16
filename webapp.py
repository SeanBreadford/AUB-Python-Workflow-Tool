from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from filesystem import is_ignored_file, is_in_ignored_dir
from project import Project, ProjectConfigError, ProjectNotFoundError, load_project
from sorter_service import SorterBusy, SorterService, WatchUnavailable
from tracking import (
    Tracker,
    TrackingError,
    build_tracker,
    current_user,
    events_stamp,
    flag_rank,
    status_colors,
    task_flags,
    update_task,
)
from versions import VersionIndex, scan_versions

# Project browser served as a local web page.
#
# It runs on each person's own computer and reads the synced project folder,
# exactly like the CLI, so there is still no shared server. Because any web
# page you visit could try to talk to a server on localhost, every request is
# checked:
#   - the server only listens on 127.0.0.1
#   - the Host header must be our own address (blocks DNS rebinding)
#   - a secret token from the launch URL is swapped for a SameSite=Strict
#     cookie, which other sites' requests never carry
#   - writes must be JSON POSTs from our own Origin
#   - media is only served from inside the project, never ignored files

WEB_DIR = Path(__file__).with_name("web")
COOKIE_NAME = "pb_session"
SESSION_ENDED = "This page's session has ended (the app was restarted). Open the Project Browser again from the app."
RESCAN_AFTER_SECONDS = 30
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v"}
MEDIA_EXT = IMAGE_EXT | VIDEO_EXT

# Optional: with Pillow installed, images are served as small cached JPEG
# thumbnails (real renders are often 2K-4K), and formats browsers can't show
# (TIFF, TGA, BMP) get previews too. Without it, originals are served as-is.
try:
    from PIL import Image, ImageOps

    Image.init()
    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the machine
    PIL_AVAILABLE = False
CONVERTIBLE_EXT = {".tif", ".tiff", ".tga", ".bmp"}
THUMB_WIDTHS = (320, 1920)  # grid/panel, and full view for formats browsers can't show


def thumb_cache_dir() -> Path:
    """Per-computer cache, never inside the synced project."""
    override = os.getenv("FILE_SORTER_CACHE")
    if override:
        base = Path(override)
    elif sys.platform.startswith("win"):
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "TUPFileSorter"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "TUPFileSorter"
    else:
        base = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")) / "tup-file-sorter"
    return base / "thumbs"


def make_thumbnail(path: Path, width: int) -> Path:
    """A JPEG at most `width` wide, cached by file, size, modified time and width."""
    stat = path.stat()
    key = hashlib.sha1(f"{path}|{stat.st_size}|{stat.st_mtime_ns}|{width}".encode()).hexdigest()
    cached = thumb_cache_dir() / key[:2] / f"{key}.jpg"
    if cached.exists():
        return cached
    with Image.open(path) as image:
        image.draft("RGB", (width, width))  # fast path for big JPEGs
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGBA") if "A" in image.getbands() else image.convert("RGB")
        if image.mode == "RGBA":
            # Flatten transparency onto the page background instead of black
            background = Image.new("RGB", image.size, (42, 42, 42))
            background.paste(image, mask=image.getchannel("A"))
            image = background
        image.thumbnail((width, width * 4))
        cached.parent.mkdir(parents=True, exist_ok=True)
        temporary = cached.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        image.convert("RGB").save(temporary, "JPEG", quality=82, optimize=True)
    os.replace(temporary, cached)  # atomic: a half-written thumbnail is never served
    return cached
STATIC_FILES = {"index.html", "app.css", "app.js"}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "media-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def open_in_file_browser(path: Path) -> None:
    """Show a file's folder in Explorer / Finder / the Linux file manager."""
    folder = path if path.is_dir() else path.parent
    if sys.platform.startswith("win"):
        os.startfile(folder)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def media_kind(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in IMAGE_EXT or (PIL_AVAILABLE and ext in CONVERTIBLE_EXT):
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    return None


# Project State
class ProjectState:
    """Caches the version index and tracker; rebuilds them when things change."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.lock = threading.Lock()
        self.project: Project | None = None
        self.config_mtime: int | None = None
        self.index: VersionIndex | None = None
        self.scanned_at = 0.0
        self.scan_count = 0
        self.tracker: Tracker | None = None
        self.tracker_stamp: tuple | None = None
        self.invalidations = 0

    def invalidate(self) -> None:
        """Files moved (e.g. after a sort): rescan on the next request."""
        with self.lock:
            self.index = None
            self.invalidations += 1  # so version() changes and open pages refetch

    def get(self, rescan: bool = False) -> tuple[Project, VersionIndex, Tracker]:
        with self.lock:
            project = load_project(self.root)
            mtime = project.config_path.stat().st_mtime_ns
            if mtime != self.config_mtime:
                # Config edited: everything derived from it is stale
                self.config_mtime, self.index, self.tracker = mtime, None, None
            self.project = project

            stale = time.monotonic() - self.scanned_at > RESCAN_AFTER_SECONDS
            if rescan or stale or self.index is None:
                self.index = scan_versions(project)
                self.scanned_at = time.monotonic()
                self.scan_count += 1
                self.tracker = None

            stamp = events_stamp(project)
            if self.tracker is None or stamp != self.tracker_stamp:
                self.tracker = build_tracker(project, self.index)
                self.tracker_stamp = stamp
            return project, self.index, self.tracker

    def version(self) -> str:
        """Cheap fingerprint the page polls to know when to refetch."""
        with self.lock:
            project = load_project(self.root)
            parts = (project.config_path.stat().st_mtime_ns, self.scan_count, self.invalidations,
                     events_stamp(project))
        return hashlib.sha1(repr(parts).encode()).hexdigest()[:16]


# JSON Builders
def _media_entry(path: Path, project: Project) -> dict | None:
    kind = media_kind(path)
    if kind is None:
        return None
    rel = "/".join(quote(part, safe="") for part in path.relative_to(project.root).parts)
    original = f"/media/{rel}"
    if kind == "image" and PIL_AVAILABLE:
        stamp = path.stat().st_mtime_ns  # changes when the file does, so browsers refetch
        thumb = lambda width: f"/thumb/{rel}?w={width}&v={stamp}"  # noqa: E731
        full = original if path.suffix.lower() in IMAGE_EXT else thumb(1920)
        return {"kind": kind, "url": full, "thumb": thumb(320)}
    return {"kind": kind, "url": original, "thumb": original}


def _versions_for(index: VersionIndex, project: Project, entity: str) -> list[dict]:
    found = index.find(entity)
    if not found:
        return []
    products = []
    for product in found.sorted_products():
        versions = []
        for version in reversed(product.history()):
            files = sorted(version.files)
            preview = files[len(files) // 2] if version.is_sequence else files[0]
            versions.append({
                "label": version.label,
                "number": version.number,
                "modified": version.modified,
                "files": len(files),
                "sequence": version.is_sequence,
                "path": files[0].relative_to(project.root).as_posix(),
                "copies": len(version.copies),
                "artists": list(version.artists),
                "media": _media_entry(preview, project),
            })
        products.append({"name": product.name, "task": product.task, "versions": versions})
    return products


def _thumbnail(index: VersionIndex, project: Project, entity: str) -> str | None:
    """Newest image among the latest versions of an entity, for the grid."""
    found = index.find(entity)
    if not found:
        return None
    best = None
    for product in found.products.values():
        latest = product.latest
        files = sorted(latest.files)
        candidate = files[len(files) // 2] if latest.is_sequence else files[0]
        if media_kind(candidate) == "image" and (best is None or latest.modified > best[0]):
            best = (latest.modified, candidate)
    return _media_entry(best[1], project)["thumb"] if best else None


def _task_json(project: Project, state, index: VersionIndex) -> dict:
    found = index.find(state.entity)
    latest = None
    if found:
        products = [p for p in found.products.values() if p.task == state.task]
        if products:
            latest = max((p.latest for p in products), key=lambda v: v.modified).label
    return {
        "task": state.task,
        "status": state.status,
        "assignee": state.assignee,
        "updated": state.updated,
        "updated_by": state.updated_by,
        "last_activity": state.last_activity,
        "last_artist": state.last_artist,
        "latest": latest,
        "decided_on": state.decided_on,
        "flags": [{"code": f.code, "message": f.message}
                  for f in sorted(task_flags(project, state), key=flag_rank)],
        "notes": [{"time": n.time, "user": n.user, "text": n.text, "status": n.status} for n in state.notes],
    }


def workflow_actions(project: Project) -> dict[str, str | None]:
    """
    Which status each one-click action sets, worked out from the config's
    flags so custom workflows still get sensible buttons.
    """
    def first(flag: str) -> str | None:
        return next((s.code for s in project.statuses if getattr(s, flag)), None)

    plain = [s.code for s in project.statuses
             if not (s.default or s.done or s.feedback_request or s.retake)]
    start = project.auto_started or (plain[-1] if plain else None)
    return {"start": start, "review": first("feedback_request"), "approve": first("done"),
            "changes": first("retake"), "reopen": start}


def project_json(project: Project) -> dict:
    colors = status_colors(project)
    return {
        "name": project.name,
        "code": project.code,
        "user": current_user(),
        "statuses": [
            {"code": s.code, "name": s.name, "label": s.label or s.code, "default": s.default, "done": s.done,
             "feedback_request": s.feedback_request, "retake": s.retake,
             "background": colors[s.code][0], "text": colors[s.code][1]}
            for s in project.statuses
        ],
        "task_types": project.task_types,
        "actions": workflow_actions(project),
        "team": [member.name for member in project.team],
    }


def overview_json(project: Project, index: VersionIndex, tracker: Tracker) -> dict:
    entities = []
    for entity in tracker.entities:
        act = sequence = None
        if entity.context:
            act, sequence, *_ = [part.strip() for part in entity.context.split("/")]
        tasks = [_task_json(project, s, index) for s in tracker.tasks_for(entity.name)]
        for task in tasks:
            task.pop("notes")
        entities.append({
            "name": entity.name,
            "type": entity.entity_type,
            "act": act,
            "sequence": sequence,
            "tasks": tasks,
            "thumbnail": _thumbnail(index, project, entity.name),
        })
    return {
        "entities": entities,
        "unsorted": len(index.unsorted),
        "warnings": [f"Skipped {tracker.unreadable_lines} unreadable event line(s)."]
        if tracker.unreadable_lines else [],
    }


def activity_json(tracker: Tracker, limit: int = 300) -> list[dict]:
    """Newest first: files arriving, status changes, notes and assignments."""
    return list(reversed(tracker.activity[-limit:])) if limit > 0 else []


def entity_json(project: Project, index: VersionIndex, tracker: Tracker, name: str) -> dict | None:
    entity = tracker.find(name)
    if entity is None:
        return None
    return {
        "name": entity.name,
        "type": entity.entity_type,
        "context": entity.context,
        "tasks": [_task_json(project, s, index) for s in tracker.tasks_for(entity.name)],
        "products": _versions_for(index, project, entity.name),
        "thumbnail": _thumbnail(index, project, entity.name),
    }


# Request Handling
class Handler(BaseHTTPRequestHandler):
    server: "BrowserServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return  # keep the terminal quiet; errors are returned to the page

    # Checks
    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.server.allowed_hosts

    def _authed(self) -> bool:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        value = cookie[COOKIE_NAME].value if COOKIE_NAME in cookie else ""
        return hmac.compare_digest(value, self.server.token)

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin in self.server.allowed_origins

    # Responses
    def _send(self, status: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
        self._response_started = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _guard(self, action) -> None:
        """Run a route; turn any failure into a JSON error the page can show."""
        self._response_started = False
        try:
            action()
        except (ProjectNotFoundError, ProjectConfigError) as error:
            self._fail(HTTPStatus.CONFLICT, str(error))
        except TrackingError as error:
            self._fail(HTTPStatus.BAD_REQUEST, str(error))
        except OSError as error:
            # Usually a file locked by the sync app (OneDrive on Windows) or a permissions problem
            name = Path(error.filename).name if error.filename else "a project file"
            self._fail(HTTPStatus.SERVICE_UNAVAILABLE,
                       f"Couldn't access {name}: {error.strerror or error}. If the sync app is busy "
                       "with it, try again in a moment.")
        except Exception as error:  # noqa: BLE001 - last resort, keep the server alive
            traceback.print_exc()
            self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, f"Unexpected error: {error}")

    def _fail(self, status: int, message: str) -> None:
        if self._response_started:
            # Headers already sent (e.g. mid-video): all we can do is hang up
            self.close_connection = True
            return
        self._error(status, message)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    # Methods
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Unexpected Host header")
        url = urlsplit(self.path)

        # Launch link: trade the token for a cookie, then drop it from the URL
        if url.path == "/" and "token" in parse_qs(url.query):
            token = parse_qs(url.query)["token"][0]
            if not hmac.compare_digest(token, self.server.token):
                return self._error(HTTPStatus.FORBIDDEN, "Invalid link; use the one the app printed")
            cookie = f"{COOKIE_NAME}={token}; HttpOnly; SameSite=Strict; Path=/"
            return self._send(HTTPStatus.SEE_OTHER, b"", "text/plain",
                              {"Location": "/", "Set-Cookie": cookie})

        if not self._authed():
            if url.path.startswith("/api/"):
                return self._error(HTTPStatus.FORBIDDEN, SESSION_ENDED)
            return self._send(HTTPStatus.FORBIDDEN,
                              b"Open the project browser from the app (or the link it printed).",
                              "text/plain; charset=utf-8")

        self._guard(lambda: self._route_get(url))

    def _route_get(self, url) -> None:
        if url.path == "/":
            return self._static("index.html")
        if url.path.startswith("/static/"):
            return self._static(url.path[len("/static/"):])
        if url.path.startswith("/media/"):
            return self._media(url.path[len("/media/"):])
        if url.path.startswith("/thumb/"):
            return self._thumb(url.path[len("/thumb/"):], parse_qs(url.query))
        if url.path.startswith("/api/"):
            return self._api_get(url.path[len("/api/"):], parse_qs(url.query))
        return self._error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        # Read the body before any check: on a kept-alive connection, unread
        # bytes would otherwise be parsed as the next request
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 <= length <= 64_000:
            self.close_connection = True
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request too large")
        raw = self.rfile.read(length)

        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Unexpected Host header")
        if not self._authed():
            return self._error(HTTPStatus.FORBIDDEN, SESSION_ENDED)
        if not self._origin_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Not allowed")
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Expected JSON")
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "Invalid JSON")

        self._guard(lambda: self._route_post(urlsplit(self.path).path, body))

    def _route_post(self, path: str, body: dict) -> None:
        if path == "/api/task":
            return self._api_task(body)
        if path == "/api/rescan":
            self.server.state.get(rescan=True)
            return self._json({"ok": True})
        if path == "/api/reveal":
            return self._api_reveal(body)
        if path == "/api/sorter/run":
            try:
                started = self.server.sorter.start_sort(preview=body.get("preview") is True,
                                                        fast=body.get("fast") is True)
            except SorterBusy as error:
                return self._error(HTTPStatus.CONFLICT, str(error))
            return self._json(started)
        if path == "/api/sorter/watch":
            try:
                watch = self.server.sorter.set_watch(body.get("on") is True)
            except WatchUnavailable as error:
                return self._error(HTTPStatus.BAD_REQUEST, str(error))
            return self._json({"watch": dict(watch, counts=dict(watch["counts"])) if watch else None})
        return self._error(HTTPStatus.NOT_FOUND, "Not found")

    # Static Files
    def _static(self, name: str) -> None:
        if name not in STATIC_FILES:
            return self._error(HTTPStatus.NOT_FOUND, "Not found")
        body = (WEB_DIR / name).read_bytes()
        content_type = {"html": "text/html", "css": "text/css", "js": "text/javascript"}[name.rsplit(".", 1)[1]]
        self._send(HTTPStatus.OK, body, content_type + "; charset=utf-8")

    # Media
    def _resolve_media(self, rel: str) -> Path | None:
        project = self.server.state.get()[0]
        parts = [unquote(part) for part in rel.split("/") if part]
        if not parts or any(part in ("..", ".") or "\\" in part for part in parts):
            return None
        path = project.root.joinpath(*parts).resolve()
        if project.root not in path.parents or not path.is_file() or media_kind(path) is None:
            return None
        if is_ignored_file(path, project) or is_in_ignored_dir(path, project):
            return None
        return path

    def _thumb(self, rel: str, query: dict) -> None:
        path = self._resolve_media(rel)
        if path is None or media_kind(path) != "image":
            return self._error(HTTPStatus.NOT_FOUND, "No such image")
        if not PIL_AVAILABLE:
            return self._media(rel)
        raw = query.get("w", ["320"])[0]
        width = int(raw) if raw.isdigit() and int(raw) in THUMB_WIDTHS else 320
        try:
            thumbnail = make_thumbnail(path, width)
        except (OSError, ValueError, Image.DecompressionBombError):
            # Unreadable or unsupported image: the original is the best we can do
            if path.suffix.lower() in IMAGE_EXT:
                return self._media(rel)
            return self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Can't make a preview of this image")
        body = thumbnail.read_bytes()
        self._response_started = True
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "private, max-age=86400")  # URL changes when the file does
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _media(self, rel: str) -> None:
        path = self._resolve_media(rel)
        if path is None:
            return self._error(HTTPStatus.NOT_FOUND, "No such media")
        size = path.stat().st_size
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix.lower() == ".mov":
            content_type = "video/mp4"  # most browsers only play H.264 .mov when told it's mp4

        # Video seeking needs byte ranges
        start, end = 0, size - 1
        status = HTTPStatus.OK
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if match and (match[1] or match[2]):
            if match[1]:
                start = int(match[1])
                end = min(int(match[2]), size - 1) if match[2] else size - 1
            else:
                start = max(0, size - int(match[2]))
            if start > end or start >= size:
                return self._send(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"", "text/plain",
                                  {"Content-Range": f"bytes */{size}"})
            status = HTTPStatus.PARTIAL_CONTENT

        length = end - start + 1
        self._response_started = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "private, max-age=60")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return  # the browser stopped reading, e.g. seeked elsewhere
                remaining -= len(chunk)

    # API
    def _api_get(self, route: str, query: dict) -> None:
        state = self.server.state
        if route == "version":
            return self._json({"version": state.version()})
        project, index, tracker = state.get()
        if route == "project":
            return self._json(project_json(project))
        if route == "overview":
            return self._json(overview_json(project, index, tracker))
        if route == "sorter":
            raw = query.get("after", ["0"])[0]
            return self._json(self.server.sorter.status(int(raw) if raw.isdigit() else 0))
        if route == "activity":
            raw = query.get("limit", ["300"])[0]
            limit = min(int(raw), 2000) if raw.isdigit() else 300
            return self._json({"items": activity_json(tracker, limit)})
        if route == "entity":
            name = query.get("name", [""])[0]
            data = entity_json(project, index, tracker, name)
            return self._json(data) if data else self._error(HTTPStatus.NOT_FOUND, f"Unknown '{name}'")
        return self._error(HTTPStatus.NOT_FOUND, "Not found")

    def _api_task(self, body: dict) -> None:
        for key in ("entity", "task", "user"):
            if not isinstance(body.get(key), str):
                return self._error(HTTPStatus.BAD_REQUEST, f"Missing '{key}'")
        for key in ("status", "assignee", "comment"):
            if key in body and body[key] is not None and not isinstance(body[key], str):
                return self._error(HTTPStatus.BAD_REQUEST, f"'{key}' must be text")

        project, index, tracker = self.server.state.get()
        options = {"status": body.get("status"), "comment": body.get("comment"),
                   "redecide": body.get("redecide") is True}
        if "assignee" in body:
            options["assignee"] = body["assignee"]
        events = update_task(tracker, body["entity"], body["task"], body["user"], **options)

        project, index, tracker = self.server.state.get()  # include the change just made
        return self._json({"saved": len(events),
                           "entity": entity_json(project, index, tracker, body["entity"])})

    def _api_reveal(self, body: dict) -> None:
        """Show a file in the OS file manager (the page itself can't)."""
        rel = body.get("path")
        if not isinstance(rel, str):
            return self._error(HTTPStatus.BAD_REQUEST, "Missing 'path'")
        project = self.server.state.get()[0]
        path = (project.root / rel).resolve()
        if project.root not in path.parents or not path.exists():
            return self._error(HTTPStatus.NOT_FOUND, "No such file")
        self.server.reveal(path)
        return self._json({"ok": True})


# Server
class BrowserServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path, port: int = 0) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.state = ProjectState(root)
        self.sorter = SorterService(self.state.root, on_change=self.state.invalidate)
        self.token = secrets.token_urlsafe(24)
        self.port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.allowed_origins = {f"http://{host}" for host in self.allowed_hosts}
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?token={self.token}"

    def reveal(self, path: Path) -> None:
        open_in_file_browser(path)

    def start(self) -> str:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        self.sorter.shutdown()
        self.shutdown()
        self.server_close()


def serve(root: Path, port: int = 0, open_browser: bool = True) -> None:
    """Run the project browser until Ctrl+C (used by main.py --serve)."""
    load_project(root)  # fail early with a clear message
    try:
        server = BrowserServer(root, port)
    except OSError as error:
        raise SystemExit(f"Error: can't use port {port} ({error.strerror}). "
                         "Pick another with --port, or leave it out to use any free port.")
    url = server.url
    print(f"Project browser: {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.sorter.shutdown()
        server.server_close()
        print("Project browser stopped.")
