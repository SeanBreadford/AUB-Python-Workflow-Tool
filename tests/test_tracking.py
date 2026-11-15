from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from filesystem import sort_project  # noqa: E402
from project import ProjectConfigError, init_project, load_project  # noqa: E402
from tracking import (  # noqa: E402
    TrackingError,
    build_tracker,
    events_dir,
    format_entity,
    format_status_grid,
    record_event,
)
from versions import scan_versions  # noqa: E402
from test_sorter import MINIMAL, write_config  # noqa: E402

STATUSES = """
[[statuses]]
code = "todo"
default = true
[[statuses]]
code = "wip"
[[statuses]]
code = "done"
done = true
"""


class TrackingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        init_project(self.root)
        self.project = load_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def tracker(self):
        return build_tracker(self.project, scan_versions(self.project))

    def record(self, *args, **kwargs) -> dict:
        return record_event(self.tracker(), *args, **kwargs)

    def write_foreign(self, filename: str, events: list[dict], tail: str = "") -> None:
        """Simulate an events file synced in from another machine."""
        folder = events_dir(self.project)
        folder.mkdir(parents=True, exist_ok=True)
        lines = "".join(json.dumps(event) + "\n" for event in events)
        (folder / filename).write_text(lines + tail)

    def event(self, time: str, **data) -> dict:
        return {"id": uuid.uuid4().hex, "time": time, "user": "other", "entity": "SH05",
                "entity_type": "shot", "task": "layout", **data}


class StateTests(TrackingTestCase):
    def test_tasks_start_at_default_status(self) -> None:
        tracker = self.tracker()
        self.assertEqual([s.status for s in tracker.tasks_for("SH01")], ["todo"] * 6)

    def test_status_note_and_assign(self) -> None:
        self.record("status", "sh05", "layout", user="alex", status="WIP", note="started")
        self.record("assign", "SH05", "layout", user="priya", assignee="alex")
        self.record("note", "SH05", "layout", user="priya", text="check f40")

        state = self.tracker().task("SH05", "layout")
        self.assertEqual(state.status, "wip")
        self.assertEqual(state.assignee, "alex")
        self.assertEqual([(n.user, n.text) for n in state.notes], [("alex", "started"), ("priya", "check f40")])

    def test_each_user_writes_their_own_file(self) -> None:
        self.record("status", "SH05", "layout", user="alex", status="wip")
        self.record("status", "SH06", "layout", user="priya", status="wip")
        names = sorted(p.name.split("@")[0] for p in events_dir(self.project).iterdir())
        self.assertEqual(names, ["alex", "priya"])

    def test_latest_event_wins_across_machines(self) -> None:
        self.write_foreign("a@pc1.jsonl", [self.event("2026-01-02T00:00:00.000+00:00", type="status", status="done")])
        self.write_foreign("b@pc2.jsonl", [self.event("2026-01-01T00:00:00.000+00:00", type="status", status="wip")])
        self.assertEqual(self.tracker().task("SH05", "layout").status, "done")

    def test_conflicted_copies_and_partial_lines(self) -> None:
        event = self.event("2026-01-01T00:00:00.000+00:00", type="note", text="hello")
        self.write_foreign("a@pc1.jsonl", [event], tail='{"id": "trunc')
        self.write_foreign("a@pc1 (conflicted copy).jsonl", [event])

        tracker = self.tracker()
        self.assertEqual(len(tracker.task("SH05", "layout").notes), 1)
        self.assertEqual(tracker.unreadable_lines, 1)
        self.assertIn("skipped 1 unreadable", format_status_grid(tracker))


class AutomationTests(TrackingTestCase):
    def test_automation_moves_tasks_that_have_not_started(self) -> None:
        self.record("status", "SH05", "2d", status="wip")
        self.record("status", "SH05", "layout", status="done")

        tracker = self.tracker()
        self.assertEqual(tracker.task("SH05", "3d").status, "ready")
        self.assertIn("(automation)", tracker.task("SH05", "3d").updated_by)
        # 2d was already in progress, so the automation leaves it alone
        self.assertEqual(tracker.task("SH05", "2d").status, "wip")

    def test_automation_is_limited_to_its_entity_type(self) -> None:
        (self.root / "char_Princess_v001.ma").write_text("x")
        with redirect_stdout(io.StringIO()):
            sort_project(self.root, fast_mode=True)
        # The model -> textures automation in the default config is for props only
        self.record("status", "char_Princess", "model", status="done")
        self.assertEqual(self.tracker().task("char_Princess", "textures").status, "todo")


class ValidationTests(TrackingTestCase):
    def test_rejects_unknown_status_task_and_entity(self) -> None:
        with self.assertRaisesRegex(TrackingError, "Unknown status"):
            self.record("status", "SH05", "layout", status="finished")
        with self.assertRaisesRegex(TrackingError, "not a shot task"):
            self.record("status", "SH05", "rigging", status="wip")
        with self.assertRaisesRegex(TrackingError, "Unknown shot or asset"):
            self.record("status", "prop_Mask", "model", status="wip")
        self.assertFalse(events_dir(self.project).exists())

    def test_new_asset_with_entity_type(self) -> None:
        self.record("status", "prop_Mask", "model", status="wip", entity_type="prop")
        tracker = self.tracker()
        self.assertEqual(tracker.find("prop_mask").entity_type, "prop")
        self.assertIn("model: wip", format_entity(tracker, None, "prop_Mask"))


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def assertConfigError(self, extra: str, message: str) -> None:
        write_config(self.root, MINIMAL + extra)
        with self.assertRaises(ProjectConfigError) as caught:
            load_project(self.root)
        self.assertIn(message, str(caught.exception))

    def test_needs_exactly_one_default(self) -> None:
        self.assertConfigError('[[statuses]]\ncode = "a"\n[[statuses]]\ncode = "b"\n', "exactly one")

    def test_duplicate_status(self) -> None:
        self.assertConfigError(STATUSES + '[[statuses]]\ncode = "wip"\n', "duplicate status code")

    def test_automation_unknown_status(self) -> None:
        self.assertConfigError(
            STATUSES + '[[automations]]\ntask = "a"\nstatus = "done"\nthen_task = "b"\nthen_status = "nope"\n',
            "unknown status 'nope'",
        )

    def test_automation_unknown_task(self) -> None:
        self.assertConfigError(
            STATUSES + '[task_types]\nshot = ["layout"]\n'
            '[[automations]]\nentity_type = "shot"\ntask = "layout"\nstatus = "done"\n'
            'then_task = "comp"\nthen_status = "wip"\n',
            "'comp' is not a shot task",
        )

    def test_tracking_off_without_statuses(self) -> None:
        write_config(self.root, MINIMAL)
        with self.assertRaisesRegex(TrackingError, "tracking is off"):
            build_tracker(load_project(self.root))


if __name__ == "__main__":
    unittest.main()
