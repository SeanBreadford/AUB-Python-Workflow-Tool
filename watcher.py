from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Iterable, Set

from config import DEBOUNCE_SECONDS
from filesystem import sort_project

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:  # pragma: no cover
    FileSystemEventHandler = object  # type: ignore[assignment]
    Observer = None  # type: ignore[assignment]



def _is_ignored_event_path(path: Path, root: Path) -> bool:
    try:
        parts = path.resolve().relative_to(root.resolve()).parts
    except Exception:
        parts = path.parts

    for part in parts:
        if part.startswith("_"):
            return True
        p = part.lower()
        if p.startswith(".") or p.startswith("~"):
            return True
    name = path.name.lower()
    if name.startswith((".", "~", "_")):
        return True
    return False


# Watch Handler
class DebouncedHandler(FileSystemEventHandler):  # type: ignore[misc]
    def __init__(
        self,
        root: Path,
        dry_run: bool = False,
        quiet: bool = True,
        fast_mode: bool = True,
        debounce_seconds: float = DEBOUNCE_SECONDS,
    ) -> None:
        self.root = root.resolve()
        self.dry_run = dry_run
        self.quiet = quiet
        self.fast_mode = fast_mode
        self.debounce_seconds = max(0.1, debounce_seconds)

        self._pending: Set[Path] = set()
        self._lock = threading.Lock()
        self._running = True
        self._last_event_time = 0.0

        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    # Event Hooks
    def on_any_event(self, event) -> None:  # noqa: ANN001
        if not self._running:
            return
        if getattr(event, "is_directory", False):
            return

        for value in (getattr(event, "src_path", None), getattr(event, "dest_path", None)):
            if not value:
                continue
            path = Path(value).resolve()
            if _is_ignored_event_path(path, self.root):
                continue
            if path == self.root or self.root in path.parents:
                with self._lock:
                    self._pending.add(path)
                    self._last_event_time = time.time()

    # Worker Loop
    def _loop(self) -> None:
        while self._running:
            time.sleep(0.2)

            with self._lock:
                idle_long_enough = (time.time() - self._last_event_time) >= self.debounce_seconds
                if not self._pending or not idle_long_enough:
                    continue
                batch = list(self._pending)
                self._pending.clear()

            existing_files = [p for p in batch if p.exists() and p.is_file()]
            if not existing_files:
                continue

            sort_project(
                root=self.root,
                dry_run=self.dry_run,
                quiet=self.quiet,
                files=existing_files,
                fast_mode=self.fast_mode,
            )

    # Stop Hook
    def stop(self) -> None:
        self._running = False
        self._worker.join(timeout=2.0)


# Watch Entry
def watch_project(
    root: Path,
    dry_run: bool = False,
    quiet: bool = False,
    fast_mode: bool = True,
    debounce_seconds: float = DEBOUNCE_SECONDS,
) -> None:
    if Observer is None:
        raise RuntimeError("watchdog is not installed. Install it with: pip install watchdog")

    root = Path(root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Root does not exist: {root}")

    handler = DebouncedHandler(
        root=root,
        dry_run=dry_run,
        quiet=quiet,
        fast_mode=fast_mode,
        debounce_seconds=debounce_seconds,
    )

    observer = Observer()
    observer.schedule(handler, str(root), recursive=True)
    observer.start()

    print(f"Watching: {root}")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        handler.stop()
        observer.stop()
        observer.join(timeout=3.0)
        print("Watcher stopped.")
