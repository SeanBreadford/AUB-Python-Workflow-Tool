from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from filesystem import sort_project  # noqa: E402
from project import ProjectConfigError, init_project, load_project  # noqa: E402
from versions import format_report, parse_name, scan_versions  # noqa: E402
from test_sorter import MINIMAL, write_config  # noqa: E402


class ParseNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        init_project(root)
        self.project = load_project(root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_names(self) -> None:
        cases = {
            "SH05_layout_v003.ma": ("SH05_layout.ma", 3, None),
            "layout.v027.ma": ("layout.ma", 27, None),
            "music_theme_v3.wav": ("music_theme.wav", 3, None),
            "SH05_comp_v01_final.nk": ("SH05_comp_final.nk", 1, None),
            "SH05_beauty_v002_0001.exr": ("SH05_beauty_####.exr", 2, 1),
            "SH05_beauty.000120.exr": ("SH05_beauty.######.exr", None, 120),
            "SH05_set.blend": ("SH05_set.blend", None, None),
            "SH05_vfx_v2.ma": ("SH05_vfx.ma", 2, None),
            "prop_OilLamp_disp_001.png": ("prop_OilLamp_disp_001.png", None, None),
            "README": ("README", None, None),
        }
        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                parsed = parse_name(filename, self.project)
                self.assertEqual((parsed.name, parsed.version, parsed.frame), expected)


class ScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        init_project(self.root)
        self.project = load_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def add(self, *names: str) -> None:
        for name in names:
            (self.root / name).write_text(name)

    def sort(self) -> None:
        with redirect_stdout(io.StringIO()):
            sort_project(self.root, fast_mode=True)

    def test_versions_are_grouped_by_entity_task_and_name(self) -> None:
        self.add("SH05_layout_v001.ma", "SH05_layout_v002.ma", "SH05_set.blend", "concept_char_hero.psd")
        self.add(*(f"SH05_beauty_v001_{frame:04d}.exr" for frame in range(1, 11)))
        self.sort()

        index = scan_versions(self.project)
        shot = index.find("SH05")
        self.assertIsNotNone(shot)
        self.assertEqual(shot.context, "Act_1 / SQ_01 / SC_05")

        products = {(p.task, p.name): p for p in shot.products.values()}
        layout = products[("layout", "SH05_layout.ma")]
        self.assertEqual([v.number for v in layout.history()], [1, 2])
        self.assertEqual(layout.latest.number, 2)

        renders = products[("renders", "SH05_beauty_####.exr")]
        self.assertTrue(renders.latest.is_sequence)
        self.assertEqual(len(renders.latest.files), 10)

        # Concept art has no entity in the default config, so it isn't tracked
        self.assertEqual([e.name for e in index.entities], ["SH05"])

    def test_asset_case_variants_are_one_entity(self) -> None:
        self.add("prop_OilLamp_v001.fbx", "PROP_oillamp_v002.fbx")
        self.sort()
        index = scan_versions(self.project)
        self.assertEqual(len(index.entities), 1)
        (product,) = index.entities[0].products.values()
        self.assertEqual(product.latest.number, 2)

    def test_unsorted_files_are_reported_not_tracked(self) -> None:
        self.add("SH05_layout_v001.ma")
        self.sort()
        self.add("SH05_layout_v002.ma")

        index = scan_versions(self.project)
        (product,) = index.find("SH05").products.values()
        self.assertEqual(product.latest.number, 1)
        self.assertEqual([p.name for p in index.unsorted], ["SH05_layout_v002.ma"])
        self.assertIn("1 tracked file(s) not sorted yet", format_report(index))

    def test_latest_is_highest_version_not_newest_file(self) -> None:
        self.add("SH05_layout_v003.ma", "SH05_layout_v010.ma")
        self.sort()
        v003 = next(self.root.rglob("SH05_layout_v003.ma"))
        os.utime(v003, (2_000_000_000, 2_000_000_000))  # touched later
        (product,) = scan_versions(self.project).find("SH05").products.values()
        self.assertEqual(product.latest.number, 10)


class TrackingConfigTests(unittest.TestCase):
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

    def test_entity_unknown_field(self) -> None:
        self.assertConfigError(
            '[[rules]]\npattern = "x"\ndest = "a"\nentity = "{asset}"\n',
            "entity uses unknown field(s) asset",
        )

    def test_task_needs_entity(self) -> None:
        self.assertConfigError('[[rules]]\npattern = "x"\ndest = "a"\ntask = "comp"\n', "need an entity")

    def test_version_pattern_needs_group(self) -> None:
        self.assertConfigError("[versions]\npattern = 'v\\d+'\n", "(?P<version>...)")

    def test_cases_inherit_and_override_tracking(self) -> None:
        write_config(
            self.root,
            MINIMAL
            + '[[rules]]\npattern = "(?P<shot>sh\\\\d+)_"\ndest = "{shot_dir}"\n'
            + 'entity = "{shot}"\ntask = "misc"\n'
            + '    [[rules.cases]]\n    ext = [".nk"]\n    dest = "comp"\n    task = "comp"\n'
            + "    [[rules.cases]]\n",
        )
        comp, default = load_project(self.root).rules
        self.assertEqual((comp.entity, comp.task), ("{shot}", "comp"))
        self.assertEqual((default.entity, default.task), ("{shot}", "misc"))


if __name__ == "__main__":
    unittest.main()
