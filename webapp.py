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
import webbrowser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from filesystem import is_ignored_file, is_in_ignored_dir
from project import Project, ProjectConfigError, ProjectNotFoundError, load_project
from tracking import (
    Tracker,
    TrackingError,
    build_tracker,
    current_user,
    events_stamp,
    status_colors,
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
RESCAN_AFTER_SECONDS = 30
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v"}
MEDIA_EXT = IMAGE_EXT | VIDEO_EXT
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
    if ext in IMAGE_EXT:
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

    def invalidate(self) -> None:
        """Files moved (e.g. after a sort): rescan on the next request."""
        with self.lock:
            self.index = None

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
            parts = (project.config_path.stat().st_mtime_ns, self.scan_count, events_stamp(project))
        return hashlib.sha1(repr(parts).encode()).hexdigest()[:16]


# JSON Builders
def _media_entry(path: Path, project: Project) -> dict | None:
    kind = media_kind(path)
    if kind is None:
        return None
    rel = path.relative_to(project.root)
    return {"kind": kind, "url": "/media/" + "/".join(quote(part, safe="") for part in rel.parts)}


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
    return _media_entry(best[1], project)["url"] if best else None


def _task_json(state, index: VersionIndex) -> dict:
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
        "latest": latest,
        "notes": [{"time": n.time, "user": n.user, "text": n.text, "status": n.status} for n in state.notes],
    }


def project_json(project: Project) -> dict:
    colors = status_colors(project)
    return {
        "name": project.name,
        "code": project.code,
        "user": current_user(),
        "statuses": [
            {"code": s.code, "name": s.name, "default": s.default, "done": s.done,
             "feedback_request": s.feedback_request, "retake": s.retake,
             "background": colors[s.code][0], "text": colors[s.code][1]}
            for s in project.statuses
        ],
        "task_types": project.task_types,
    }


def overview_json(project: Project, index: VersionIndex, tracker: Tracker) -> dict:
    entities = []
    for entity in tracker.entities:
        act = sequence = None
        if entity.context:
            act, sequence, *_ = [part.strip() for part in entity.context.split("/")]
        tasks = [_task_json(s, index) for s in tracker.tasks_for(entity.name)]
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


def entity_json(project: Project, index: VersionIndex, tracker: Tracker, name: str) -> dict | None:
    entity = tracker.find(name)
    if entity is None:
        return None
    return {
        "name": entity.name,
        "type": entity.entity_type,
        "context": entity.context,
        "tasks": [_task_json(s, index) for s in tracker.tasks_for(entity.name)],
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
            return self._send(HTTPStatus.FORBIDDEN,
                              b"Open the project browser from the app (or the link it printed).",
                              "text/plain; charset=utf-8")

        try:
            if url.path == "/":
                return self._static("index.html")
            if url.path.startswith("/static/"):
                return self._static(url.path[len("/static/"):])
            if url.path.startswith("/media/"):
                return self._media(url.path[len("/media/"):])
            if url.path.startswith("/api/"):
                return self._api_get(url.path[len("/api/"):], parse_qs(url.query))
        except (ProjectNotFoundError, ProjectConfigError, TrackingError) as error:
            return self._error(HTTPStatus.CONFLICT, str(error))
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
        if not self._authed() or not self._origin_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Not allowed")
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Expected JSON")
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "Invalid JSON")

        try:
            path = urlsplit(self.path).path
            if path == "/api/task":
                return self._api_task(body)
            if path == "/api/rescan":
                self.server.state.get(rescan=True)
                return self._json({"ok": True})
            if path == "/api/reveal":
                return self._api_reveal(body)
        except (ProjectNotFoundError, ProjectConfigError) as error:
            return self._error(HTTPStatus.CONFLICT, str(error))
        except TrackingError as error:
            return self._error(HTTPStatus.BAD_REQUEST, str(error))
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
        options = {"status": body.get("status"), "comment": body.get("comment")}
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
        server.server_close()
        print("Project browser stopped.")
