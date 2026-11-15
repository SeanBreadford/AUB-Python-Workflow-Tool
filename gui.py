from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from config import DEFAULT_BATCH_SIZE, ROOT
from filesystem import set_operation_callback, sort_project, sort_project_batch
from project import ProjectConfigError, ProjectNotFoundError, init_project, load_project
from tracking import build_tracker
from versions import format_report, scan_versions

try:
    from watcher import DebouncedHandler, Observer
    WATCH_AVAILABLE = Observer is not None
except Exception:
    DebouncedHandler = None  # type: ignore[assignment]
    Observer = None  # type: ignore[assignment]
    WATCH_AVAILABLE = False


# UI Theme
COLORS = {
    "bg": "#121212",
    "panel": "#1e1e1e",
    "input": "#2a2a2a",
    "text": "#eaeaea",
    "muted": "#a8a8a8",
    "accent": "#3a86ff",
    "success": "#4caf50",
    "warning": "#ff9800",
    "error": "#ef5350",
}


# Log Writer
class QueueWriter:
    def __init__(self, out_queue: "queue.Queue[str]") -> None:
        self.out_queue = out_queue

    def write(self, message: str) -> None:
        message = message.strip()
        if message:
            self.out_queue.put(message)

    def flush(self) -> None:
        return


# App UI
class FileSorterGUI:
    def __init__(self, root_window: tk.Tk) -> None:
        self.root = root_window
        self.root.title("Production File Organiser")
        self.root.geometry("980x700")
        self.root.configure(bg=COLORS["bg"])

        # State
        self.processing = False
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.op_queue: "queue.Queue[str]" = queue.Queue()
        self.stats = {"processed": 0, "moved": 0, "skipped": 0, "errors": 0}

        # Watch state
        self.watching = False
        self.observer = None
        self.handler = None

        # Variables
        self.root_var = tk.StringVar(value=str(ROOT))
        self.dry_run_var = tk.BooleanVar(value=False)
        self.fast_mode_var = tk.BooleanVar(value=False)
        self.batch_var = tk.BooleanVar(value=True)
        self.batch_size_var = tk.IntVar(value=DEFAULT_BATCH_SIZE)
        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()
        self._poll_log_queue()

        # Route sorter output and stats to the GUI for the app's lifetime,
        # so watch mode is visible too (and works when stdout is None in the exe)
        self._old_stdout = sys.stdout
        sys.stdout = QueueWriter(self.log_queue)
        set_operation_callback(self._on_operation)

    # UI Build
    def _build_ui(self) -> None:
        container = tk.Frame(self.root, bg=COLORS["bg"])
        container.pack(fill="both", expand=True, padx=14, pady=14)

        title = tk.Label(
            container,
            text="Production File Organiser",
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=("Segoe UI", 18, "bold"),
        )
        title.pack(anchor="w", pady=(0, 10))

        # Root picker
        root_panel = tk.Frame(container, bg=COLORS["panel"])
        root_panel.pack(fill="x", pady=(0, 10))
        root_panel.configure(highlightbackground="#2d2d2d", highlightthickness=1)

        tk.Label(
            root_panel,
            text="Project Root",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=10, pady=(10, 4))

        row = tk.Frame(root_panel, bg=COLORS["panel"])
        row.pack(fill="x", padx=10, pady=(0, 10))

        root_entry = tk.Entry(
            row,
            textvariable=self.root_var,
            bg=COLORS["input"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            bd=8,
            font=("Segoe UI", 10),
        )
        root_entry.pack(side="left", fill="x", expand=True)

        browse = tk.Button(
            row,
            text="Browse",
            command=self._browse_root,
            bg=COLORS["accent"],
            fg="white",
            relief="flat",
            padx=14,
            pady=8,
            cursor="hand2",
        )
        browse.pack(side="left", padx=(8, 0))

        # Options
        options = tk.Frame(container, bg=COLORS["panel"])
        options.pack(fill="x", pady=(0, 10))
        options.configure(highlightbackground="#2d2d2d", highlightthickness=1)

        tk.Label(
            options,
            text="Options",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 6))

        check_style = {
            "bg": COLORS["panel"],
            "fg": COLORS["text"],
            "selectcolor": COLORS["input"],
            "activebackground": COLORS["panel"],
            "activeforeground": COLORS["text"],
            "font": ("Segoe UI", 10),
        }

        tk.Checkbutton(options, text="Dry Run", variable=self.dry_run_var, **check_style).grid(
            row=1, column=0, sticky="w", padx=10, pady=2
        )
        tk.Checkbutton(options, text="Fast Mode", variable=self.fast_mode_var, **check_style).grid(
            row=1, column=1, sticky="w", padx=10, pady=2
        )
        tk.Checkbutton(options, text="Batch Mode", variable=self.batch_var, **check_style).grid(
            row=1, column=2, sticky="w", padx=10, pady=2
        )

        tk.Label(
            options,
            text="Batch Size",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
        ).grid(row=2, column=0, sticky="w", padx=10, pady=(6, 10))

        tk.Spinbox(
            options,
            from_=1,
            to=500,
            textvariable=self.batch_size_var,
            width=8,
            bg=COLORS["input"],
            fg=COLORS["text"],
            relief="flat",
            bd=5,
            buttonbackground=COLORS["panel"],
            highlightthickness=0,
        ).grid(row=2, column=1, sticky="w", pady=(6, 10))

        # Buttons
        actions = tk.Frame(container, bg=COLORS["bg"])
        actions.pack(fill="x", pady=(0, 10))

        self.sort_btn = tk.Button(
            actions,
            text="Sort Now",
            command=self.start_sort,
            bg=COLORS["accent"],
            fg="white",
            relief="flat",
            padx=14,
            pady=10,
            cursor="hand2",
        )
        self.sort_btn.pack(side="left")

        self.watch_btn = tk.Button(
            actions,
            text="Start Watch",
            command=self.toggle_watch,
            bg="#444",
            fg="white",
            relief="flat",
            padx=14,
            pady=10,
            cursor="hand2",
        )
        self.watch_btn.pack(side="left", padx=(8, 0))

        report_btn = tk.Button(
            actions,
            text="Version Report",
            command=self.start_report,
            bg="#444",
            fg="white",
            relief="flat",
            padx=14,
            pady=10,
            cursor="hand2",
        )
        report_btn.pack(side="left", padx=(8, 0))

        clear_btn = tk.Button(
            actions,
            text="Clear Log",
            command=self.clear_log,
            bg="#333",
            fg="white",
            relief="flat",
            padx=14,
            pady=10,
            cursor="hand2",
        )
        clear_btn.pack(side="left", padx=(8, 0))

        self.progress = ttk.Progressbar(actions, mode="indeterminate")
        self.progress.pack(side="right", fill="x", expand=True, padx=(20, 0))

        # Stats
        stats_panel = tk.Frame(container, bg=COLORS["panel"])
        stats_panel.pack(fill="x", pady=(0, 10))
        stats_panel.configure(highlightbackground="#2d2d2d", highlightthickness=1)

        tk.Label(
            stats_panel,
            text="Stats",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=10, pady=(10, 6))

        self.stat_labels: dict[str, tk.Label] = {}
        line = tk.Frame(stats_panel, bg=COLORS["panel"])
        line.pack(fill="x", padx=10, pady=(0, 10))
        for key in ("processed", "moved", "skipped", "errors"):
            badge = tk.Frame(line, bg=COLORS["input"], padx=10, pady=6)
            badge.pack(side="left", padx=(0, 8))
            tk.Label(
                badge,
                text=key.title(),
                bg=COLORS["input"],
                fg=COLORS["muted"],
                font=("Segoe UI", 9),
            ).pack()
            value = tk.Label(
                badge,
                text="0",
                bg=COLORS["input"],
                fg=COLORS["text"],
                font=("Segoe UI", 11, "bold"),
            )
            value.pack()
            self.stat_labels[key] = value

        # Log panel
        log_panel = tk.Frame(container, bg=COLORS["panel"])
        log_panel.pack(fill="both", expand=True)
        log_panel.configure(highlightbackground="#2d2d2d", highlightthickness=1)

        tk.Label(
            log_panel,
            text="Log",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=10, pady=(10, 6))

        self.log_text = tk.Text(
            log_panel,
            bg=COLORS["input"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            font=("Consolas", 10),
            wrap="word",
            padx=10,
            pady=10,
        )
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_text.configure(state="disabled")

        # Status bar
        status_bar = tk.Label(
            container,
            textvariable=self.status_var,
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            anchor="w",
            font=("Segoe UI", 9),
        )
        status_bar.pack(fill="x", pady=(8, 0))

    # UI Actions
    def _browse_root(self) -> None:
        selected = filedialog.askdirectory(title="Select project root")
        if selected:
            self.root_var.set(selected)

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        # Keep log size reasonable
        max_lines = 1000
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > max_lines:
            self.log_text.delete("1.0", f"{line_count - max_lines}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_log_queue(self) -> None:
        try:
            while True:
                message = self.log_queue.get_nowait()
                self._append_log(message)
        except queue.Empty:
            pass
        try:
            while True:
                self._apply_operation(self.op_queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(120, self._poll_log_queue)

    # Stats Helpers
    def _reset_stats(self) -> None:
        self.stats = {"processed": 0, "moved": 0, "skipped": 0, "errors": 0}
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        for key, label in self.stat_labels.items():
            label.configure(text=str(self.stats.get(key, 0)))

    def _apply_operation(self, operation: str) -> None:
        if operation in self.stats:
            self.stats[operation] += 1
            self._refresh_stats()

    def _on_operation(self, operation: str) -> None:
        # Called from worker threads: queue it for the Tk thread to apply
        self.op_queue.put(operation)

    # Project Check
    def _ensure_project(self, root: Path) -> bool:
        """True if root has a valid project config; offers to create one if not."""
        try:
            project = load_project(root)
        except ProjectConfigError as error:
            messagebox.showerror("Project config error", str(error))
            return False
        except ProjectNotFoundError:
            create = messagebox.askyesno(
                "No project config",
                f"This folder isn't set up as a project yet:\n{root}\n\n"
                "Create the default project config here?",
            )
            if create:
                path = init_project(root)
                self.log_queue.put(f"Created project config: {path}")
                messagebox.showinfo(
                    "Project created",
                    f"Review the rules in:\n{path}\n\nThen run again, ideally with Dry Run first.",
                )
            return False

        self.root.title(f"Production File Organiser - {project.name}")
        return True

    # Sort Actions
    def start_sort(self) -> None:
        if self.processing:
            messagebox.showwarning("Busy", "Sorting is already running.")
            return

        root = Path(self.root_var.get()).expanduser().resolve()
        if not root.exists():
            messagebox.showerror("Invalid root", f"Folder does not exist:\n{root}")
            return
        if not self._ensure_project(root):
            return

        try:
            batch_size = max(1, self.batch_size_var.get())
        except tk.TclError:
            messagebox.showerror("Invalid batch size", "Batch size must be a whole number.")
            return

        # Read Tk variables here: they must not be touched from the worker thread
        options = {
            "dry_run": self.dry_run_var.get(),
            "fast_mode": self.fast_mode_var.get(),
            "batch": self.batch_var.get(),
            "batch_size": batch_size,
        }

        self.processing = True
        self._reset_stats()
        self.progress.start(10)
        self.sort_btn.configure(state="disabled")
        self.status_var.set("Sorting...")

        worker = threading.Thread(target=self._sort_worker, args=(root, options), daemon=True)
        worker.start()

    def _sort_worker(self, root: Path, options: dict) -> None:
        status = "Sort complete."
        try:
            if options["batch"]:
                sort_project_batch(
                    root=root,
                    dry_run=options["dry_run"],
                    quiet=False,
                    batch_size=options["batch_size"],
                    fast_mode=options["fast_mode"],
                )
            else:
                sort_project(
                    root=root,
                    dry_run=options["dry_run"],
                    quiet=False,
                    fast_mode=options["fast_mode"],
                )
            if options["dry_run"]:
                status = "Dry run complete."
        except Exception as error:
            self.log_queue.put(f"ERROR: {error}")
            status = "Sort failed."
        finally:
            self.root.after(0, self._finish_sort_ui, status)

    def _finish_sort_ui(self, status: str) -> None:
        self.processing = False
        self.progress.stop()
        self.sort_btn.configure(state="normal")
        self.status_var.set(status)

    # Report Actions
    def start_report(self) -> None:
        root = Path(self.root_var.get()).expanduser().resolve()
        if not root.exists():
            messagebox.showerror("Invalid root", f"Folder does not exist:\n{root}")
            return
        if not self._ensure_project(root):
            return
        self.status_var.set("Scanning versions...")
        threading.Thread(target=self._report_worker, args=(root,), daemon=True).start()

    def _report_worker(self, root: Path) -> None:
        try:
            project = load_project(root)
            index = scan_versions(project, root)
            tracker = build_tracker(project, index) if project.statuses else None
            report = format_report(index, tracker)
            for line in report.splitlines():
                self.log_queue.put(line or " ")
            status = "Version report complete."
        except Exception as error:
            self.log_queue.put(f"ERROR: {error}")
            status = "Version report failed."
        self.root.after(0, self.status_var.set, status)

    # Watch Actions
    def toggle_watch(self) -> None:
        if self.watching:
            self.stop_watch()
        else:
            self.start_watch()

    def start_watch(self) -> None:
        if not WATCH_AVAILABLE:
            messagebox.showerror(
                "Watch unavailable",
                "watchdog is not installed.\nInstall it with: pip install watchdog",
            )
            return

        root = Path(self.root_var.get()).expanduser().resolve()
        if not root.exists():
            messagebox.showerror("Invalid root", f"Folder does not exist:\n{root}")
            return

        if self.watching:
            return
        if not self._ensure_project(root):
            return

        self._reset_stats()
        self.handler = DebouncedHandler(
            root=root,
            dry_run=self.dry_run_var.get(),
            quiet=False,
            fast_mode=True,
        )
        self.observer = Observer()
        self.observer.schedule(self.handler, str(root), recursive=True)
        self.observer.start()

        self.watching = True
        self.watch_btn.configure(text="Stop Watch", bg=COLORS["warning"])
        self.status_var.set("Watch mode active.")

    def stop_watch(self) -> None:
        if not self.watching:
            return

        try:
            if self.handler:
                self.handler.stop()
            if self.observer:
                self.observer.stop()
                self.observer.join(timeout=2)
        finally:
            self.handler = None
            self.observer = None
            self.watching = False
            self.watch_btn.configure(text="Start Watch", bg="#444")
            self.status_var.set("Watch mode stopped.")

    # Shutdown
    def shutdown(self) -> None:
        self.stop_watch()
        # small delay helps watchdog close cleanly
        time.sleep(0.05)
        set_operation_callback(None)
        sys.stdout = self._old_stdout


# Entry Point
def main() -> None:
    root = tk.Tk()
    app = FileSorterGUI(root)

    def on_close() -> None:
        app.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
