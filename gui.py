from __future__ import annotations

import json
import os
import sys
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

from project import ProjectConfigError, ProjectNotFoundError, init_project, load_project
from theme import COLORS, FONT
from webapp import BrowserServer

# The desktop app is a small launcher: pick the project folder, open the
# project browser (where sorting, auto-sort and tracking all live), and keep
# this window open while you work. Closing it stops everything.

DEMO_FOLDER = Path.home() / "TUP Demo"


# Settings
def settings_path() -> Path:
    """Per-computer settings (the last project folder)."""
    override = os.getenv("FILE_SORTER_CONFIG_HOME")
    if override:
        base = Path(override)
    elif sys.platform.startswith("win"):
        base = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming")) / "TUPFileSorter"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "TUPFileSorter"
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "tup-file-sorter"
    return base / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError:
        pass  # not remembering the folder is an inconvenience, not an error


# Launcher
class Launcher:
    def __init__(self, window: tk.Tk) -> None:
        self.window = window
        self.server: BrowserServer | None = None
        window.title("TUP Project Browser")
        window.geometry("640x340")
        window.minsize(520, 300)
        window.configure(bg=COLORS["bg"])
        window.option_add("*highlightThickness", 0)  # Tk 9 draws light focus outlines otherwise

        remembered = load_settings().get("project", "")
        self.folder_var = tk.StringVar(value=os.getenv("FILE_SORTER_ROOT", remembered))
        self.status_var = tk.StringVar(value="" if remembered else "Choose your team's project folder to begin.")
        self._build()

    def _label(self, parent, text="", size=10, muted=False, bold=False, **kwargs) -> tk.Label:
        return tk.Label(parent, text=text, bg=kwargs.pop("bg", COLORS["bg"]),
                        fg=COLORS["muted"] if muted else COLORS["text"],
                        font=(FONT, size, "bold" if bold else "normal"), anchor="w", justify="left", **kwargs)

    def _button(self, parent, text, command, primary=False) -> tk.Button:
        return tk.Button(parent, text=text, command=command, relief="flat", cursor="hand2",
                         bg=COLORS["accent"] if primary else "#444", fg="white",
                         activebackground=COLORS["accent"], activeforeground="white",
                         padx=16, pady=10 if primary else 7, font=(FONT, 11 if primary else 10))

    def _build(self) -> None:
        frame = tk.Frame(self.window, bg=COLORS["bg"])
        frame.pack(fill="both", expand=True, padx=22, pady=18)

        self._label(frame, "TUP Project Browser", size=18, bold=True).pack(fill="x")
        self._label(frame, "Pick your team's project folder and open the project browser. Sorting, "
                    "auto-sort and tracking are all in there. Keep this window open while you work.",
                    muted=True, wraplength=580).pack(fill="x", pady=(4, 14))

        self._label(frame, "Project folder", bold=True).pack(fill="x")
        row = tk.Frame(frame, bg=COLORS["bg"])
        row.pack(fill="x", pady=(4, 14))
        tk.Entry(row, textvariable=self.folder_var, bg=COLORS["input"], fg=COLORS["text"],
                 insertbackground=COLORS["text"], relief="flat", bd=8, font=(FONT, 10)).pack(
            side="left", fill="x", expand=True)
        self._button(row, "Choose…", self.choose_folder).pack(side="left", padx=(8, 0))

        actions = tk.Frame(frame, bg=COLORS["bg"])
        actions.pack(fill="x")
        self.open_button = self._button(actions, "Open Project Browser", self.open_project, primary=True)
        self.open_button.pack(side="left")
        self._button(actions, "Try the demo", self.open_demo).pack(side="left", padx=(10, 0))

        self._label(frame, textvariable=self.status_var, muted=True, wraplength=580).pack(fill="x", pady=(14, 0))

    # Actions
    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(title="Choose your team's project folder")
        if folder:
            self.folder_var.set(folder)

    def open_project(self) -> None:
        text = self.folder_var.get().strip()
        if not text:
            messagebox.showinfo("Choose a folder", "Choose your team's project folder first.")
            return
        folder = Path(text).expanduser().resolve()
        if not folder.is_dir():
            messagebox.showerror("Folder not found", f"This folder doesn't exist:\n{folder}")
            return
        try:
            project = load_project(folder)
        except ProjectConfigError as error:
            messagebox.showerror("The project's settings have a problem", str(error))
            return
        except ProjectNotFoundError:
            if not messagebox.askyesno(
                "Set up this folder?",
                f"This folder isn't set up as a project yet:\n{folder}\n\n"
                "Set it up with the standard TUP structure? This adds a _pipeline folder with the "
                "project's settings. Nothing else is changed.",
            ):
                return
            init_project(folder)
            project = load_project(folder)
        self._serve(project.root)

    def open_demo(self) -> None:
        from demo import create_demo
        from tracking import current_user

        folder = DEMO_FOLDER
        if not (folder / "_pipeline").is_dir():
            if folder.exists() and any(folder.iterdir()):
                messagebox.showerror("Can't create the demo",
                                     f"{folder} already has other files in it, so the demo wasn't created there.")
                return
            create_demo(folder, current_user())
        self.folder_var.set(str(folder))
        self._serve(folder)

    def _serve(self, root: Path) -> None:
        if self.server is None or self.server.state.root != root:
            self._stop_server()
            self.server = BrowserServer(root)
            self.server.start()
        save_settings({**load_settings(), "project": str(root)})
        webbrowser.open(self.server.url)
        self.open_button.configure(text="Open again")
        self.status_var.set(f"Running for {root.name}. Your browser should have opened; if not, press "
                            "Open again. Closing this window stops the project browser and auto-sort.")

    def _stop_server(self) -> None:
        if self.server is not None:
            self.server.stop()
            self.server = None

    def shutdown(self) -> None:
        self._stop_server()


def main() -> None:
    window = tk.Tk()
    launcher = Launcher(window)

    def on_close() -> None:
        launcher.shutdown()
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", on_close)
    window.mainloop()


if __name__ == "__main__":
    main()
