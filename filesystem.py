from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from config import (
    CONFIG_DIR_NAME,
    INTER_BATCH_DELAY,
    STABILITY_RETRIES,
    STABILITY_RETRY_DELAY,
    STABILITY_SAMPLE_DELAY,
)
from project import Project, load_project
from rules import choose_destination

# Callback Config
operation_callback: Optional[Callable[[str], None]] = None


def set_operation_callback(callback: Optional[Callable[[str], None]]) -> None:
    global operation_callback
    operation_callback = callback


def _emit(op: str) -> None:
    if operation_callback:
        operation_callback(op)


# Log Helpers
def log(message: str, quiet: bool = False) -> None:
    if not quiet:
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


def is_file_stable(path: Path, retries: int = STABILITY_RETRIES) -> bool:
    """
    Checks if a file is not changing in size and can be read.
    Useful for cloud sync folders.
    """
    if not path.exists() or not path.is_file():
        return False

    for _ in range(retries):
        try:
            size1 = path.stat().st_size
            time.sleep(STABILITY_SAMPLE_DELAY)
            size2 = path.stat().st_size
            if size1 == size2 and size1 >= 0:
                with path.open("rb") as handle:
                    handle.read(1)
                return True
        except OSError:
            pass
        time.sleep(STABILITY_RETRY_DELAY)
    return False


def is_file_accessible(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            handle.read(1)
        return True
    except OSError:
        return False


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
def robust_move(src: Path, dest: Path, retries: int = 3) -> bool:
    for attempt in range(retries):
        try:
            shutil.move(str(src), str(dest))
            return True
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            return False
        except OSError:
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            return False
    return False


# Core Processing
def _rel(path: Path, project: Project) -> Path:
    try:
        return path.relative_to(project.root)
    except ValueError:
        return path


def _already_sorted(src: Path, dest: Path) -> bool:
    # samefile also catches case-only differences on Windows and macOS
    if dest.exists():
        try:
            return src.samefile(dest)
        except OSError:
            return False
    return src.resolve() == dest.resolve(strict=False)


def process_file(
    src: Path,
    project: Project,
    dry_run: bool,
    quiet: bool,
    fast_mode: bool = False,
) -> None:
    try:
        rel = src.resolve().relative_to(project.root)
    except ValueError:
        log(f"SKIP (outside project): {src}", quiet)
        _emit("skipped")
        return

    # Watch mode passes files directly, so ignored dirs must be checked here too
    if is_ignored_file(src, project) or is_in_ignored_dir(src, project):
        log(f"SKIP (ignored): {rel}", quiet)
        _emit("skipped")
        return

    dest = choose_destination(src, project)
    if dest is None:
        log(f"SKIP (no rule): {rel}", quiet)
        _emit("skipped")
        return

    if _already_sorted(src, dest):
        log(f"OK (already sorted): {rel}", quiet)
        _emit("skipped")
        return

    # Cloud safety checks (dry run moves nothing, so skip the slow check)
    if fast_mode or dry_run:
        ready = is_file_accessible(src)
    else:
        ready = is_file_stable(src)

    if not ready:
        log(f"DEFER (not ready): {rel}", quiet)
        _emit("skipped")
        return

    final_dest = dest
    if final_dest.exists():
        final_dest = resolve_collision(final_dest)
        log(f"COLLISION (renamed): {_rel(final_dest, project)}", quiet)

    if dry_run:
        log(f"DRY RUN: {rel} -> {_rel(final_dest, project)}", quiet)
        _emit("processed")
        return

    final_dest.parent.mkdir(parents=True, exist_ok=True)

    moved = robust_move(src, final_dest)
    if moved:
        log(f"MOVED: {rel} -> {_rel(final_dest, project)}", quiet)
        _emit("moved")
        _emit("processed")
    else:
        log(f"ERROR (move failed): {rel}", quiet)
        _emit("errors")
        _emit("processed")


def _process_all(
    targets: Iterable[Path],
    project: Project,
    dry_run: bool,
    quiet: bool,
    fast_mode: bool,
) -> None:
    for src in targets:
        try:
            process_file(
                src=Path(src),
                project=project,
                dry_run=dry_run,
                quiet=quiet,
                fast_mode=fast_mode,
            )
        except Exception as error:
            log(f"ERROR ({Path(src).name}): {error}", quiet)
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
