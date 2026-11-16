from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from filesystem import sort_project  # noqa: E402
from project import ProjectConfigError, init_project, load_project  # noqa: E402
from tracking import build_tracker, record_event  # noqa: E402
from versions import scan_versions  # noqa: E402
from test_sorter import MINIMAL, write_config  # noqa: E402

LAYOUT = "production/Acts/Act_1/SQ_01/SC_05/SH05/layout"


class HealthTestCase(unittest.TestCase):
    """Files and events with controlled times: the past, a decision now, the future."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        init_project(self.root)
        self.project = load_project(self.root)
        self.now = time.time()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def save(self, name: str, minutes_from_now: float = -60) -> None:
        """An artist saves a file (sorted straight away), timed relative to now."""
        (self.root / name).write_text(name)
        with redirect_stdout(io.StringIO()):
            sort_project(self.root, fast_mode=True)
        moved = next(self.root.rglob(name))
        stamp = self.now + minutes_from_now * 60
        os.utime(moved, (stamp, stamp))

    def tracker(self):
        return build_tracker(self.project, scan_versions(self.project))

    def record(self, *args, **kwargs) -> None:
        record_event(self.tracker(), *args, **kwargs)

    def flags(self, entity: str = "SH05", task: str = "layout", now: datetime | None = None) -> dict[str, str]:
        tracker = self.tracker()
        return {f.code: f.message for f in tracker.flags(tracker.task(entity, task), now)}


class AutoStatusTests(HealthTestCase):
    def test_first_file_starts_the_task(self) -> None:
        self.save("SH05_layout_v001.ma")
        tracker = self.tracker()
        state = tracker.task("SH05", "layout")
        self.assertEqual((state.status, state.updated_by), ("wip", "files"))
        kinds = [(a["kind"], a.get("status"), a.get("auto")) for a in tracker.activity if a["entity"] == "SH05"]
        self.assertEqual(kinds, [("file", None, None), ("status", "wip", True)])

    def test_files_never_undo_a_decision(self) -> None:
        self.save("SH05_layout_v001.ma", -120)
        self.record("status", "SH05", "layout", user="priya", status="wfa")
        self.save("SH05_layout_v002.ma", +5)
        self.assertEqual(self.tracker().task("SH05", "layout").status, "wfa")

    def test_new_version_after_retake_goes_back_to_wip(self) -> None:
        self.save("SH05_layout_v001.ma", -120)
        self.record("status", "SH05", "layout", user="priya", status="retake", note="Flip the camera")
        self.assertEqual(self.tracker().task("SH05", "layout").status, "retake")
        self.save("SH05_layout_v002.ma", +5)
        self.assertEqual(self.tracker().task("SH05", "layout").status, "wip")

    def test_off_without_config(self) -> None:
        write_config(self.root, MINIMAL + '[[statuses]]\ncode = "todo"\ndefault = true\n'
                     '[[rules]]\npattern = "(?P<shot>sh\\\\d+)_"\ndest = "{shot_dir}"\nentity = "{shot}"\ntask = "layout"\n')
        self.project = load_project(self.root)
        self.save("SH05_layout_v001.ma")
        self.assertEqual(self.tracker().task("SH05", "layout").status, "todo")


class VersionPinningTests(HealthTestCase):
    def test_approval_remembers_the_version(self) -> None:
        self.save("SH05_layout_v001.ma", -120)
        self.save("SH05_layout_v002.ma", -60)
        self.record("status", "SH05", "layout", user="priya", status="done")
        self.assertEqual(self.tracker().task("SH05", "layout").decided_on, {"SH05_layout.ma": 2})
        self.assertEqual(self.flags(), {})

    def test_changed_after_approval(self) -> None:
        self.save("SH05_layout_v002.ma", -60)
        self.record("status", "SH05", "layout", user="priya", status="done")
        self.save("SH05_layout_v003.ma", +5)
        flags = self.flags()
        self.assertIn("changed_after_approval", flags)
        self.assertIn("SH05_layout.ma v003 (decision was on v002)", flags["changed_after_approval"])

    def test_newer_than_submitted_for_review(self) -> None:
        self.save("SH05_layout_v002.ma", -60)
        self.record("status", "SH05", "layout", user="alex", status="wfa")
        self.save("SH05_layout_v003.ma", +5)
        self.assertIn("newer_than_submitted", self.flags())

    def test_version_clash(self) -> None:
        self.save("SH05_layout_v004.ma", -60)
        self.save("SH05_layout_v004.ma", -30)  # a second person saves "v004" too; the sorter renames it
        self.assertTrue((self.root / LAYOUT / "SH05_layout_v004_001.ma").exists())
        flags = self.flags()
        self.assertIn("2 files claim SH05_layout.ma v004", flags["conflict"])
        products = self.tracker().task("SH05", "layout").latest
        self.assertEqual(products, {"SH05_layout.ma": 4})  # not a separate "SH05_layout_001.ma"


class StalledTests(HealthTestCase):
    def test_active_task_goes_stale(self) -> None:
        self.save("SH05_layout_v001.ma", -60)
        in_ten_days = datetime.now(timezone.utc) + timedelta(days=10)
        self.assertEqual(self.flags(now=datetime.now(timezone.utc)), {})
        self.assertIn("No new file or update for 10 days", self.flags(now=in_ten_days)["stalled"])

    def test_retake_without_new_version(self) -> None:
        self.save("SH05_layout_v001.ma", -60)
        self.record("status", "SH05", "layout", user="priya", status="retake")
        in_eight_days = datetime.now(timezone.utc) + timedelta(days=8)
        self.assertIn("Retake noted 8 days ago", self.flags(now=in_eight_days)["stalled"])

    def test_done_and_untouched_tasks_never_stall(self) -> None:
        self.save("SH05_layout_v001.ma", -60)
        self.record("status", "SH05", "layout", user="priya", status="done")
        later = datetime.now(timezone.utc) + timedelta(days=60)
        self.assertNotIn("stalled", self.flags(now=later))
        self.assertEqual(self.flags("SH06", "layout", now=later), {})


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def assertConfigError(self, extra: str, message: str) -> None:
        statuses = '[[statuses]]\ncode = "todo"\ndefault = true\n[[statuses]]\ncode = "wip"\n'
        write_config(self.root, MINIMAL + statuses + extra)
        with self.assertRaises(ProjectConfigError) as caught:
            load_project(self.root)
        self.assertIn(message, str(caught.exception))

    def test_unknown_status(self) -> None:
        self.assertConfigError('[auto_status]\nstart_from = ["todo"]\nstarted = "doing"\n', "unknown status 'doing'")

    def test_started_needs_start_from(self) -> None:
        self.assertConfigError('[auto_status]\nstarted = "wip"\n', "needs 'start_from'")

    def test_stale_days(self) -> None:
        self.assertConfigError("[health]\nstale_days = 0\n", "at least 1")


if __name__ == "__main__":
    unittest.main()


class TeamTests(HealthTestCase):
    """Names in file names credit the artist (only for people on the team)."""

    def setUp(self) -> None:
        super().setUp()
        config = self.root / "_pipeline" / "project.toml"
        config.write_text(config.read_text() + '\n[[team]]\nname = "sam"\naliases = ["SB"]\n'
                          '\n[[team]]\nname = "alex"\n')
        self.project = load_project(self.root)

    def test_first_file_assigns_its_artist(self) -> None:
        self.save("SH05_layout_v001_SB.ma")
        tracker = self.tracker()
        self.assertEqual(tracker.task("SH05", "layout").assignee, "sam")
        files = [a for a in tracker.activity if a["kind"] == "file"]
        self.assertEqual(files[0]["artists"], ["sam"])

    def test_files_never_undo_an_assignment(self) -> None:
        self.save("SH05_layout_v001_sam.ma", -120)
        self.record("assign", "SH05", "layout", user="priya", assignee=None)  # deliberately unassigned
        self.save("SH05_layout_v002_alex.ma", +5)
        self.assertIsNone(self.tracker().task("SH05", "layout").assignee)

    def test_two_artists_on_one_version_is_a_clash(self) -> None:
        self.save("SH05_layout_v004_sam.ma", -60)
        self.save("SH05_layout_v004_alex.ma", -30)
        flags = self.flags()
        self.assertIn("2 files claim SH05_layout.ma v004 (alex and sam)", flags["conflict"])

    def test_stalled_names_the_last_artist(self) -> None:
        self.save("SH05_layout_v001_sam.ma", -60)
        later = datetime.now(timezone.utc) + timedelta(days=10)
        self.assertIn("(last file from sam)", self.flags(now=later)["stalled"])

    def test_unknown_names_are_not_credited(self) -> None:
        self.save("SH05_layout_v001_final.ma")
        tracker = self.tracker()
        self.assertIsNone(tracker.task("SH05", "layout").assignee)
        self.assertEqual(list(tracker.task("SH05", "layout").latest), ["SH05_layout_final.ma"])

    def test_team_config_is_checked(self) -> None:
        config = self.root / "_pipeline" / "project.toml"
        original = config.read_text()
        for extra, message in (('\n[[team]]\nname = "Sam"\n', "already used by sam"),
                               ('\n[[team]]\nname = "s.b"\n', "letters and digits only")):
            with self.subTest(extra=extra):
                config.write_text(original + extra)
                with self.assertRaises(ProjectConfigError) as caught:
                    load_project(self.root)
                self.assertIn(message, str(caught.exception))
