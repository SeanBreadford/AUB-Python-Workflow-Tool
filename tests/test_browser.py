from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.destroy()
    HAVE_DISPLAY = True
except Exception:  # no tkinter, or no display (run under xvfb-run for these)
    HAVE_DISPLAY = False

from filesystem import sort_project  # noqa: E402
from project import init_project, load_project  # noqa: E402
from tracking import build_tracker, events_dir, record_event  # noqa: E402
from versions import scan_versions  # noqa: E402

FILES = [
    "SH05_layout_v001.ma",
    "SH05_layout_v002.ma",
    "SH05_set_v001.blend",
    "SH06_layout_v001.ma",
    "SH99_test_v001.ma",
    "prop_OilLamp_v001.fbx",
]


@unittest.skipUnless(HAVE_DISPLAY, "needs a display (run with xvfb-run)")
class BrowserTests(unittest.TestCase):
    def setUp(self) -> None:
        import browser
        import gui

        self.browser_module = browser
        self._tmp = tempfile.TemporaryDirectory()
        self.root_dir = Path(self._tmp.name).resolve()
        init_project(self.root_dir)
        for name in FILES:
            (self.root_dir / name).write_text("x")
        with redirect_stdout(io.StringIO()):
            sort_project(self.root_dir, fast_mode=True)
        self.project = load_project(self.root_dir)
        self.record("status", "SH05", "layout", user="alex", status="wfa", note="Ready for review")

        self.tk = tk.Tk()
        self.app = gui.FileSorterGUI(self.tk)
        self.app.root_var.set(str(self.root_dir))
        self.browser = self.app.browser
        self.app.notebook.select(self.browser)
        self.wait_for(lambda: self.browser.tracker is not None and not self.browser.loading)

    def tearDown(self) -> None:
        self.app.shutdown()
        self.tk.destroy()
        self._tmp.cleanup()

    def record(self, *args, **kwargs) -> None:
        tracker = build_tracker(self.project, scan_versions(self.project))
        record_event(tracker, *args, **kwargs)

    def pump(self, seconds: float = 0.2) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.tk.update()
            time.sleep(0.02)

    def wait_for(self, condition, timeout: float = 10.0) -> None:
        end = time.time() + timeout
        while not condition():
            if time.time() > end:
                self.fail("timed out waiting for the browser")
            self.pump(0.05)

    def select(self, entity: str, task: str | None = None) -> None:
        self.browser.entity_tree.selection_set(f"e:{entity}")
        self.pump()
        if task:
            self.browser.task_tree.selection_set(f"t:{task}")
            self.pump()

    def leaves(self, parent: str = "") -> list[str]:
        tree, found = self.browser.entity_tree, []
        for child in tree.get_children(parent):
            found += [child] if child.startswith("e:") else self.leaves(child)
        return found

    def test_tree_groups_shots_by_act_and_sequence(self) -> None:
        tree = self.browser.entity_tree
        self.assertEqual(tree.parent("e:SH05"), "g:shot:Act_1:SQ_01")
        self.assertEqual(tree.parent("e:SH99"), "g:shot:unassigned")
        self.assertEqual(tree.parent("e:prop_OilLamp"), "g:prop")
        self.assertIn("feedback_request", tree.item("e:SH05", "tags"))

    def test_selecting_shows_tasks_versions_and_notes(self) -> None:
        self.select("SH05", "layout")
        layout = self.browser.task_tree.item("t:layout", "values")
        self.assertEqual(list(layout[:4]), ["layout", "wfa", "-", "v002"])
        versions = self.browser.version_tree
        (product,) = versions.get_children()
        self.assertEqual(versions.item(product, "values")[:2], ("SH05_layout.ma", "v002"))
        self.assertEqual(len(versions.get_children(product)), 2)
        self.assertIn("Ready for review", self.browser.notes.get("1.0", "end"))

    def test_save_status_with_comment_runs_automation(self) -> None:
        self.select("SH05", "layout")
        self.browser.user_var.set("priya")
        self.browser.status_choice.set("done - Done")
        self.browser.assignee_var.set("alex")
        self.browser.comment.insert("1.0", "Approved")
        self.browser.save()

        tracker = build_tracker(self.project)
        state = tracker.task("SH05", "layout")
        self.assertEqual((state.status, state.assignee), ("done", "alex"))
        self.assertEqual((state.notes[-1].user, state.notes[-1].text), ("priya", "Approved"))
        self.assertEqual(tracker.task("SH05", "3d").status, "ready")
        self.assertEqual(self.browser.task_tree.item("t:3d", "values")[1], "ready")
        self.assertEqual(self.browser.comment.get("1.0", "end").strip(), "")

    def test_comment_alone_is_a_note(self) -> None:
        self.select("SH06", "layout")
        self.browser.comment.insert("1.0", "Horizon tilts")
        self.browser.save()
        state = build_tracker(self.project).task("SH06", "layout")
        self.assertEqual(state.status, "todo")
        self.assertEqual(state.notes[-1].text, "Horizon tilts")

    def test_save_needs_a_user_name(self) -> None:
        self.select("SH06", "layout")
        self.browser.user_var.set("")
        self.browser.status_choice.set("wip - Work in progress")
        with mock.patch.object(self.browser_module.messagebox, "showerror") as error:
            self.browser.save()
        error.assert_called_once()
        self.assertEqual(build_tracker(self.project).task("SH06", "layout").status, "todo")

    def test_picks_up_changes_synced_from_other_machines(self) -> None:
        event = {"id": uuid.uuid4().hex, "time": "2099-01-01T00:00:00.000+00:00", "user": "sam",
                 "type": "status", "entity": "SH06", "entity_type": "shot", "task": "layout",
                 "status": "retake"}
        (events_dir(self.project) / "sam@laptop.jsonl").write_text(json.dumps(event) + "\n")
        self.browser._poll_events()
        self.assertEqual(self.browser.tracker.task("SH06", "layout").status, "retake")
        self.assertIn("retake", self.browser.entity_tree.item("e:SH06", "tags"))

    def test_filter(self) -> None:
        self.browser.filter_var.set("oil")
        self.pump()
        self.assertEqual(self.leaves(), ["e:prop_OilLamp"])
        self.browser.filter_var.set("")
        self.pump()
        self.assertGreater(len(self.leaves()), 50)

    def test_open_version_folder(self) -> None:
        self.select("SH05", "layout")
        tree = self.browser.version_tree
        tree.selection_set(tree.get_children()[0])
        with mock.patch.object(self.browser_module, "open_in_file_browser") as opener:
            self.browser._on_version_open()
        opened = opener.call_args.args[0]
        self.assertEqual(opened.name, "SH05_layout_v002.ma")

    def test_sort_marks_browser_for_rescan(self) -> None:
        self.app._finish_sort_ui("Sort complete.")
        self.assertIsNone(self.browser.loaded_root)


if __name__ == "__main__":
    unittest.main()
