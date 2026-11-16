from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sorter_service  # noqa: E402
from sorter_service import SorterService, classify  # noqa: E402
from test_webapp import ServerTestCase  # noqa: E402


class SortingApiTests(ServerTestCase):
    def drop(self, *names: str) -> None:
        for name in names:
            (self.root / name).write_text("x")

    def run_job(self, **body) -> dict:
        status, data = self.json("POST", "/api/sorter/run", body)
        self.assertEqual(status, 200, data)
        self.server.sorter.wait()
        _, data = self.json("GET", "/api/sorter")
        return data

    def test_preview_moves_nothing(self) -> None:
        self.drop("SH06_layout_v001.ma")
        data = self.run_job(preview=True, fast=True)
        self.assertEqual(data["job"]["kind"], "preview")
        self.assertTrue(data["job"]["finished"])
        self.assertEqual(data["job"]["counts"].get("preview"), 1)
        self.assertTrue((self.root / "SH06_layout_v001.ma").exists())
        self.assertIn("preview", {line["level"] for line in data["log"]})

    def test_sort_moves_files_and_refreshes_the_browser(self) -> None:
        self.drop("SH06_layout_v001.ma")
        _, before = self.json("GET", "/api/version")
        data = self.run_job(fast=True)
        self.assertEqual(data["job"]["counts"].get("moved"), 1)
        self.assertFalse((self.root / "SH06_layout_v001.ma").exists())
        _, after = self.json("GET", "/api/version")
        self.assertNotEqual(before, after)  # open pages refetch
        _, overview = self.json("GET", "/api/overview")
        sh06 = next(e for e in overview["entities"] if e["name"] == "SH06")
        self.assertEqual(next(t for t in sh06["tasks"] if t["task"] == "layout")["status"], "wip")

    def test_log_is_read_incrementally(self) -> None:
        self.drop("SH06_layout_v001.ma")
        first = self.run_job(preview=True, fast=True)
        _, again = self.json("GET", f"/api/sorter?after={first['next']}")
        self.assertEqual(again["log"], [])

    def test_one_sort_at_a_time(self) -> None:
        gate = threading.Event()  # holds the first sort open
        with mock.patch.object(sorter_service, "sort_project", side_effect=lambda *a, **k: gate.wait(5)):
            status, _ = self.json("POST", "/api/sorter/run", {})
            self.assertEqual(status, 200)
            status, data = self.json("POST", "/api/sorter/run", {})
            self.assertEqual(status, 409)
            self.assertIn("already running", data["error"])
            gate.set()
            self.server.sorter.wait()

    def test_sorting_needs_the_session(self) -> None:
        status, _ = self.json("POST", "/api/sorter/run", {}, auth=False)
        self.assertEqual(status, 403)
        status, _ = self.json("POST", "/api/sorter/run", {}, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_auto_sort_without_watchdog_explains(self) -> None:
        with mock.patch.object(sorter_service, "WATCH_AVAILABLE", False):
            status, data = self.json("POST", "/api/sorter/watch", {"on": True})
        self.assertEqual(status, 400)
        self.assertIn("pip install watchdog", data["error"])

    def test_auto_sort_on_and_off(self) -> None:
        observer = mock.Mock()
        handler_cls = mock.Mock()
        with mock.patch.object(sorter_service, "WATCH_AVAILABLE", True), \
                mock.patch.object(sorter_service, "Observer", return_value=observer), \
                mock.patch.object(sorter_service, "DebouncedHandler", handler_cls):
            status, data = self.json("POST", "/api/sorter/watch", {"on": True})
            self.assertEqual(status, 200)
            self.assertIsNotNone(data["watch"])
            observer.start.assert_called_once()
            self.assertIsNotNone(handler_cls.call_args.kwargs["reporter"])  # watch gets its own log
            status, data = self.json("POST", "/api/sorter/watch", {"on": False})
            self.assertIsNone(data["watch"])
            observer.stop.assert_called_once()


class ServiceTests(unittest.TestCase):
    def test_classify(self) -> None:
        cases = {
            "MOVED: a -> b": "moved",
            "DRY RUN: a -> b": "preview",
            "DEFER (still changing): a": "waiting",
            "SKIP (no rule): a": "left",
            "OK (already sorted): a": "already",
            "ERROR (move failed): a": "error",
            "Sorting root: /x": "info",
        }
        for text, level in cases.items():
            with self.subTest(text=text):
                self.assertEqual(classify(text), level)

    def test_moves_by_auto_sort_refresh_the_browser(self) -> None:
        changed = mock.Mock()
        service = SorterService(Path(tempfile.gettempdir()), on_change=changed)
        service._append("MOVED: a -> b", "moved", "watch")
        changed.assert_called_once()
        service._append("DRY RUN: a -> b", "preview", "preview")
        changed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
