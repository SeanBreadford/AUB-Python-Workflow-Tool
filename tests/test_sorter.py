from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import filesystem  # noqa: E402
from config import CONFIG_DIR_NAME, CONFIG_FILE_NAME  # noqa: E402
from filesystem import sort_project  # noqa: E402
from project import (  # noqa: E402
    ProjectConfigError,
    ProjectNotFoundError,
    init_project,
    load_project,
)
from rules import choose_destination  # noqa: E402

SHOT_05 = "production/Acts/Act_1/SQ_01/SC_05/SH05"

# filename -> expected destination folder (relative to the project root)
EXPECTED = {
    "SH05_layout_v003.ma": f"{SHOT_05}/layout",
    "SH05_anim_v001.ma": f"{SHOT_05}/layout",
    "SH05_set.blend": f"{SHOT_05}/3d",
    "SH05_anim_key.psd": f"{SHOT_05}/2d",
    "SH05_bg_paint.psd": SHOT_05,
    "SH05_fx2d_smoke.png": f"{SHOT_05}/fx",
    "SH05_comp_v01.nk": f"{SHOT_05}/comp",
    "SH05_render_v01.mov": f"{SHOT_05}/renders",
    "SH05_beauty_0001.exr": f"{SHOT_05}/renders",
    "SH05beauty_0001.exr": "production/shots/renders",
    "sh07_layout.ma": "production/Acts/Act_1/SQ_01/SC_07/SH07/layout",
    "SH20_layout.ma": "production/Acts/Act_2/SQ_02/SC_01/SH20/layout",
    "SH54_layout.ma": "production/Acts/Act_3/SQ_05/SC_12/SH54/layout",
    "SH55_layout.ma": "production/shots/SH55/layout",
    "ref_walk.mov": "reference",
    "edit.prproj": "editing/sessions",
    "music_theme_v3.wav": "editing/audio",
    "concept_char_hero.psd": "concept_art/characters",
    "concept_mask_design.psd": "concept_art/misc",
    "animfx_test_01.mov": "concept_art/Anim/2D/fx",
    "char_Princess_body.ma": "production/assets/3d/char/char_Princess/model",
    "prop_OilLamp_disp_001.png": "production/assets/3d/props/prop_OilLamp/textures",
    "prop_OilLamp_notes.txt": "production/assets/3d/props/prop_OilLamp",
    "env_Castle.blend": "production/assets/3d/env/env_Castle/model",
    "random.ma": "incoming",
}


def write_config(root: Path, text: str) -> None:
    path = root / CONFIG_DIR_NAME / CONFIG_FILE_NAME
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)


MINIMAL = """
[project]
name = "Test"
[shots]
dir = "{production}/{shot}"
unmapped_dir = "{production}/{shot}"
"""


class ProjectTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "production").mkdir()
        init_project(self.root)
        self.project = load_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def dest_folder(self, rel: str) -> str | None:
        dest = choose_destination(self.root / rel, self.project)
        return dest.parent.relative_to(self.root).as_posix() if dest else None

    def sort(self, **kwargs) -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            sort_project(self.root, **kwargs)
        return out.getvalue()


class RoutingTests(ProjectTestCase):
    def test_default_rules(self) -> None:
        for name, expected in EXPECTED.items():
            with self.subTest(name=name):
                self.assertEqual(self.dest_folder(name), expected)

    def test_fallback_only_applies_to_project_root(self) -> None:
        self.assertEqual(self.dest_folder("photo.jpg"), "incoming")
        self.assertIsNone(self.dest_folder("reference/photo.jpg"))
        self.assertIsNone(self.dest_folder("production/assets/notes.txt"))

    def test_matched_files_are_moved_from_anywhere(self) -> None:
        self.assertEqual(self.dest_folder("reference/SH05_set.blend"), f"{SHOT_05}/3d")

    def test_existing_folder_case_is_reused(self) -> None:
        existing = self.root / "production/assets/3d/props/PROP_oillamp"
        existing.mkdir(parents=True)
        self.assertEqual(
            self.dest_folder("prop_OilLamp.fbx"),
            "production/assets/3d/props/PROP_oillamp/model",
        )


class SortTests(ProjectTestCase):
    def test_moves_and_never_overwrites(self) -> None:
        (self.root / "SH05_set.blend").write_text("new")
        target = self.root / SHOT_05 / "3d"
        target.mkdir(parents=True)
        (target / "SH05_set.blend").write_text("old")

        self.sort(fast_mode=True)

        self.assertEqual((target / "SH05_set.blend").read_text(), "old")
        self.assertEqual((target / "SH05_set_001.blend").read_text(), "new")

    def test_dry_run_changes_nothing(self) -> None:
        (self.root / "SH05_set.blend").write_text("x")
        output = self.sort(dry_run=True)
        self.assertIn("DRY RUN: SH05_set.blend", output)
        self.assertTrue((self.root / "SH05_set.blend").exists())

    def test_ignored_files_and_dirs(self) -> None:
        for rel in ("_do_not_sort.png", "Thumbs.db", "autosave/SH05_a.ma", "Act_2/_old/SH05_b.ma"):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")

        # Pass files explicitly, as watch mode does, so the ignore check in
        # process_file is tested and not just the directory walk
        files = [p for p in self.root.rglob("*") if p.is_file() and CONFIG_DIR_NAME not in p.parts]
        output = self.sort(files=files, fast_mode=True)

        self.assertNotIn("MOVED", output)
        self.assertEqual(output.count("SKIP (ignored)"), 4)

    def test_project_found_from_subfolder(self) -> None:
        (self.root / "production/SH05_set.blend").write_text("x")
        out = io.StringIO()
        with redirect_stdout(out):
            sort_project(self.root / "production", fast_mode=True)
        self.assertTrue((self.root / SHOT_05 / "3d/SH05_set.blend").exists())


class CloudSafetyTests(ProjectTestCase):
    def test_file_still_changing_is_deferred_others_move(self) -> None:
        for name in ("SH05_set.blend", "SH06_set.blend", "SH07_set.blend"):
            (self.root / name).write_text("x")
        growing = self.root / "SH06_set.blend"

        def sync_writes_during_the_wait(_seconds: float) -> None:
            with growing.open("a") as handle:
                handle.write("more data arriving")

        with mock.patch.object(filesystem.time, "sleep", side_effect=sync_writes_during_the_wait) as sleep:
            output = self.sort()

        sleep.assert_called_once()  # one shared wait, not one per file
        self.assertIn("DEFER (still changing): SH06_set.blend", output)
        self.assertEqual(output.count("MOVED"), 2)
        self.assertTrue(growing.exists())

    def test_locked_file_is_deferred_not_an_error(self) -> None:
        (self.root / "SH05_set.blend").write_text("x")
        stats: list[str] = []
        filesystem.set_operation_callback(stats.append)
        self.addCleanup(filesystem.set_operation_callback, None)
        with mock.patch.object(filesystem, "MOVE_RETRY_DELAY", 0), \
                mock.patch.object(filesystem.shutil, "move", side_effect=PermissionError("locked")):
            output = self.sort(fast_mode=True)
        self.assertIn("DEFER (in use, will retry next sort): SH05_set.blend", output)
        self.assertNotIn("errors", stats)
        self.assertTrue((self.root / "SH05_set.blend").exists())

    def test_check_never_opens_files(self) -> None:
        # Opening an online-only OneDrive file can make it download
        path = self.root / "SH05_set.blend"
        path.write_text("x")
        with mock.patch.object(Path, "open", side_effect=AssertionError("file was opened")), \
                mock.patch("builtins.open", side_effect=AssertionError("file was opened")):
            stable, busy = filesystem.split_stable([path], delay=0)
        self.assertEqual((stable, busy), ([path], []))

    def test_dry_run_and_fast_mode_do_not_wait(self) -> None:
        (self.root / "SH05_set.blend").write_text("x")
        with mock.patch.object(filesystem.time, "sleep") as sleep:
            self.sort(dry_run=True)
            self.sort(fast_mode=True)
        sleep.assert_not_called()


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def assertConfigError(self, extra: str, message: str) -> None:
        write_config(self.root, MINIMAL + extra)
        with self.assertRaises(ProjectConfigError) as caught:
            load_project(self.root)
        self.assertIn(message, str(caught.exception))

    def test_missing_config(self) -> None:
        with self.assertRaises(ProjectNotFoundError):
            load_project(self.root)

    def test_init_never_overwrites(self) -> None:
        init_project(self.root)
        with self.assertRaises(FileExistsError):
            init_project(self.root)

    def test_typo_in_key(self) -> None:
        self.assertConfigError('[[rules]]\npattern = "x"\ndset = "a"\n', "unknown key(s): dset")

    def test_unknown_field_in_dest(self) -> None:
        self.assertConfigError('[[rules]]\npattern = "x"\ndest = "{asset}"\n', "unknown field(s) asset")

    def test_unknown_file_group(self) -> None:
        self.assertConfigError('[[rules]]\next = ["modle"]\ndest = "a"\n', "unknown file group 'modle'")

    def test_bad_regex(self) -> None:
        self.assertConfigError('[[rules]]\npattern = "("\ndest = "a"\n', "invalid regex")

    def test_dest_cannot_escape_project(self) -> None:
        self.assertConfigError('[[rules]]\npattern = "x"\ndest = "../elsewhere"\n', "inside the project")

    def test_overlapping_shot_ranges(self) -> None:
        ranges = (
            '[[shots.ranges]]\nact = "A"\nsequence = "S"\nfirst = 1\nlast = 5\n'
            '[[shots.ranges]]\nact = "A"\nsequence = "S"\nfirst = 5\nlast = 6\n'
        )
        self.assertConfigError(ranges, "SH05 is already in another range")


class CliTests(unittest.TestCase):
    def test_refuses_folder_without_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "app.py").write_text("x")
            result = subprocess.run(
                [sys.executable, str(REPO / "main.py"), "--root", tmp],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--init", result.stderr)
            self.assertTrue((Path(tmp) / "app.py").exists())


if __name__ == "__main__":
    unittest.main()
