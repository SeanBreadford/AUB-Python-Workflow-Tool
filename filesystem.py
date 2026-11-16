from __future__ import annotations

import shutil
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from config import CONFIG_DIR_NAME, INTER_BATCH_DELAY, STABILITY_SAMPLE_DELAY
from project import Project, load_project
from rules import choose_destination

# Reporting
# By default sorting prints its log. A Reporter set for the current thread
# (see reporting()) receives the log and the per-file outcomes instead, so a
# sort run by the web server gets its own log and counts even while
# auto-sort runs in another thread.
class Reporter:
    def log(self, message: str) -> None:
        print(message)

    def op(self, kind: str) -> None:
        """kind: processed, moved, skipped or errors."""


_reporter: ContextVar[Reporter | None] = ContextVar("sort_reporter", default=None)


@contextmanager
def reporting(reporter: Reporter):
    token = _reporter.set(reporter)
    try:
        yield reporter
    finally:
        _reporter.reset(token)


def _emit(op: str) -> None:
    reporter = _reporter.get()
    if reporter is not None:
        reporter.op(op)


# Log Helpers
def log(message: str, quiet: bool = False) -> None:
    reporter = _reporter.get()
    if reporter is not None:
        reporter.log(message)  # the reporter decides what to show
    elif not quiet:
        print(message)


# Safety Helpers
def is_ignored_file(path: Path, project: Project) -> bool:
    lower_name = path.name.lower()
    if path.name.startswith(project.ignore_prefixes):
        return True
    return any(part in lower_name for part in project.ignore_name_parts)


def is_in_ignored_dir(path: Path, project: Project) -> bool:
    try:
        rel_parts = path.resolve().relative_to(project.root).parts[:-1]
    except ValueError:
        rel_parts = path.parent.parts

    for part in rel_parts:
        if part == CONFIG_DIR_NAME or part.lower() in project.ignore_dirs:
            return True
        if part.startswith(project.ignore_prefixes):
            return True
    return False


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def split_stable(paths: Iterable[Path], delay: float = STABILITY_SAMPLE_DELAY) -> tuple[list[Path], list[Path]]:
    """
    Cloud safety check for many files at once: note every file's size and
    modified time, wait once, and look again. Files that didn't change are
    safe to move; the rest are still being written or synced.

    Only metadata is read, never file contents: opening an online-only
    OneDrive / Google Drive file can make it download.
    """
    paths = list(paths)
    before = {path: _signature(path) for path in paths}
    if paths and delay > 0:
        time.sleep(delay)
    stable, busy = [], []
    for path in paths:
        now = _signature(path)
        if now is None:
            continue  # gone (moved or deleted by someone else); nothing to do
        (stable if now == before[path] else busy).append(path)
    return stable, busy


# Collision Helpers
def resolve_collision(dest: Path) -> Path:
    parent = dest.parent
    stem = dest.stem
    suffix = dest.suffix
    index = 1
    while True:
        candidate = parent / f"{stem}_{index:03d}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


# File Iteration
def iter_files(root: Path, project: Project) -> Iterable[Path]:
    root = root.resolve()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if is_in_ignored_dir(path, project):
            continue
        yield path


# Move Helpers
MOVE_RETRY_DELAY = 1.5


class FileInUseError(Exception):
    """The file stayed locked (open in an app, or by the sync client)."""


def robust_move(src: Path, dest: Path, retries: int = 3) -> None:
    """Move with retries. Raises FileInUseError if the file stays locked."""
    for attempt in range(retries):
        try:
            shutil.move(str(src), str(dest))
            return
        except PermissionError as error:
            if attempt == retries - 1:
                raise FileInUseError(str(error)) from error
        except OSError:
            if attempt == retries - 1:
                raise
        time.sleep(MOVE_RETRY_DELAY * (attempt + 1))


# Core Processing
def _rel(path: Path, project: Project) -> Path:
    try:
        return path.relative_to(project.root)
    except ValueError:
        return path


def is_already_sorted(src: Path, dest: Path) -> bool:
    # samefile also catches case-only differences on Windows and macOS
    if dest.exists():
        try:
            return src.samefile(dest)
        except OSError:
            return False
    return src.resolve() == dest.resolve(strict=False)


@dataclass(frozen=True)
class PlannedMove:
    src: Path
    rel: Path
    dest: Path


def plan_file(src: Path, project: Project, quiet: bool) -> PlannedMove | None:
    """Decide where a file goes, logging why if it stays put."""
    try:
        rel = src.resolve().relative_to(project.root)
    except ValueError:
        log(f"SKIP (outside project): {src}", quiet)
        _emit("skipped")
        return None

    # Watch mode passes files directly, so ignored dirs must be checked here too
    if is_ignored_file(src, project) or is_in_ignored_dir(src, project):
        log(f"SKIP (ignored): {rel}", quiet)
        _emit("skipped")
        return None

    dest = choose_destination(src, project)
    if dest is None:
        log(f"SKIP (no rule): {rel}", quiet)
        _emit("skipped")
        return None

    if is_already_sorted(src, dest):
        log(f"OK (already sorted): {rel}", quiet)
        _emit("skipped")
        return None

    return PlannedMove(src, rel, dest)


def move_file(plan: PlannedMove, project: Project, dry_run: bool, quiet: bool) -> None:
    final_dest = plan.dest
    if final_dest.exists():
        final_dest = resolve_collision(final_dest)
        log(f"COLLISION (renamed): {_rel(final_dest, project)}", quiet)

    if dry_run:
        log(f"DRY RUN: {plan.rel} -> {_rel(final_dest, project)}", quiet)
        _emit("processed")
        return

    final_dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        robust_move(plan.src, final_dest)
    except FileInUseError:
        log(f"DEFER (in use, will retry next sort): {plan.rel}", quiet)
        _emit("skipped")
        return
    except OSError as error:
        log(f"ERROR (move failed): {plan.rel}: {error}", quiet)
        _emit("errors")
        _emit("processed")
        return
    log(f"MOVED: {plan.rel} -> {_rel(final_dest, project)}", quiet)
    _emit("moved")
    _emit("processed")


def _process_all(
    targets: Iterable[Path],
    project: Project,
    dry_run: bool,
    quiet: bool,
    fast_mode: bool,
) -> None:
    plans: list[PlannedMove] = []
    for src in targets:
        try:
            plan = plan_file(Path(src), project, quiet)
        except Exception as error:
            log(f"ERROR ({Path(src).name}): {error}", quiet)
            _emit("errors")
            continue
        if plan is not None:
            plans.append(plan)

    # One shared wait for the whole batch instead of one per file. Dry runs
    # move nothing and fast mode skips the wait, so neither needs it.
    if plans and not dry_run and not fast_mode:
        stable, busy = split_stable(plan.src for plan in plans)
        ready = set(stable)
        for plan in plans:
            if plan.src not in ready and plan.src in busy:
                log(f"DEFER (still changing): {plan.rel}", quiet)
                _emit("skipped")
        plans = [plan for plan in plans if plan.src in ready]

    for plan in plans:
        try:
            move_file(plan, project, dry_run, quiet)
        except Exception as error:
            log(f"ERROR ({plan.src.name}): {error}", quiet)
            _emit("errors")


def sort_project(
    root: Path,
    dry_run: bool = False,
    quiet: bool = False,
    files: Optional[Iterable[Path]] = None,
    fast_mode: bool = False,
) -> None:
    """
    Sort every file under root (or just `files`). The project config is
    looked up from root, so root can be the project or any folder inside it.
    Raises ProjectNotFoundError / ProjectConfigError if there is no valid config.
    """
    root = Path(root).expanduser().resolve()
    project = load_project(root)
    targets = list(files) if files is not None else list(iter_files(root, project))
    log(f"Sorting root: {root}", quiet)
    _process_all(targets, project, dry_run, quiet, fast_mode)


def sort_project_batch(
    root: Path,
    dry_run: bool = False,
    quiet: bool = False,
    batch_size: int = 50,
    fast_mode: bool = False,
    inter_batch_delay: float = INTER_BATCH_DELAY,
) -> None:
    root = Path(root).expanduser().resolve()
    project = load_project(root)
    all_files = list(iter_files(root, project))
    total = len(all_files)

    if total == 0:
        log("No files found.", quiet)
        return

    log(f"Batch sort start: {total} files", quiet)

    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        batch = all_files[start:end]
        batch_index = (start // batch_size) + 1
        batch_total = (total + batch_size - 1) // batch_size

        log(f"Batch {batch_index}/{batch_total}: {len(batch)} files", quiet)
        _process_all(batch, project, dry_run, quiet, fast_mode)

        if not dry_run and end < total:
            time.sleep(inter_batch_delay)

    log("Batch sort complete.", quiet)
