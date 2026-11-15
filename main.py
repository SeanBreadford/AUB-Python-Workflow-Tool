from __future__ import annotations

import argparse
from pathlib import Path

from config import DEFAULT_BATCH_SIZE, ROOT
from filesystem import sort_project, sort_project_batch
from project import ProjectConfigError, ProjectNotFoundError, init_project, load_project
from tracking import TrackingError, build_tracker, format_entity, format_status_grid, record_event
from versions import format_report, scan_versions

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
        "--init",
        action="store_true",
        help="Create a default project config (_pipeline/project.toml) in --root",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="List the latest version of everything per shot and asset (changes nothing)",
    )
    web = parser.add_argument_group("project browser")
    web.add_argument(
        "--serve",
        action="store_true",
        help="Open the project browser web page (runs locally until Ctrl+C)",
    )
    web.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port for --serve (default: any free port)",
    )
    web.add_argument(
        "--no-open",
        action="store_true",
        help="With --serve, print the link instead of opening a browser",
    )
    tracking = parser.add_argument_group("production tracking")
    tracking.add_argument(
        "--status",
        action="store_true",
        help="Show the task status grid for every shot and asset",
    )
    tracking.add_argument(
        "--show",
        metavar="ENTITY",
        help="Show one shot or asset: task statuses, versions and notes",
    )
    tracking.add_argument(
        "--set-status",
        nargs=3,
        metavar=("ENTITY", "TASK", "STATUS"),
        help="Set a task's status, e.g. --set-status SH05 layout wip",
    )
    tracking.add_argument(
        "--comment",
        help="Note to attach to --set-status",
    )
    tracking.add_argument(
        "--add-note",
        nargs=3,
        metavar=("ENTITY", "TASK", "TEXT"),
        help="Add a note to a task",
    )
    tracking.add_argument(
        "--assign",
        nargs=3,
        metavar=("ENTITY", "TASK", "USER"),
        help="Assign a task to someone ('-' to unassign)",
    )
    tracking.add_argument(
        "--entity-type",
        help="Type of a shot or asset that has no files yet, e.g. prop",
    )
    tracking.add_argument(
        "--user",
        help="Name to record changes under (default: FILE_SORTER_USER or your login)",
    )
    parser.add_argument(
        "--fast-mode",
        action="store_true",
        help="Skip full stability checks (useful in watch mode)",
    )

    return parser


# Tracking Commands
def run_tracking(args: argparse.Namespace, project) -> None:
    # Scan the whole project so every shot and asset with files is known
    index = scan_versions(project)
    tracker = build_tracker(project, index)
    common = {"user": args.user, "entity_type": args.entity_type}

    if args.set_status:
        entity, task, status = args.set_status
        event = record_event(tracker, "status", entity, task, status=status, note=args.comment, **common)
        print(f"{event['entity']} {task}: {event['status']}")
    if args.add_note:
        entity, task, text = args.add_note
        event = record_event(tracker, "note", entity, task, text=text, **common)
        print(f"{event['entity']} {task}: note added")
    if args.assign:
        entity, task, assignee = args.assign
        assignee = None if assignee == "-" else assignee
        event = record_event(tracker, "assign", entity, task, assignee=assignee, **common)
        print(f"{event['entity']} {task}: " + (f"assigned to {assignee}" if assignee else "unassigned"))

    if args.status or args.show:
        if args.set_status or args.add_note or args.assign:
            tracker = build_tracker(project, index)  # include the change just made
        if args.status:
            print(format_status_grid(tracker))
        if args.show:
            print(format_entity(tracker, index, args.show))


# CLI Entry
def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.gui:
        from gui import main as run_gui
        run_gui()
        return

    root = Path(args.root).expanduser().resolve()

    if args.init:
        try:
            path = init_project(root)
        except (FileExistsError, FileNotFoundError) as error:
            raise SystemExit(str(error))
        print(f"Created project config: {path}")
        print("Review the rules in it, then preview with --dry-run.")
        return

    # Fail early with a clear message instead of sorting an unconfigured folder
    try:
        project = load_project(root)
    except (ProjectNotFoundError, ProjectConfigError) as error:
        raise SystemExit(f"Error: {error}")

    if args.serve:
        from webapp import serve

        serve(project.root, port=args.port, open_browser=not args.no_open)
        return

    if args.report:
        index = scan_versions(project, root)
        tracker = build_tracker(project, index) if project.statuses else None
        print(format_report(index, tracker))
        return

    if any((args.status, args.show, args.set_status, args.add_note, args.assign)):
        try:
            run_tracking(args, project)
        except TrackingError as error:
            raise SystemExit(f"Error: {error}")
        return

    if not args.quiet:
        print(f"Project: {project.name} ({project.config_path})")

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
