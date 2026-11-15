from __future__ import annotations

import http.client
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from filesystem import sort_project  # noqa: E402
from project import init_project, load_project  # noqa: E402
from tracking import build_tracker, contrast_ratio, record_event, status_colors  # noqa: E402
from versions import scan_versions  # noqa: E402
from webapp import COOKIE_NAME, BrowserServer  # noqa: E402

FILES = {
    "SH05_layout_v001.ma": b"x",
    "SH05_layout_v002.ma": b"x",
    "SH05_comp_v001.png": b"\x89PNG fake",
    "SH05_render_v001.mp4": bytes(range(256)) * 4,  # 1024 bytes
    "prop_OilLamp_v001.fbx": b"x",
}


class ServerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        init_project(self.root)
        for name, data in FILES.items():
            (self.root / name).write_bytes(data)
        with redirect_stdout(io.StringIO()):
            sort_project(self.root, fast_mode=True)
        self.project = load_project(self.root)
        tracker = build_tracker(self.project, scan_versions(self.project))
        record_event(tracker, "status", "SH05", "layout", user="alex", status="wfa", note="<b>review</b> me")

        self.server = BrowserServer(self.root)
        self.server.start()
        self.revealed: list[Path] = []
        self.server.reveal = self.revealed.append  # don't open a real file manager
        self.host = f"127.0.0.1:{self.server.port}"
        self.cookie = f"{COOKIE_NAME}={self.server.token}"

    def tearDown(self) -> None:
        self.server.stop()
        self._tmp.cleanup()

    def request(self, method: str, path: str, body=None, headers: dict | None = None, auth: bool = True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        all_headers = {"Host": self.host}
        if auth:
            all_headers["Cookie"] = self.cookie
        if body is not None and not isinstance(body, (bytes, str)):
            body = json.dumps(body)
            all_headers["Content-Type"] = "application/json"
        all_headers.update(headers or {})
        connection.request(method, path, body=body, headers=all_headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, data

    def json(self, method: str, path: str, body=None, **kwargs):
        response, data = self.request(method, path, body, **kwargs)
        return response.status, json.loads(data or b"{}")


class SecurityTests(ServerTestCase):
    def test_launch_link_swaps_token_for_cookie(self) -> None:
        response, _ = self.request("GET", f"/?token={self.server.token}", auth=False)
        self.assertEqual(response.status, 303)
        self.assertEqual(response.getheader("Location"), "/")
        cookie = response.getheader("Set-Cookie")
        self.assertIn(self.cookie, cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_wrong_token(self) -> None:
        response, _ = self.request("GET", "/?token=nope", auth=False)
        self.assertEqual(response.status, 403)

    def test_everything_needs_the_cookie(self) -> None:
        for path in ("/", "/static/app.js", "/api/overview", "/media/whatever.png"):
            with self.subTest(path=path):
                response, _ = self.request("GET", path, auth=False)
                self.assertEqual(response.status, 403)
        response, _ = self.request("GET", "/api/overview", headers={"Cookie": f"{COOKIE_NAME}=guess"})
        self.assertEqual(response.status, 403)

    def test_foreign_host_header_is_rejected(self) -> None:
        # DNS rebinding: a hostile site's name pointed at 127.0.0.1
        response, _ = self.request("GET", "/api/overview", headers={"Host": f"evil.example:{self.server.port}"})
        self.assertEqual(response.status, 403)

    def test_writes_need_json_and_our_origin(self) -> None:
        body = {"entity": "SH05", "task": "layout", "user": "x", "status": "done"}
        response, _ = self.request("POST", "/api/task", body=json.dumps(body),
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(response.status, 415)
        status, _ = self.json("POST", "/api/task", body, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.json("POST", "/api/task", body, auth=False)
        self.assertEqual(status, 403)
        self.assertEqual(build_tracker(self.project).task("SH05", "layout").status, "wfa")

    def test_rejected_post_leaves_connection_usable(self) -> None:
        # A rejected body must still be consumed, or it's parsed as the next request
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        connection.request("POST", "/api/task", body="x=1" * 50,
                           headers={"Host": self.host, "Cookie": self.cookie, "Content-Type": "text/plain"})
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 415)
        connection.request("GET", "/api/version", headers={"Host": self.host, "Cookie": self.cookie})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn("version", json.loads(response.read()))
        connection.close()

    def test_security_headers(self) -> None:
        response, _ = self.request("GET", "/")
        self.assertEqual(response.status, 200)
        self.assertIn("script-src 'self'", response.getheader("Content-Security-Policy"))
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")

    def test_media_cannot_escape_the_project(self) -> None:
        secret = self.root.parent / f"{self.root.name}-secret.png"
        secret.write_bytes(b"secret")
        self.addCleanup(secret.unlink)
        (self.root / "_pipeline" / "notes.png").write_bytes(b"ignored")
        for path in (f"/media/..%2F{secret.name}", f"/media/../{secret.name}",
                     "/media/_pipeline/project.toml", "/media/_pipeline/notes.png",
                     "/media/production/Acts/Act_1/SQ_01/SC_05/SH05/layout/SH05_layout_v001.ma"):
            with self.subTest(path=path):
                response, data = self.request("GET", path)
                self.assertEqual(response.status, 404)
                self.assertNotIn(b"secret", data)

    def test_static_files_are_allowlisted(self) -> None:
        response, _ = self.request("GET", "/static/../webapp.py")
        self.assertEqual(response.status, 404)


class ApiTests(ServerTestCase):
    def test_project_and_status_colours(self) -> None:
        status, data = self.json("GET", "/api/project")
        self.assertEqual(status, 200)
        self.assertEqual(data["name"], "The Ugly Princess")
        codes = [s["code"] for s in data["statuses"]]
        self.assertEqual(codes, ["todo", "ready", "wip", "wfa", "retake", "done"])

    def test_default_status_colours_meet_wcag_aa(self) -> None:
        for code, (background, text) in status_colors(self.project).items():
            with self.subTest(status=code):
                self.assertGreaterEqual(contrast_ratio(background, text), 4.5)

    def test_overview(self) -> None:
        status, data = self.json("GET", "/api/overview")
        self.assertEqual(status, 200)
        sh05 = next(e for e in data["entities"] if e["name"] == "SH05")
        self.assertEqual((sh05["act"], sh05["sequence"]), ("Act_1", "SQ_01"))
        layout = next(t for t in sh05["tasks"] if t["task"] == "layout")
        self.assertEqual((layout["status"], layout["latest"]), ("wfa", "v002"))
        self.assertTrue(sh05["thumbnail"].startswith("/media/production/"))
        self.assertTrue(any(e["name"] == "prop_OilLamp" for e in data["entities"]))

    def test_entity_detail(self) -> None:
        status, data = self.json("GET", "/api/entity?name=sh05")
        self.assertEqual(status, 200)
        self.assertEqual(data["name"], "SH05")
        layout = next(t for t in data["tasks"] if t["task"] == "layout")
        self.assertEqual(layout["notes"][0]["text"], "<b>review</b> me")  # raw text; the page escapes it
        products = {p["name"]: p for p in data["products"]}
        self.assertEqual([v["label"] for v in products["SH05_layout.ma"]["versions"]], ["v002", "v001"])
        self.assertEqual(products["SH05_render.mp4"]["versions"][0]["media"]["kind"], "video")
        status, _ = self.json("GET", "/api/entity?name=SH404")
        self.assertEqual(status, 404)

    def test_update_task(self) -> None:
        status, data = self.json("POST", "/api/task", {
            "entity": "SH05", "task": "layout", "user": "priya",
            "status": "done", "assignee": "alex", "comment": "Approved",
        }, headers={"Origin": f"http://{self.host}"})
        self.assertEqual(status, 200)
        self.assertEqual(data["saved"], 2)
        tasks = {t["task"]: t for t in data["entity"]["tasks"]}
        self.assertEqual((tasks["layout"]["status"], tasks["layout"]["assignee"]), ("done", "alex"))
        self.assertEqual(tasks["layout"]["notes"][-1]["text"], "Approved")
        self.assertEqual(tasks["3d"]["status"], "ready")  # automation

    def test_update_task_errors(self) -> None:
        base = {"entity": "SH05", "task": "layout", "user": "priya"}
        status, data = self.json("POST", "/api/task", {**base, "status": "finished"})
        self.assertEqual(status, 400)
        self.assertIn("Unknown status", data["error"])
        status, data = self.json("POST", "/api/task", {**base, "user": "  ", "status": "done"})
        self.assertEqual(status, 400)
        status, _ = self.json("POST", "/api/task", {"entity": "SH05"})
        self.assertEqual(status, 400)

    def test_version_changes_when_someone_saves(self) -> None:
        _, before = self.json("GET", "/api/version")
        self.json("POST", "/api/task", {"entity": "SH05", "task": "comp", "user": "sam", "status": "wip"})
        _, after = self.json("GET", "/api/version")
        self.assertNotEqual(before["version"], after["version"])

    def test_media_and_ranges(self) -> None:
        _, data = self.json("GET", "/api/entity?name=SH05")
        video = next(p for p in data["products"] if p["name"] == "SH05_render.mp4")["versions"][0]["media"]["url"]
        response, body = self.request("GET", video)
        self.assertEqual((response.status, len(body)), (200, 1024))
        self.assertEqual(response.getheader("Accept-Ranges"), "bytes")

        response, body = self.request("GET", video, headers={"Range": "bytes=100-199"})
        self.assertEqual(response.status, 206)
        self.assertEqual(response.getheader("Content-Range"), "bytes 100-199/1024")
        self.assertEqual(body, (bytes(range(256)) * 4)[100:200])

        response, body = self.request("GET", video, headers={"Range": "bytes=-24"})
        self.assertEqual((response.status, len(body)), (206, 24))
        response, _ = self.request("GET", video, headers={"Range": "bytes=5000-"})
        self.assertEqual(response.status, 416)

    def test_locked_file_gives_a_clear_error(self) -> None:
        # e.g. OneDrive holding the events file on Windows
        real_open = Path.open

        def locked_events(path, *args, **kwargs):
            if path.suffix == ".jsonl" and args and "a" in args[0]:
                raise PermissionError(13, "Permission denied", str(path))
            return real_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", locked_events):
            status, data = self.json("POST", "/api/task", {"entity": "SH05", "task": "comp", "user": "alex",
                                                           "status": "wip"})
        self.assertEqual(status, 503)
        self.assertRegex(data["error"], r"Couldn't access alex@.*\.jsonl: Permission denied")
        self.assertIn("try again", data["error"])
        self.assertEqual(build_tracker(self.project).task("SH05", "comp").status, "todo")

    def test_unexpected_error_is_reported_and_server_survives(self) -> None:
        with mock.patch("webapp.overview_json", side_effect=RuntimeError("boom")), \
                redirect_stderr(io.StringIO()) as log:
            status, data = self.json("GET", "/api/overview")
        self.assertEqual((status, data["error"]), (500, "Unexpected error: boom"))
        self.assertIn("RuntimeError: boom", log.getvalue())  # full traceback for whoever runs the server
        status, _ = self.json("GET", "/api/overview")
        self.assertEqual(status, 200)

    def test_expired_session_explains_itself(self) -> None:
        status, data = self.json("GET", "/api/overview", auth=False)
        self.assertEqual(status, 403)
        self.assertIn("Open the Project Browser again", data["error"])

    def test_reveal(self) -> None:
        _, data = self.json("GET", "/api/entity?name=SH05")
        path = data["products"][0]["versions"][0]["path"]
        status, _ = self.json("POST", "/api/reveal", {"path": path})
        self.assertEqual(status, 200)
        self.assertEqual(self.revealed, [self.root / path])
        status, _ = self.json("POST", "/api/reveal", {"path": "../../etc/passwd"})
        self.assertEqual(status, 404)

    def test_rescan_picks_up_new_files(self) -> None:
        (self.root / "SH05_layout_v003.ma").write_text("x")
        with redirect_stdout(io.StringIO()):
            sort_project(self.root, fast_mode=True)
        status, _ = self.json("POST", "/api/rescan", {})
        self.assertEqual(status, 200)
        _, data = self.json("GET", "/api/overview")
        sh05 = next(e for e in data["entities"] if e["name"] == "SH05")
        self.assertEqual(next(t for t in sh05["tasks"] if t["task"] == "layout")["latest"], "v003")


if __name__ == "__main__":
    unittest.main()
