from __future__ import annotations

import http.cookiejar
import os
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import tkinter as tk

    _probe = tk.Tk()
    _probe.destroy()
    HAVE_DISPLAY = True
except Exception:  # no tkinter or no display; run under xvfb-run for these
    HAVE_DISPLAY = False

from project import init_project  # noqa: E402


@unittest.skipUnless(HAVE_DISPLAY, "needs a display (run with xvfb-run)")
class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        import gui

        self.gui = gui
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"FILE_SORTER_CONFIG_HOME": str(self.tmp / "settings")})
        self._env.start()
        os.environ.pop("FILE_SORTER_ROOT", None)
        self.opened: list[str] = []
        self._browser = mock.patch.object(gui.webbrowser, "open", self.opened.append)
        self._browser.start()
        self.window = tk.Tk()
        self.launcher = gui.Launcher(self.window)

    def tearDown(self) -> None:
        self.launcher.shutdown()
        self.window.destroy()
        self._browser.stop()
        self._env.stop()
        self._tmp.cleanup()

    def page_loads(self, url: str) -> bool:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        return "Project Browser" in opener.open(url, timeout=5).read().decode()

    def test_open_a_project_and_remember_it(self) -> None:
        project = self.tmp / "TUP"
        project.mkdir()
        init_project(project)
        self.launcher.folder_var.set(str(project / "production"))  # a subfolder works too
        (project / "production").mkdir()
        self.launcher.open_project()

        self.assertEqual(len(self.opened), 1)
        self.assertTrue(self.page_loads(self.opened[0]))
        self.assertEqual(self.gui.load_settings()["project"], str(project.resolve()))
        # Next launch starts on the same folder
        again = self.gui.Launcher(tk.Toplevel(self.window))
        self.assertEqual(again.folder_var.get(), str(project.resolve()))

    def test_new_folder_is_set_up_after_asking(self) -> None:
        folder = self.tmp / "fresh"
        folder.mkdir()
        self.launcher.folder_var.set(str(folder))
        with mock.patch.object(self.gui.messagebox, "askyesno", return_value=False):
            self.launcher.open_project()
        self.assertFalse((folder / "_pipeline").exists())  # said no: nothing touched
        with mock.patch.object(self.gui.messagebox, "askyesno", return_value=True):
            self.launcher.open_project()
        self.assertTrue((folder / "_pipeline" / "project.toml").exists())
        self.assertEqual(len(self.opened), 1)

    def test_try_the_demo(self) -> None:
        with mock.patch.object(self.gui, "DEMO_FOLDER", self.tmp / "TUP Demo"):
            self.launcher.open_demo()
            self.assertTrue((self.tmp / "TUP Demo" / "_pipeline").is_dir())
            self.launcher.open_demo()  # second time just opens it again
        self.assertEqual(len(self.opened), 2)

    def test_closing_stops_the_server(self) -> None:
        with mock.patch.object(self.gui, "DEMO_FOLDER", self.tmp / "TUP Demo"):
            self.launcher.open_demo()
        url = self.opened[0]
        self.launcher.shutdown()
        with self.assertRaises(OSError):
            urllib.request.urlopen(url, timeout=2)


if __name__ == "__main__":
    unittest.main()
