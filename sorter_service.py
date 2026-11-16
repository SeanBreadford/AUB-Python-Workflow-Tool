from __future__ import annotations

import threading
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from filesystem import Reporter, reporting, sort_project
from project import ProjectConfigError, ProjectNotFoundError

try:
    from watcher import DebouncedHandler, Observer

    WATCH_AVAILABLE = Observer is not None
except Exception:  # pragma: no cover - watchdog missing or broken
    DebouncedHandler = Observer = None  # type: ignore[assignment]
    WATCH_AVAILABLE = False

# Sorting for the project browser: one sort at a time in the background,
# auto-sort (watch mode) on demand, and one log the page reads as it grows.

LOG_LIMIT = 3000


class SorterBusy(Exception):
    pass


class WatchUnavailable(Exception):
    pass


def classify(message: str) -> str:
    """What a log line means, so the page can style, count and filter it."""
    for prefix, level in (
        ("MOVED", "moved"),
        ("DRY RUN", "preview"),
        ("DEFER", "waiting"),
        ("ERROR", "error"),
        ("COLLISION", "renamed"),
        ("SKIP (no rule)", "left"),
        ("SKIP (ignored)", "ignored"),
        ("SKIP (outside", "ignored"),
        ("OK (already sorted)", "already"),
    ):
        if message.startswith(prefix):
            return level
    return "info"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class _ServiceReporter(Reporter):
    def __init__(self, service: "SorterService", source: str, counts: Counter) -> None:
        self.service = service
        self.source = source
        self.counts = counts

    def log(self, message: str) -> None:
        self.service._append(message, classify(message), self.source, self.counts)

    def op(self, kind: str) -> None:
        pass  # counts come from the log lines, which say more (waiting vs ignored...)


class SorterService:
    def __init__(self, root: Path, on_change: Callable[[], None] = lambda: None) -> None:
        self.root = Path(root)
        self.on_change = on_change  # files moved: the browser should rescan
        self._lock = threading.Lock()
        self._log: deque[dict] = deque(maxlen=LOG_LIMIT)
        self._seq = 0
        self.job: dict | None = None
        self._job_thread: threading.Thread | None = None
        self._observer = None
        self._handler = None
        self.watch: dict | None = None

    # Log
    def _append(self, message: str, level: str, source: str, counts: Counter | None = None) -> None:
        with self._lock:
            if counts is not None:
                counts[level] += 1  # under the lock: status() may be copying it
            self._seq += 1
            self._log.append({"seq": self._seq, "time": _now(), "text": message, "level": level, "source": source})
        if level == "moved" and source == "watch":
            self.on_change()

    def status(self, after: int = 0) -> dict:
        with self._lock:
            lines = [entry for entry in self._log if entry["seq"] > after]
            return {
                "job": dict(self.job, counts=dict(self.job["counts"])) if self.job else None,
                "watch": dict(self.watch, counts=dict(self.watch["counts"])) if self.watch else None,
                "watch_available": WATCH_AVAILABLE,
                "log": lines,
                "next": self._seq,
            }

    # One-off sorts
    def start_sort(self, preview: bool = False, fast: bool = False) -> dict:
        with self._lock:
            if self.job and not self.job["finished"]:
                raise SorterBusy("A sort is already running; wait for it to finish.")
            self.job = {"kind": "preview" if preview else "sort", "fast": fast, "started": _now(),
                        "finished": None, "error": None, "counts": Counter()}
            job = self.job
        self._job_thread = threading.Thread(target=self._run, args=(job, preview, fast), daemon=True)
        self._job_thread.start()
        return {"kind": job["kind"], "started": job["started"]}

    def _run(self, job: dict, preview: bool, fast: bool) -> None:
        reporter = _ServiceReporter(self, "preview" if preview else "sort", job["counts"])
        try:
            with reporting(reporter):
                sort_project(self.root, dry_run=preview, fast_mode=fast)
        except (ProjectNotFoundError, ProjectConfigError, OSError) as error:
            job["error"] = str(error)
            self._append(f"ERROR: {error}", "error", reporter.source)
        finally:
            with self._lock:
                job["finished"] = _now()
            if not preview:
                self.on_change()

    def wait(self, timeout: float = 30.0) -> None:
        """Wait for the current sort (used by tests)."""
        if self._job_thread is not None:
            self._job_thread.join(timeout)

    # Auto-sort
    def set_watch(self, on: bool) -> dict | None:
        if on:
            self.start_watch()
        else:
            self.stop_watch()
        return self.watch

    def start_watch(self) -> None:
        if not WATCH_AVAILABLE:
            raise WatchUnavailable("Auto-sort needs the watchdog package. Install it with: pip install watchdog")
        with self._lock:
            if self.watch is not None:
                return
            self.watch = {"started": _now(), "counts": Counter()}
            counts = self.watch["counts"]
        reporter = _ServiceReporter(self, "watch", counts)
        self._handler = DebouncedHandler(root=self.root, dry_run=False, quiet=False, fast_mode=False,
                                         reporter=reporter)
        self._observer = Observer()
        self._observer.schedule(self._handler, str(self.root), recursive=True)
        self._observer.start()
        self._append("Auto-sort is on: new files are sorted as they arrive.", "info", "watch")

    def stop_watch(self) -> None:
        with self._lock:
            if self.watch is None:
                return
            self.watch = None
            handler, observer = self._handler, self._observer
            self._handler = self._observer = None
        if handler is not None:
            handler.stop()
        if observer is not None:
            observer.stop()
            observer.join(timeout=3)
        self._append("Auto-sort is off.", "info", "watch")

    def shutdown(self) -> None:
        self.stop_watch()
