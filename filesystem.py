from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from config import (
    IGNORE_DIRS,
    IGNORED_NAME_PARTS,
    IGNORED_PREFIXES,
    STABILITY_RETRIES,
    STABILITY_RETRY_DELAY,
    STABILITY_SAMPLE_DELAY,
)
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
def is_ignored_file(path: Path) -> bool:
    lower_name = path.name.lower()
    if lower_name.startswith(IGNORED_PREFIXES):
        return True
    return any(part.lower() in lower_name for part in IGNORED_NAME_PARTS)


def is_in_ignored_dir(path: Path, root: Path) -> bool:
    try:
        rel_parts = path.resolve().relative_to(root.resolve()).parts
    except Exception:
        rel_parts = path.parts

    ignore_lower = {name.lower() for name in IGNORE_DIRS}
    for part in rel_parts:
        part_lower = part.lower()
        if part_lower in ignore_lower:
            return True
        if part.startswith("_"):
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
def iter_files(root: Path) -> Iterable[Path]:
    root = root.resolve()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if is_in_ignored_dir(path, root):
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
def process_file(
    src: Path,
    root: Path,
    dry_run: bool,
    quiet: bool,
    fast_mode: bool = False,
) -> None:
    root = root.resolve()

    try:
        rel = src.resolve().relative_to(root)
    except ValueError:
        log(f"SKIP (outside root): {src}", quiet)
        _emit("skipped")
        return

    # Watch mode passes files directly, so ignored dirs must be checked here too
    if is_ignored_file(src) or is_in_ignored_dir(src, root):
        log(f"SKIP (ignored): {rel}", quiet)
        _emit("skipped")
        return

    dest = choose_destination(src, root)
    if dest is None:
        log(f"SKIP (no rule): {rel}", quiet)
        _emit("skipped")
        return

    src_resolved = src.resolve()
    dest_resolved = dest.resolve(strict=False)

    if src_resolved == dest_resolved:
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
        try:
            new_rel = final_dest.relative_to(root)
        except ValueError:
            new_rel = final_dest
        log(f"COLLISION (renamed): {new_rel}", quiet)

    if dry_run:
        try:
            to_rel = final_dest.relative_to(root)
        except ValueError:
            to_rel = final_dest
        log(f"DRY RUN: {rel} -> {to_rel}", quiet)
        _emit("processed")
        return

    final_dest.parent.mkdir(parents=True, exist_ok=True)

    moved = robust_move(src, final_dest)
    if moved:
        try:
            to_rel = final_dest.relative_to(root)
        except ValueError:
            to_rel = final_dest
        log(f"MOVED: {rel} -> {to_rel}", quiet)
        _emit("moved")
        _emit("processed")
    else:
        log(f"ERROR (move failed): {rel}", quiet)
        _emit("errors")
        _emit("processed")


def sort_project(
    root: Path,
    dry_run: bool = False,
    quiet: bool = False,
    files: Optional[Iterable[Path]] = None,
    fast_mode: bool = False,
) -> None:
    root = Path(root).expanduser().resolve()
    targets = list(files) if files is not None else list(iter_files(root))
    log(f"Sorting root: {root}", quiet)

    for src in targets:
        try:
            process_file(
                src=Path(src),
                root=root,
                dry_run=dry_run,
                quiet=quiet,
                fast_mode=fast_mode,
            )
        except Exception as error:
            log(f"ERROR ({Path(src).name}): {error}", quiet)
            _emit("errors")


def sort_project_batch(
    root: Path,
    dry_run: bool = False,
    quiet: bool = False,
    batch_size: int = 50,
    fast_mode: bool = False,
    inter_batch_delay: float = 1.0,
) -> None:
    root = Path(root).expanduser().resolve()
    all_files = list(iter_files(root))
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

        for src in batch:
            try:
                process_file(
                    src=src,
                    root=root,
                    dry_run=dry_run,
                    quiet=quiet,
                    fast_mode=fast_mode,
                )
            except Exception as error:
                log(f"ERROR ({src.name}): {error}", quiet)
                _emit("errors")

        if not dry_run and end < total:
            time.sleep(inter_batch_delay)

    log("Batch sort complete.", quiet)
