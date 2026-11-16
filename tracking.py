from __future__ import annotations

import getpass
import json
import os
import re
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config import CONFIG_DIR_NAME
from project import Project
from versions import VersionIndex, natural_key

# Task statuses, notes and assignments, modelled on Kitsu.
#
# Every change is an event appended to _pipeline/events/<user>@<host>.jsonl.
# Each machine only ever writes its own file, so cloud sync never has two
# machines editing one file (which is what corrupts a shared database). To
# get the current state, everyone reads every file and replays the events in
# time order; the latest change to a task wins.

EVENTS_DIR_NAME = "events"
EVENT_TYPES = ("status", "note", "assign")


class TrackingError(Exception):
    pass


# Users
def current_user() -> str:
    return os.getenv("FILE_SORTER_USER") or getpass.getuser()


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-.]", "_", text) or "unknown"


def events_dir(project: Project) -> Path:
    return project.root / CONFIG_DIR_NAME / EVENTS_DIR_NAME


# State Model
@dataclass
class Note:
    time: str
    user: str
    text: str
    status: str | None = None


@dataclass
class TaskState:
    entity: str
    entity_type: str | None
    task: str
    status: str
    assignee: str | None = None
    updated: str | None = None
    updated_by: str | None = None
    notes: list[Note] = field(default_factory=list)
    # From the files: newest version number per product (unversioned = 0)
    latest: dict[str, int] = field(default_factory=dict)
    # Versions the last status decision was made on, e.g. what was approved
    decided_on: dict[str, int] | None = None
    last_activity: str | None = None
    # "Two files claim SH05_layout.ma v004"
    conflicts: list[str] = field(default_factory=list)
    last_artist: str | None = None  # who saved the newest file, when the name says
    assigned_by_person: bool = False  # once someone (un)assigns, files never change it


@dataclass(frozen=True)
class Flag:
    code: str     # see FLAG_ORDER
    message: str


# Most serious first: things that cause wrong work, then reminders
FLAG_ORDER = ("conflict", "changed_after_approval", "newer_than_submitted", "stalled")


def flag_rank(flag: Flag) -> int:
    return FLAG_ORDER.index(flag.code) if flag.code in FLAG_ORDER else len(FLAG_ORDER)


@dataclass
class TrackedEntity:
    name: str
    entity_type: str | None
    context: str | None = None


@dataclass
class Tracker:
    project: Project
    entities: list[TrackedEntity]
    tasks: dict[tuple[str, str], TaskState]
    unreadable_lines: int = 0
    # Everything that happened, oldest first: files, status changes, notes
    activity: list[dict] = field(default_factory=list)

    def tasks_for(self, entity: str) -> list[TaskState]:
        """Configured tasks first, in config order, then any others seen."""
        key = entity.lower()
        known = self.find(entity)
        configured = self.project.task_types.get(known.entity_type, ()) if known else ()
        states = [self.task(entity, task) for task in configured]
        extras = sorted(
            (s for (e, t), s in self.tasks.items() if e == key and t not in configured),
            key=lambda s: s.task,
        )
        return states + extras

    def task(self, entity: str, task: str) -> TaskState:
        state = self.tasks.get((entity.lower(), task))
        if state is not None:
            return state
        known = self.find(entity)
        return TaskState(
            entity=known.name if known else entity,
            entity_type=known.entity_type if known else None,
            task=task,
            status=self.project.default_status.code,
        )

    def find(self, entity: str) -> TrackedEntity | None:
        entity = entity.lower()
        return next((e for e in self.entities if e.name.lower() == entity), None)

    def with_flag(self, flag: str) -> list[TaskState]:
        codes = {s.code for s in self.project.statuses if getattr(s, flag)}
        return [s for s in self.tasks.values() if s.status in codes]

    def flags(self, state: TaskState, now: datetime | None = None) -> list[Flag]:
        return task_flags(self.project, state, now)

    def all_flags(self, now: datetime | None = None) -> list[tuple[TaskState, Flag]]:
        return [(state, flag) for state in self.tasks.values() for flag in task_flags(self.project, state, now)]


# Reading
def load_events(project: Project) -> tuple[list[dict], int]:
    """All events from every user's file, oldest first, and a count of bad lines."""
    folder = events_dir(project)
    events: dict[str, dict] = {}
    bad = 0
    if not folder.is_dir():
        return [], 0

    # Includes cloud "conflicted copy" files; duplicates are dropped by id
    for path in sorted(folder.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if event.get("type") in EVENT_TYPES and event.get("id"):
                    events.setdefault(event["id"], event)
                    continue
            except json.JSONDecodeError:
                pass
            bad += 1  # e.g. a half-synced last line

    # Stable sort: events with the same timestamp keep their order in the
    # file, so one person's quick successive changes replay in order
    return sorted(events.values(), key=lambda e: e.get("time", "")), bad


def _known_entities(project: Project, index: VersionIndex | None) -> list[TrackedEntity]:
    """Shots from the shot ranges, then every shot and asset that has files."""
    entities: dict[str, TrackedEntity] = {}
    for shot, (act, sequence, scene) in project.shot_mapping.items():
        entities[shot.lower()] = TrackedEntity(shot, "shot", f"{act} / {sequence} / {scene}")
    for entity in index.entities if index else ():
        entities.setdefault(entity.name.lower(), TrackedEntity(entity.name, entity.entity_type, entity.context))
    return list(entities.values())


def iso_from_mtime(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, timezone.utc).isoformat(timespec="milliseconds")


def file_events(index: VersionIndex | None) -> list[dict]:
    """
    One event per saved version, timed by the file's modified time. These are
    worked out from disk on every load and never written anywhere, so files
    are the record of what artists did without anyone typing it in.
    """
    events = []
    for entity in index.entities if index else ():
        for product in entity.products.values():
            if product.task is None:
                continue
            for version in product.history():
                events.append({
                    "type": "file",
                    "time": iso_from_mtime(version.modified),
                    "entity": entity.name,
                    "entity_type": entity.entity_type,
                    "task": product.task,
                    "product": product.name,
                    "version": version.number,
                    "label": version.label,
                    "files": len(version.files),
                    "copies": len(version.copies),
                    "sequence": version.is_sequence,
                    "artists": list(version.artists),
                })
    return events


def build_tracker(project: Project, index: VersionIndex | None = None) -> Tracker:
    if not project.statuses:
        raise TrackingError("No [[statuses]] in the project config, so tracking is off.")

    events, bad = load_events(project)
    # Files first on equal times; the sort is stable
    events = sorted(file_events(index) + events, key=lambda e: e.get("time", ""))
    entities = _known_entities(project, index)
    tracker = Tracker(project, entities, {}, bad)
    default = project.default_status.code

    def state_for(entity: str, entity_type: str | None, task: str) -> TaskState:
        key = (entity.lower(), task)
        if key not in tracker.tasks:
            known = tracker.find(entity)
            if known is None:
                # Entity only known from events, e.g. an asset with no files yet
                known = TrackedEntity(entity, entity_type)
                tracker.entities.append(known)
            tracker.tasks[key] = TaskState(known.name, known.entity_type, task, default)
        return tracker.tasks[key]

    for event in events:
        try:
            state = state_for(event["entity"], event.get("entity_type"), event["task"])
        except KeyError:
            tracker.unreadable_lines += 1
            continue
        time, user = event.get("time", ""), event.get("user", "?")
        state.last_activity = max(state.last_activity or "", time)
        base = {"time": time, "entity": state.entity, "task": state.task}

        if event["type"] == "file":
            product, number = event["product"], event.get("version") or 0
            artists = event.get("artists") or []
            state.latest[product] = max(state.latest.get(product, -1), number)
            if artists:
                state.last_artist = artists[-1]
            # A clash: the sorter had to rename a same-named file, or two people
            # put their name on the same version (SH05_layout_v004_sam / _alex)
            if event.get("copies") or len(artists) > 1:
                claimants = max(event.get("copies", 0) + 1, len(artists))
                who = f" ({' and '.join(artists)})" if len(artists) > 1 else ""
                state.conflicts.append(f"{claimants} files claim {product} {event['label']}{who}")
            tracker.activity.append({**base, "kind": "file", "product": product, "label": event["label"],
                                     "files": event["files"], "sequence": event.get("sequence", False),
                                     "copies": event.get("copies", 0), "artists": artists})
            _auto_status(project, tracker, state, time, state_for)
            # Whoever saves the first file for an unassigned task is working on it
            if artists and state.assignee is None and not state.assigned_by_person:
                state.assignee = artists[0]
                tracker.activity.append({**base, "kind": "assign", "user": "files", "assignee": artists[0],
                                         "auto": True})
        elif event["type"] == "status":
            state.status = str(event.get("status", default)).lower()
            state.updated, state.updated_by = time, user
            # What the decision was made on: recorded at the time, else what the files said then
            decided = event.get("versions")
            state.decided_on = dict(decided) if isinstance(decided, dict) else dict(state.latest)
            if event.get("note"):
                state.notes.append(Note(time, user, event["note"], state.status))
            tracker.activity.append({**base, "kind": "status", "user": user, "status": state.status,
                                     "note": event.get("note")})
            _run_automations(project, tracker, state, time, user, state_for)
        elif event["type"] == "note":
            state.notes.append(Note(time, user, str(event.get("text", ""))))
            tracker.activity.append({**base, "kind": "note", "user": user, "note": str(event.get("text", ""))})
        elif event["type"] == "assign":
            state.assignee = event.get("assignee") or None
            state.assigned_by_person = True
            state.updated, state.updated_by = time, user
            tracker.activity.append({**base, "kind": "assign", "user": user, "assignee": state.assignee})

    tracker.entities.sort(key=lambda e: natural_key(e.name))
    return tracker


def _set_auto(tracker: Tracker, state: TaskState, status: str, time: str, by: str) -> None:
    state.status = status
    state.updated, state.updated_by = time, by
    tracker.activity.append({"time": time, "entity": state.entity, "task": state.task, "kind": "status",
                             "user": by, "status": status, "auto": True})


def _auto_status(project: Project, tracker: Tracker, state: TaskState, time: str, state_for) -> None:
    """A new file is evidence of work: move the task along without anyone clicking."""
    status = project.status(state.status)
    if project.auto_started and state.status in project.auto_start_from:
        _set_auto(tracker, state, project.auto_started, time, "files")
        _run_automations(project, tracker, state, time, "files", state_for)
    elif project.auto_after_retake and status is not None and status.retake:
        _set_auto(tracker, state, project.auto_after_retake, time, "files")


def _run_automations(project, tracker: Tracker, state: TaskState, time: str, user: str, state_for) -> None:
    """
    Apply matching automations. Only tasks that haven't started (still at the
    default status) are moved, so an automation never undoes someone's work.
    """
    for automation in project.automations:
        if automation.task != state.task or automation.status != state.status:
            continue
        if automation.entity_type not in (None, state.entity_type):
            continue
        target = state_for(state.entity, state.entity_type, automation.then_task)
        if target.status == project.default_status.code:
            _set_auto(tracker, target, automation.then_status, time, f"{user} (automation)")


# Health
def _newer(state: TaskState) -> list[str]:
    """Products with a version newer than the one the last decision was made on."""
    if state.decided_on is None:
        return []
    found = []
    for product, number in sorted(state.latest.items()):
        decided = state.decided_on.get(product)
        if decided is None:
            found.append(f"{product} (new since then)")
        elif number > decided:
            found.append(f"{product} v{number:03d} (decision was on v{decided:03d})")
    return found


def task_flags(project: Project, state: TaskState, now: datetime | None = None) -> list[Flag]:
    """Problems worth someone's attention, worked out fresh from the current state."""
    flags = []
    status = project.status(state.status)
    newer = _newer(state)
    if status is not None and status.done and newer:
        flags.append(Flag("changed_after_approval", "Changed after approval: " + "; ".join(newer)))
    if status is not None and status.feedback_request and newer:
        flags.append(Flag("newer_than_submitted", "Newer than what was sent for review: " + "; ".join(newer)))
    for conflict in state.conflicts:
        flags.append(Flag("conflict", f"Version clash: {conflict}. Two people may have saved the same version."))

    active = status is not None and not status.default and not status.done
    if project.stale_days and active and state.last_activity:
        now = now or datetime.now(timezone.utc)
        try:
            idle = (now - datetime.fromisoformat(state.last_activity)).days
        except ValueError:
            idle = 0
        if idle >= project.stale_days:
            who = f" (last file from {state.last_artist})" if state.last_artist else ""
            if status.retake:
                flags.append(Flag("stalled", f"Retake noted {idle} days ago, no new version yet{who}"))
            else:
                flags.append(Flag("stalled", f"No new file or update for {idle} days{who}"))
    return flags


# Writing
def resolve_entity(tracker: Tracker, name: str, entity_type: str | None = None) -> TrackedEntity:
    known = tracker.find(name)
    if known:
        return known
    if entity_type is None:
        raise TrackingError(
            f"Unknown shot or asset '{name}'. Shots come from the shot ranges and assets "
            f"from sorted files; to track a new one, give its entity type."
        )
    if entity_type not in tracker.project.task_types:
        types = ", ".join(tracker.project.task_types) or "none"
        raise TrackingError(f"Unknown entity type '{entity_type}' (configured: {types})")
    return TrackedEntity(name, entity_type)


def record_event(
    tracker: Tracker,
    event_type: str,
    entity_name: str,
    task: str,
    user: str | None = None,
    entity_type: str | None = None,
    **data: str | None,
) -> dict:
    """Validate and append one event to this user's log. Returns the event."""
    project = tracker.project
    entity = resolve_entity(tracker, entity_name, entity_type)

    tasks = project.task_types.get(entity.entity_type or "")
    if tasks is not None and task not in tasks:
        raise TrackingError(f"'{task}' is not a {entity.entity_type} task (tasks: {', '.join(tasks)})")
    if event_type == "status":
        status = project.status(data.get("status") or "")
        if status is None:
            codes = ", ".join(s.code for s in project.statuses)
            raise TrackingError(f"Unknown status '{data.get('status')}' (statuses: {codes})")
        data["status"] = status.code

    user = user or current_user()
    if event_type == "status":
        # Pin the decision to the versions that exist right now (what the person saw)
        latest = tracker.task(entity.name, task).latest
        if latest and "versions" not in data:
            data["versions"] = dict(latest)  # type: ignore[assignment]
    event = {
        "id": uuid.uuid4().hex,
        "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "user": user,
        "type": event_type,
        "entity": entity.name,
        "entity_type": entity.entity_type,
        "task": task,
        **{k: v for k, v in data.items() if v is not None},
    }

    folder = events_dir(project)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_safe(user)}@{_safe(socket.gethostname())}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return event


_UNSET = object()


def update_task(
    tracker: Tracker,
    entity: str,
    task: str,
    user: str,
    status: str | None = None,
    assignee: object = _UNSET,
    comment: str | None = None,
    redecide: bool = False,
) -> list[dict]:
    """
    Apply a task form like Kitsu's comment panel: a status change carries the
    comment with it; a comment without a status change becomes a note.
    Only fields that actually change are recorded, unless `redecide` asks to
    record the same status again (re-approve or re-submit the newest version).
    Returns the new events.
    """
    if not user or not user.strip():
        raise TrackingError("Enter your name first, so changes can be credited.")
    user = user.strip()
    state = tracker.task(entity, task)
    entity_type = state.entity_type
    comment = (comment or "").strip() or None
    events = []

    if status and (status.lower() != state.status or redecide):
        events.append(record_event(tracker, "status", entity, task, user=user, entity_type=entity_type,
                                   status=status, note=comment))
        comment = None
    if assignee is not _UNSET:
        new_assignee = (assignee or "").strip() or None  # type: ignore[union-attr]
        if new_assignee != state.assignee:
            events.append(record_event(tracker, "assign", entity, task, user=user,
                                       entity_type=entity_type, assignee=new_assignee))
    if comment:
        events.append(record_event(tracker, "note", entity, task, user=user, entity_type=entity_type,
                                   text=comment))
    return events


def events_stamp(project: Project) -> tuple:
    """Changes whenever any events file is added, removed or grows."""
    folder = events_dir(project)
    if not folder.is_dir():
        return ()
    return tuple(sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in folder.glob("*.jsonl")))


# Display Helpers
# Defaults checked for WCAG AA contrast (4.5:1) against their text colour
DEFAULT_STATUS_COLORS = {"ready": "#2f6fd6", "wip": "#7a5fc0"}
FLAG_COLORS = {"retake": "#c62828", "feedback_request": "#a85d00", "done": "#2e7d32", "default": "#3a3a3a"}
FALLBACK_COLORS = ("#00796b", "#5d4037", "#ad1457", "#455a64", "#6a1b9a")


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def status_colors(project: Project) -> dict[str, tuple[str, str]]:
    """Status code -> (background, text), text picked for the best contrast."""
    colors = {}
    for index, status in enumerate(project.statuses):
        background = status.color or DEFAULT_STATUS_COLORS.get(status.code)
        if background is None:
            flag = next((f for f in ("retake", "feedback_request", "done", "default") if getattr(status, f)), None)
            background = FLAG_COLORS.get(flag) or FALLBACK_COLORS[index % len(FALLBACK_COLORS)]
        text = max(("#ffffff", "#111111"), key=lambda t: contrast_ratio(background, t))
        if status.default:
            text = "#d0d0d0"  # quieter than white for "nothing happened yet"
        colors[status.code] = (background, text)
    return colors


def local_time(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def format_status_grid(tracker: Tracker) -> str:
    """One row per shot/asset, one column per task, like Kitsu's task grid."""
    project = tracker.project
    lines = [f"Project: {project.name}"]
    width = max([len(s.code) for s in project.statuses] + [1])

    by_type: dict[str | None, list[TrackedEntity]] = {}
    for entity in tracker.entities:
        by_type.setdefault(entity.entity_type, []).append(entity)

    order = list(project.task_types) + [t for t in by_type if t not in project.task_types]
    for entity_type in order:
        entities = by_type.get(entity_type)
        if not entities:
            continue
        columns = list(project.task_types.get(entity_type or "", ()))
        for entity in entities:
            columns += [s.task for s in tracker.tasks_for(entity.name) if s.task not in columns]
        name_width = max(len(e.name) for e in entities)
        col_width = max([width] + [len(c) for c in columns])

        lines += ["", f"== {entity_type or 'other'} ({len(entities)}) =="]
        lines.append((" " * name_width + "  " + "  ".join(f"{c:<{col_width}}" for c in columns)).rstrip())
        for entity in entities:
            cells = [tracker.task(entity.name, c).status for c in columns]
            lines.append((f"{entity.name:<{name_width}}  " + "  ".join(f"{c:<{col_width}}" for c in cells)).rstrip())

    for flag, title in (("feedback_request", "Waiting for review"), ("retake", "Retakes")):
        waiting = tracker.with_flag(flag)
        if waiting:
            lines += ["", f"{title}:"]
            for state in sorted(waiting, key=lambda s: (s.updated or "")):
                who = f" ({state.assignee})" if state.assignee else ""
                lines.append(f"  {state.entity} {state.task}{who}  since {local_time(state.updated)}")

    flagged = sorted(tracker.all_flags(),
                     key=lambda pair: (flag_rank(pair[1]), natural_key(pair[0].entity), pair[0].task))
    if flagged:
        lines += ["", "Needs attention:"]
        for state, flag in flagged:
            lines.append(f"  {state.entity} {state.task}: {flag.message}")

    counts: dict[str, int] = {}
    for entity in tracker.entities:
        for state in tracker.tasks_for(entity.name):
            counts[state.status] = counts.get(state.status, 0) + 1
    if counts:
        summary = ", ".join(f"{s.code} {counts[s.code]}" for s in project.statuses if s.code in counts)
        unknown = [code for code in counts if project.status(code) is None]
        summary += "".join(f", {code} {counts[code]} (not in config)" for code in unknown)
        lines += ["", f"Tasks: {summary}"]

    if tracker.unreadable_lines:
        lines.append(f"Warning: skipped {tracker.unreadable_lines} unreadable event line(s).")
    return "\n".join(lines)


def format_entity(tracker: Tracker, index: VersionIndex | None, name: str) -> str:
    """Everything about one shot or asset: tasks, versions and notes."""
    entity = tracker.find(name)
    if entity is None:
        raise TrackingError(f"Unknown shot or asset '{name}'")
    versions = index.find(entity.name) if index else None

    header = entity.name + (f"  ({entity.context})" if entity.context else "")
    lines = [header, f"type: {entity.entity_type or '-'}"]
    for state in tracker.tasks_for(entity.name):
        status = tracker.project.status(state.status)
        label = f"{state.status} ({status.name})" if status else f"{state.status} (not in config)"
        who = f"  assigned: {state.assignee}" if state.assignee else ""
        changed = f"  by {state.updated_by} {local_time(state.updated)}" if state.updated else ""
        lines += ["", f"{state.task}: {label}{who}{changed}"]
        for flag in tracker.flags(state):
            lines.append(f"  ! {flag.message}")

        products = [p for p in versions.sorted_products() if p.task == state.task] if versions else []
        for product in products:
            history = ", ".join(v.label + (f" {'/'.join(v.artists)}" if v.artists else "") for v in product.history())
            lines.append(f"  {product.name}  latest {product.latest.label}  [{history}]")
        for note in state.notes:
            tag = f" [{note.status}]" if note.status else ""
            lines.append(f"  note {local_time(note.time)} {note.user}{tag}: {note.text}")

    if versions:
        untasked = [p for p in versions.sorted_products() if p.task is None]
        if untasked:
            lines += ["", "other files:"]
            lines += [f"  {p.name}  latest {p.latest.label}" for p in untasked]
    return "\n".join(lines)
