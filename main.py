from __future__ import annotations

import argparse
from pathlib import Path

from config import DEFAULT_BATCH_SIZE, ROOT
from filesystem import sort_project, sort_project_batch

try:
    from watcher import watch_project
    WATCH_AVAILABLE = True
    WATCH_IMPORT_ERROR = None
except Exception as error:  # pragma: no cover
    WATCH_AVAILABLE = False
    WATCH_IMPORT_ERROR = error


# CLI Config
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Production File Organiser")

    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Project root folder (default from config.py)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview moves without changing files",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce terminal logging",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Run batch sorting mode",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Files per batch (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch folder and sort on changes (requires watchdog)",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Open GUI",
    )
    parser.add_argument(
        "--fast-mode",
        action="store_true",
        help="Skip full stability checks (useful in watch mode)",
    )

    return parser


# CLI Entry
def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.gui:
        from gui import main as run_gui
        run_gui()
        return

    root = Path(args.root).expanduser().resolve()

    if args.watch:
        if not WATCH_AVAILABLE:
            print("Watch mode is not available.")
            print("Install dependency: pip install watchdog")
            if WATCH_IMPORT_ERROR:
                print(f"Import error: {WATCH_IMPORT_ERROR}")
            return
        watch_project(
            root=root,
            dry_run=args.dry_run,
            quiet=args.quiet,
            fast_mode=True,
        )
        return

    if args.batch:
        sort_project_batch(
            root=root,
            dry_run=args.dry_run,
            quiet=args.quiet,
            batch_size=max(1, args.batch_size),
            fast_mode=args.fast_mode,
        )
    else:
        sort_project(
            root=root,
            dry_run=args.dry_run,
            quiet=args.quiet,
            fast_mode=args.fast_mode,
        )


if __name__ == "__main__":
    main()
