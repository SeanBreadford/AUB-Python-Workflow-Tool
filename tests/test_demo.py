from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo import create_demo  # noqa: E402
from project import load_project  # noqa: E402
from tracking import build_tracker  # noqa: E402
from versions import scan_versions  # noqa: E402


class DemoTests(unittest.TestCase):
    """End to end: each demo shot must show the problem it's there to show."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = create_demo(Path(cls._tmp.name) / "demo", you="robin")
        cls.project = load_project(cls.root)
        cls.tracker = build_tracker(cls.project, scan_versions(cls.project))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def layout(self, shot: str):
        return self.tracker.task(shot, "layout")

    def flag_codes(self, shot: str) -> list[str]:
        return [f.code for f in self.tracker.flags(self.layout(shot))]

    def test_each_shot_shows_its_problem(self) -> None:
        self.assertEqual(self.flag_codes("SH01"), ["changed_after_approval"])
        self.assertEqual(self.flag_codes("SH02"), ["newer_than_submitted"])
        self.assertEqual(self.flag_codes("SH03"), ["stalled"])
        self.assertEqual(self.flag_codes("SH04"), ["conflict"])

    def test_statuses_that_moved_by_themselves(self) -> None:
        self.assertEqual((self.layout("SH06").status, self.layout("SH06").updated_by), ("wip", "files"))
        self.assertEqual(self.layout("SH06").assignee, "sam")  # assigned by saving its first file
        self.assertEqual((self.layout("SH07").status, self.layout("SH07").updated_by), ("wip", "files"))

    def test_feedback_waiting_for_you(self) -> None:
        sh05 = self.layout("SH05")
        self.assertEqual((sh05.status, sh05.assignee), ("retake", "robin"))
        self.assertEqual([n.user for n in sh05.notes], ["robin", "priya", "alex"])

    def test_no_noise(self) -> None:
        # Only the four deliberate problems; a demo full of stale warnings teaches people to ignore them
        flagged = {(state.entity, state.task) for state, _ in self.tracker.all_flags()}
        self.assertEqual(flagged, {("SH01", "layout"), ("SH02", "layout"), ("SH03", "layout"), ("SH04", "layout")})

    def test_has_thumbnails_and_assets(self) -> None:
        index = scan_versions(self.project)
        self.assertEqual(index.unsorted, [])
        names = {e.name for e in index.entities}
        self.assertTrue({"SH01", "SH07", "prop_OilLamp", "char_Princess", "env_Castle"} <= names)

    def test_refuses_a_folder_with_files(self) -> None:
        with self.assertRaises(FileExistsError):
            create_demo(self.root, you="robin")


if __name__ == "__main__":
    unittest.main()
