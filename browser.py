from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from project import Project, ProjectConfigError, ProjectNotFoundError, load_project
from theme import COLORS, FONT, button, entry, label
from tracking import (
    TaskState,
    Tracker,
    TrackingError,
    build_tracker,
    current_user,
    events_dir,
    local_time,
    record_event,
)
from versions import VersionIndex, scan_versions

# Project browser: shots and assets on the left, their tasks in the middle,
# and the selected task's versions, notes and update form on the right.

EVENTS_POLL_MS = 3000
STATUS_COLORS = {
    "default": COLORS["muted"],
    "done": COLORS["success"],
    "feedback_request": COLORS["warning"],
    "retake": COLORS["error"],
    "other": COLORS["accent"],
}


def status_kind(project: Project, code: str) -> str:
    status = project.status(code)
    if status is None:
        return "other"
    for flag in ("retake", "feedback_request", "done", "default"):
        if getattr(status, flag):
            return flag
    return "other"


def local_time_from_mtime(mtime: float) -> str:
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


def open_in_file_browser(path: Path) -> None:
    """Show a file's folder in Explorer / Finder / the Linux file manager."""
    folder = path if path.is_dir() else path.parent
    if sys.platform.startswith("win"):
        os.startfile(folder)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


class ProjectBrowser(tk.Frame):
    def __init__(self, parent: tk.Misc, app) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        # app is the main window: root_var, call_soon, status_var, _ensure_project
        self.app = app

        self.project: Project | None = None
        self.index: VersionIndex | None = None
        self.tracker: Tracker | None = None
        self.loaded_root: Path | None = None
        self.loading = False
        self.events_stamp: tuple | None = None

        self.entity: str | None = None
        self.task: str | None = None
        self.version_paths: dict[str, Path] = {}

        self.filter_var = tk.StringVar()
        self.user_var = tk.StringVar(value=current_user())
        self.summary_var = tk.StringVar(value="")
        self.status_choice = tk.StringVar()
        self.assignee_var = tk.StringVar()
        self.detail_title = tk.StringVar(value="Select a shot or asset")

        self._build()
        self.filter_var.trace_add("write", lambda *_: self._fill_entities())
        self._poll_id = self.after(EVENTS_POLL_MS, self._poll_events)

    # UI Build
    def _build(self) -> None:
        toolbar = tk.Frame(self, bg=COLORS["bg"])
        toolbar.pack(fill="x", pady=(0, 8))
        button(toolbar, "Refresh", self.reload, primary=True).pack(side="left")
        label(toolbar, "Filter", muted=True, bg=COLORS["bg"]).pack(side="left", padx=(14, 4))
        entry(toolbar, self.filter_var, width=18).pack(side="left")
        label(toolbar, "You", muted=True, bg=COLORS["bg"]).pack(side="left", padx=(14, 4))
        entry(toolbar, self.user_var, width=12).pack(side="left")
        label(toolbar, textvariable=self.summary_var, muted=True, bg=COLORS["bg"]).pack(side="right")

        self.panes = panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        panes.add(self._build_entities(panes), weight=1)
        panes.add(self._build_tasks(panes), weight=1)
        panes.add(self._build_details(panes), weight=1)
        self._sashes_set = False
        panes.bind("<Configure>", self._place_sashes)

    def _place_sashes(self, _event=None) -> None:
        """Split the width 22% / 38% / 40% the first time the panes are shown."""
        width = self.panes.winfo_width()
        if self._sashes_set or width < 300:
            return
        self._sashes_set = True
        self.panes.sashpos(0, int(width * 0.22))
        self.panes.sashpos(1, int(width * 0.60))

    def _panel(self, parent: tk.Misc, title: str | tk.StringVar) -> tk.Frame:
        frame = tk.Frame(parent, bg=COLORS["panel"], highlightbackground=COLORS["border"],
                         highlightthickness=1)
        if isinstance(title, tk.StringVar):
            label(frame, textvariable=title, bold=True, anchor="w").pack(fill="x", padx=10, pady=(8, 6))
        else:
            label(frame, title, bold=True, anchor="w").pack(fill="x", padx=10, pady=(8, 6))
        return frame

    def _tree(self, parent: tk.Misc, columns: dict[str, int], show: str = "headings", height: int = 10):
        holder = tk.Frame(parent, bg=COLORS["panel"])
        tree = ttk.Treeview(holder, columns=list(columns), show=show, height=height, selectmode="browse")
        for name, width in columns.items():
            tree.heading(name, text=name.title(), anchor="w")
            tree.column(name, width=width, minwidth=40, anchor="w", stretch=name == list(columns)[0])
        scroll = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for kind, color in STATUS_COLORS.items():
            tree.tag_configure(kind, foreground=color)
        tree.tag_configure("group", foreground=COLORS["muted"])
        return holder, tree

    def _build_entities(self, parent: tk.Misc) -> tk.Frame:
        frame = self._panel(parent, "Shots & Assets")
        holder, self.entity_tree = self._tree(frame, {"done": 50}, show="tree headings", height=20)
        self.entity_tree.heading("#0", text="Name", anchor="w")
        self.entity_tree.column("#0", width=150, stretch=True)
        self.entity_tree.column("done", stretch=False)
        holder.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.entity_tree.bind("<<TreeviewSelect>>", self._on_entity_selected)
        return frame

    def _build_tasks(self, parent: tk.Misc) -> tk.Frame:
        frame = self._panel(parent, self.detail_title)
        holder, self.task_tree = self._tree(
            frame, {"task": 70, "status": 60, "assignee": 70, "latest": 50, "updated": 125}, height=12
        )
        holder.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.task_tree.bind("<<TreeviewSelect>>", self._on_task_selected)
        return frame

    def _build_details(self, parent: tk.Misc) -> tk.Frame:
        frame = self._panel(parent, "Versions")
        holder, self.version_tree = self._tree(
            frame, {"name": 170, "version": 60, "modified": 125, "files": 40}, show="tree headings", height=6
        )
        self.version_tree.column("#0", width=24, stretch=False)
        holder.pack(fill="both", expand=True, padx=10)
        self.version_tree.bind("<Double-1>", self._on_version_open)
        label(frame, "Double-click a version to open its folder", muted=True, anchor="w").pack(
            fill="x", padx=10, pady=(2, 6)
        )

        form = tk.Frame(frame, bg=COLORS["panel"])
        form.pack(side="bottom", fill="x", padx=10, pady=10)

        label(frame, "Notes", bold=True, anchor="w").pack(fill="x", padx=10, pady=(4, 4))
        self.notes = tk.Text(frame, height=6, bg=COLORS["input"], fg=COLORS["text"], relief="flat",
                             highlightthickness=0, wrap="word", padx=8, pady=6, font=(FONT, 10))
        self.notes.tag_configure("meta", foreground=COLORS["muted"], font=(FONT, 9))
        self.notes.pack(fill="both", expand=True, padx=10)
        self.notes.configure(state="disabled")

        label(form, "Status", muted=True).grid(row=0, column=0, sticky="w")
        self.status_box = ttk.Combobox(form, textvariable=self.status_choice, state="readonly", width=24)
        self.status_box.grid(row=0, column=1, sticky="w", padx=(6, 14))
        label(form, "Assignee", muted=True).grid(row=0, column=2, sticky="w")
        entry(form, self.assignee_var, width=12).grid(row=0, column=3, sticky="w", padx=(6, 0))

        label(form, "Comment", muted=True).grid(row=1, column=0, sticky="nw", pady=(8, 0))
        self.comment = tk.Text(form, height=3, bg=COLORS["input"], fg=COLORS["text"], relief="flat",
                               highlightthickness=0, insertbackground=COLORS["text"], wrap="word",
                               padx=6, pady=4, font=(FONT, 10))
        self.comment.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=(8, 0))
        form.columnconfigure(1, weight=1)
        self.save_btn = button(form, "Save", self.save, primary=True)
        self.save_btn.grid(row=2, column=3, sticky="e", pady=(8, 0))
        return frame

    # Loading
    def ensure_loaded(self) -> None:
        """Load when shown for the first time or after the project root changes."""
        root = Path(self.app.root_var.get()).expanduser().resolve()
        if root != self.loaded_root and not self.loading:
            self.reload(quiet=True)

    def mark_stale(self) -> None:
        """Files changed (e.g. after a sort): rescan next time the tab is shown."""
        self.loaded_root = None

    def reload(self, quiet: bool = False) -> None:
        if self.loading:
            return
        root = Path(self.app.root_var.get()).expanduser().resolve()
        if not quiet and not self.app._ensure_project(root):
            return
        self.loading = True
        self.summary_var.set("Loading...")
        threading.Thread(target=self._load_worker, args=(root,), daemon=True).start()

    def _load_worker(self, root: Path) -> None:
        try:
            project = load_project(root)
            index = scan_versions(project)
            tracker = build_tracker(project, index)
            result = (project, index, tracker, None)
        except (ProjectNotFoundError, ProjectConfigError, TrackingError, OSError) as error:
            result = (None, None, None, error)
        self.app.call_soon(self._loaded, root, *result)

    def _loaded(self, root, project, index, tracker, error) -> None:
        self.loading = False
        self.loaded_root = root
        if error is not None:
            self.project = self.index = self.tracker = None
            self.summary_var.set(str(error).splitlines()[0])
            self._fill_entities()
            return
        self.project, self.index, self.tracker = project, index, tracker
        self.events_stamp = self._events_stamp()
        codes = [f"{s.code} - {s.name}" for s in project.statuses]
        self.status_box.configure(values=codes)
        self._refresh_views()

    def _rebuild_tracker(self) -> None:
        """Re-read the event logs only; much cheaper than rescanning files."""
        if self.project is None:
            return
        try:
            self.tracker = build_tracker(self.project, self.index)
        except TrackingError as error:
            self.summary_var.set(str(error))
            return
        self.events_stamp = self._events_stamp()
        self._refresh_views()

    def _events_stamp(self) -> tuple | None:
        if self.project is None:
            return None
        folder = events_dir(self.project)
        if not folder.is_dir():
            return ()
        return tuple(sorted((p.name, p.stat().st_mtime, p.stat().st_size) for p in folder.glob("*.jsonl")))

    def _poll_events(self) -> None:
        """Pick up other people's changes as cloud sync brings them in."""
        try:
            if self.project is not None and not self.loading and self._events_stamp() != self.events_stamp:
                self._rebuild_tracker()
        except OSError:
            pass
        self._poll_id = self.after(EVENTS_POLL_MS, self._poll_events)

    def stop(self) -> None:
        self.after_cancel(self._poll_id)

    # Views
    def _refresh_views(self) -> None:
        self._fill_entities()
        self._fill_tasks()
        self._fill_details()
        if self.tracker is not None:
            states = [s for e in self.tracker.entities for s in self.tracker.tasks_for(e.name)]
            done = sum(1 for s in states if status_kind(self.project, s.status) == "done")
            waiting = len(self.tracker.with_flag("feedback_request"))
            retakes = len(self.tracker.with_flag("retake"))
            self.summary_var.set(
                f"{self.project.name}  |  {done}/{len(states)} tasks done  |  "
                f"{waiting} waiting for review  |  {retakes} retakes"
            )

    def _entity_kind(self, name: str) -> str:
        """Colour an entity by its most urgent task."""
        kinds = {status_kind(self.project, s.status) for s in self.tracker.tasks_for(name)}
        for kind in ("retake", "feedback_request"):
            if kind in kinds:
                return kind
        if kinds == {"done"}:
            return "done"
        return "other" if kinds - {"default"} else "default"

    def _fill_entities(self) -> None:
        tree = self.entity_tree
        opened = {iid for iid in self._all_items(tree) if tree.item(iid, "open")}
        first_load = not tree.get_children()
        tree.delete(*tree.get_children())
        if self.tracker is None:
            return

        text = self.filter_var.get().strip().lower()
        groups: dict[str, str] = {}

        def group(iid: str, parent: str, title: str) -> str:
            if iid not in groups:
                is_open = bool(text) or iid in opened or (first_load and parent == "")
                groups[iid] = tree.insert(parent, "end", iid=iid, text=title, open=is_open, tags=("group",))
            return groups[iid]

        type_order = list(self.project.task_types)
        entities = sorted(
            self.tracker.entities,
            key=lambda e: type_order.index(e.entity_type) if e.entity_type in type_order else len(type_order),
        )
        for entity in entities:
            if text and text not in entity.name.lower():
                continue
            entity_type = entity.entity_type or "other"
            parent = group(f"g:{entity_type}", "", entity_type.capitalize() + "s")
            if entity.context:
                act, sequence, *_ = [part.strip() for part in entity.context.split("/")]
                parent = group(f"g:{entity_type}:{act}", parent, act)
                parent = group(f"g:{entity_type}:{act}:{sequence}", parent, sequence)
            elif entity_type == "shot":
                parent = group("g:shot:unassigned", parent, "Not in a sequence")

            states = self.tracker.tasks_for(entity.name)
            done = sum(1 for s in states if status_kind(self.project, s.status) == "done")
            tree.insert(parent, "end", iid=f"e:{entity.name}", text=entity.name,
                        values=(f"{done}/{len(states)}",), tags=(self._entity_kind(entity.name),))

        # Show how many entities each group holds
        for iid in groups.values():
            count = sum(1 for child in self._all_items(tree, iid) if child.startswith("e:"))
            tree.item(iid, text=f"{tree.item(iid, 'text')} ({count})")

        if self.entity and tree.exists(f"e:{self.entity}"):
            tree.selection_set(f"e:{self.entity}")
            tree.see(f"e:{self.entity}")

    def _all_items(self, tree: ttk.Treeview, parent: str = "") -> list[str]:
        items = []
        for child in tree.get_children(parent):
            items.append(child)
            items.extend(self._all_items(tree, child))
        return items

    def _latest_for(self, entity: str, task: str):
        versions = self.index.find(entity) if self.index else None
        products = [p for p in versions.products.values() if p.task == task] if versions else []
        if not products:
            return None
        return max((p.latest for p in products), key=lambda v: v.modified)

    def _fill_tasks(self) -> None:
        tree = self.task_tree
        tree.delete(*tree.get_children())
        if self.tracker is None or self.entity is None or self.tracker.find(self.entity) is None:
            self.detail_title.set("Select a shot or asset")
            return

        entity = self.tracker.find(self.entity)
        self.detail_title.set(entity.name + (f"  ({entity.context})" if entity.context else ""))
        for state in self.tracker.tasks_for(entity.name):
            latest = self._latest_for(entity.name, state.task)
            tree.insert("", "end", iid=f"t:{state.task}", tags=(status_kind(self.project, state.status),),
                        values=(state.task, state.status, state.assignee or "-",
                                latest.label if latest else "-", local_time(state.updated)))

        if self.task is None or not tree.exists(f"t:{self.task}"):
            children = tree.get_children()
            self.task = children[0][2:] if children else None
        if self.task:
            tree.selection_set(f"t:{self.task}")

    def _current_state(self) -> TaskState | None:
        if self.tracker is None or self.entity is None or self.task is None:
            return None
        return self.tracker.task(self.entity, self.task)

    def _fill_details(self) -> None:
        tree = self.version_tree
        tree.delete(*tree.get_children())
        self.version_paths.clear()
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")

        state = self._current_state()
        if state is None:
            self.notes.configure(state="disabled")
            return

        versions = self.index.find(self.entity) if self.index else None
        products = [p for p in versions.sorted_products() if p.task == self.task] if versions else []
        for product in products:
            latest = product.latest
            parent = tree.insert("", "end", text="", open=False,
                                 values=(product.name, latest.label, local_time_from_mtime(latest.modified),
                                         len(latest.files)))
            self.version_paths[parent] = latest.files[0]
            for version in reversed(product.history()):
                iid = tree.insert(parent, "end", text="",
                                  values=("", version.label, local_time_from_mtime(version.modified),
                                          len(version.files)))
                self.version_paths[iid] = version.files[0]
        if not products:
            tree.insert("", "end", values=("No files for this task yet", "", "", ""))

        for note in state.notes:
            tag = f" -> {note.status}" if note.status else ""
            self.notes.insert("end", f"{note.user}  {local_time(note.time)}{tag}\n", "meta")
            self.notes.insert("end", note.text + "\n\n")
        if not state.notes:
            self.notes.insert("end", "No notes yet.", "meta")
        self.notes.see("end")  # newest note
        self.notes.configure(state="disabled")

        status = self.project.status(state.status)
        self.status_choice.set(f"{status.code} - {status.name}" if status else state.status)
        self.assignee_var.set(state.assignee or "")

    # Events
    def _on_entity_selected(self, _event=None) -> None:
        selection = self.entity_tree.selection()
        if not selection or not selection[0].startswith("e:"):
            return
        name = selection[0][2:]
        if name != self.entity:
            self.entity = name
            self._fill_tasks()
            self._fill_details()

    def _on_task_selected(self, _event=None) -> None:
        selection = self.task_tree.selection()
        if not selection:
            return
        task = selection[0][2:]
        if task != self.task:
            self.task = task
            self._fill_details()

    def _on_version_open(self, _event=None) -> None:
        selection = self.version_tree.selection()
        path = self.version_paths.get(selection[0]) if selection else None
        if path is not None:
            try:
                open_in_file_browser(path)
            except OSError as error:
                messagebox.showerror("Can't open folder", str(error))

    # Writing
    def save(self) -> None:
        """Apply the form: status change (with comment), assignee, or a note."""
        state = self._current_state()
        if state is None:
            return
        user = self.user_var.get().strip()
        if not user:
            messagebox.showerror("Who are you?", "Enter your name in the 'You' box first.")
            return

        new_status = self.status_choice.get().split(" - ")[0].strip()
        assignee = self.assignee_var.get().strip() or None
        comment = self.comment.get("1.0", "end").strip() or None
        common = {"user": user, "entity_type": state.entity_type}

        try:
            changed = False
            if new_status and new_status != state.status:
                record_event(self.tracker, "status", state.entity, state.task,
                             status=new_status, note=comment, **common)
                comment, changed = None, True
            if assignee != state.assignee:
                record_event(self.tracker, "assign", state.entity, state.task, assignee=assignee, **common)
                changed = True
            if comment:
                record_event(self.tracker, "note", state.entity, state.task, text=comment, **common)
                changed = True
        except (TrackingError, OSError) as error:
            messagebox.showerror("Couldn't save", str(error))
            return

        if changed:
            self.comment.delete("1.0", "end")
            self._rebuild_tracker()
            self.app.status_var.set(f"Saved {state.entity} {state.task}.")
