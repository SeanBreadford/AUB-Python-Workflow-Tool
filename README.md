# TUP File Sorter

A Python desktop tool that sorts animation production files into a consistent
folder structure.

It was built for *The Ugly Princess* (TUP), a hybrid 2D/3D student short film.
The team shared one project folder through cloud storage, and files kept
landing in the wrong place. The sorter ran on one machine, watched the synced
folder and moved each new file to where it belonged. The cloud sync then
carried the tidy tree back to everyone.

**What makes it different:** production trackers like ShotGrid are only as
accurate as the statuses people remember to click, which on small teams is
not very. This one runs on the shared drive the team already uses (no server,
no accounts) and keeps itself up to date from the files artists save: a new
file starts a task, approvals are tied to a specific version, and it warns
when an approved shot changes, when two people save the same version, or
when work stalls.

## Try it

```bash
python main.py --root ~/tup-demo --demo     # builds a sample project in an empty folder
python main.py --root ~/tup-demo --serve    # opens the project browser
```

The demo recreates real production problems, one per shot: SH01 was changed
after approval, SH02 got a newer version after it was sent for review, SH03
has a retake nobody answered, two people saved SH04's v004, SH05 (assigned
to you) has new feedback, and SH06 and SH07 moved to work in progress just
from files being saved.

## Features

- **GUI** (Tkinter) and **command line**
- **Project browser** (web page): a Kitsu-style status grid of every shot and
  asset, with thumbnails, image and video previews, notes, and status
  changes; runs locally, no server needed
- **Dry run**: preview every move without changing any files
- **Watch mode**: sort automatically as files arrive (needs `watchdog`)
- **Batch mode** for large folders
- **Config-driven rules**: each project's folder structure, naming rules and
  shot list live in one TOML file, so new projects need no code changes
- **Version tracking**: lists the latest version of every shot and asset
  file, read straight from the sorted files
- **Production tracking**: task statuses, notes and assignments per shot
  and asset, with status automations, shared through the synced folder
- **Updates itself from files**: statuses move as files arrive, approvals
  are pinned to versions, and problems are flagged (see below)
- **Activity feed** of every saved version and change, and an **Inbox** of
  feedback on your tasks you haven't seen yet
- **Safe by default**: refuses to run on a folder that isn't set up as a project
- **Shot mapping**: `SHxx` goes to its act, sequence and scene folders
- **Cloud-safe**: waits until a file has stopped changing before moving it,
  and skips temporary and partial sync files
- **Never overwrites**: name collisions are renamed instead
- **Ignore rule**: anything starting with `_` is left alone

## Example

```text
$ python main.py --root ~/TUP --init
Created project config: ~/TUP/_pipeline/project.toml
$ python main.py --root ~/TUP --dry-run
DRY RUN: SH05_layout_v003.ma -> production/Acts/Act_1/SQ_01/SC_05/SH05/layout/SH05_layout_v003.ma
DRY RUN: prop_OilLamp_disp_001.png -> production/assets/3d/props/prop_OilLamp/textures/prop_OilLamp_disp_001.png
DRY RUN: music_theme_v3.wav -> editing/audio/music_theme_v3.wav
DRY RUN: concept_mask_design.psd -> concept_art/misc/concept_mask_design.psd
```

`_do_not_sort.png` in the same folder is skipped.

## Project config

Each project has a config at `_pipeline/project.toml` in its root. `--init`
(or the GUI, the first time you pick a folder) creates one from
`default_project.toml`, which reproduces the TUP structure. The sorter finds
the config by searching upward, so you can also point it at a subfolder such
as `production/`.

The config holds:

- **Rules**, checked top to bottom: a regex `pattern`, an `ext` list and/or
  `contains` words, and a `dest` folder with fields like `{shot_dir}` or
  `{asset}` taken from the filename. Rules can have `cases` for sub-folders.
- **Shot ranges**: which shots belong to which act and sequence
- **File groups** (`model`, `texture`, ...), **ignore lists** and the
  **fallback** for unmatched files

The config is checked when it loads. Typos in keys, unknown fields, bad
regexes and destinations outside the project are reported with the rule
name, and nothing is sorted. Watch mode reloads the config on every change,
so edits apply without a restart.

## Version tracking

`--report` (or the **Versions** list in the project browser) shows what exists for every shot
and asset, without changing anything:

```text
$ python main.py --root ~/TUP --report
== shot (1) ==
SH05  (Act_1 / SQ_01 / SC_05)
  3d        SH05_set.blend                -  2026-11-15 15:07
  layout    SH05_layout.ma             v002  2026-11-15 15:07  2 versions
  renders   SH05_beauty_####.exr       v002  2026-11-15 15:07  3 frames

== prop (1) ==
prop_OilLamp
  model     prop_OilLamp.fbx           v002  2026-11-15 15:07
```

It follows ShotGrid's publish convention. A file's name is its filename with
the version removed, so `SH05_layout_v001.ma` and `SH05_layout_v002.ma` are
two versions of `SH05_layout.ma`, and versions are grouped by entity, then
task, then name. Frame numbers become `####`, so a render sequence is one
item. The latest version is the highest number, not the newest file.

Everything is read from the files on disk, so there is no database to keep in
sync between machines. Which rules are tracked is set in the config with
`entity`, `entity_type` and `task`, and the version and frame patterns are
in its `[versions]` section. Files that belong somewhere but haven't been
sorted yet are counted at the end of the report.

## Production tracking

Each shot and asset has tasks (layout, 3d, comp, ... for shots; model and
textures for assets), and each task has a status, an assignee and notes. The
statuses, task types and automations are set in the project config, using
Kitsu's model:

```text
$ python main.py --root ~/TUP --set-status SH05 layout wfa --comment "Camera locked"
$ python main.py --root ~/TUP --assign SH05 3d alex
$ python main.py --root ~/TUP --status
== shot (54) ==
      layout   3d       2d       fx       comp     renders
SH05  done     ready    ready    todo     todo     todo
SH06  retake   todo     todo     todo     todo     todo
...
Retakes:
  SH06 layout  since 2026-11-15 15:13
```

`--show SH05` lists one shot's tasks with their versions and notes, and
`--add-note SH05 layout "..."` adds a note. Shots come from the shot ranges
and assets from sorted files; to track an asset that has no files yet, add
`--entity-type prop`. Changes are recorded under your login, or under
`--user` / `FILE_SORTER_USER`. With tracking set up, `--report` also shows
each task's status.

The default statuses are `todo`, `ready`, `wip`, `wfa` (waiting for approval),
`retake` and `done`. Automations move the next task along: when a shot's
layout is `done`, its `3d` and `2d` tasks become `ready`, but only if they
haven't started yet.

**How it syncs without a server.** Every change is appended to
`_pipeline/events/<user>@<computer>.jsonl`. Each machine only writes its own
file, so cloud sync never has two people editing the same file, which is
what corrupts a shared database. Everyone reads all the files, and the
latest change to a task wins. Duplicate "conflicted copy" files and lines
that are only half synced are handled. Because the order comes from each
computer's clock, keep clocks set automatically.

## Tracking that keeps itself up to date

The sorter already sees every file artists save, so the tracker uses them as
evidence instead of relying on people to update statuses:

- **Automatic statuses.** A task's first file moves it from `todo`/`ready`
  to `wip`, and a new version after a retake moves it back to `wip`. Files
  never undo a decision someone made.
- **Approvals are of a version.** Setting a status records which versions
  existed at that moment, so "approved" means "v003 approved".
- **Warnings**, most serious first:
  - *Version clash*: two files claim the same version (two people saved
    `v004`; the sorter kept both).
  - *Changed after approval*: a newer version appeared after sign-off.
  - *Newer than what was sent for review*: the reviewer may be looking at
    an old version.
  - *Stalled*: an active task with no new file or update for
    `stale_days`, or a retake nobody has answered.
- **Activity feed** of files, status changes and notes, and **New for
  you**: changes by others on tasks assigned to you since you last looked.

All of this is worked out from the files and the event logs each time, so
nothing extra is stored or synced. It's configured in the project config's
`[auto_status]` and `[health]` sections; remove them to turn it off.
`--status` lists the warnings too.

## Project browser

The project browser is a web page that the app serves from your own computer.
Open it with the **Project Browser** button in the desktop app, or:

```bash
python main.py --root ~/TUP --serve
```

- **Status grid:** one row per shot (grouped by act and sequence) or asset,
  one column per task, each cell a status chip. Every chip shows its status
  as text as well as colour, and the colours meet WCAG AA contrast. Tasks
  that haven't started are drawn quietly so progress stands out.
- **Filters:** click statuses in the legend to show only, say, retakes;
  search by name (press `/`).
- **Warnings:** a solid ring and `!` marks a problem (clash, changed after
  approval, newer than submitted); a dashed outline and `…` marks a stalled
  task. **Needs attention** and **My Tasks** filter the grid.
- **Activity:** the feed of everything that happened, newest first.
  Feedback on your tasks you haven't seen is highlighted and counted in your
  **Inbox**.
- **Detail panel:** click a shot or a chip to see its tasks. One-click
  actions follow the workflow: **Start working**, **Submit for review**,
  **Approve**, **Request changes** (needs a note), **Reopen**, plus **Post
  note** and **Assign to me**. A note sent with a button is attached to that
  change, like a review comment. **More options** sets any status or assignee.
- **Warnings say what to do**, with a button where there's an obvious fix,
  such as **Approve newest version** after a change.
- **Plain words, ShotGrid layout.** Chips show short labels (*In review*,
  *Retake*, *Approved*), with the full name and code on hover. The views use
  ShotGrid's terms: **My Tasks**, **Inbox**, **Activity**, **Notes**,
  **Versions**. The first visit asks your name, and **?** opens a short guide.
- **Versions:** every version of every file, with previews. Images and
  H.264 video play in the page; image sequences show their middle frame.
  **Show in folder** opens the file's folder. Formats browsers can't show
  (EXR, TIFF, scene files) are listed without a preview.
- **Live:** other people's changes appear within a few seconds as cloud sync
  brings them in. The page rescans files every 30 seconds, after a sort,
  or when you press **Refresh**.
- Links like `#SH05/layout` open straight to a task.
- If the app is closed or restarted, the page says so in a banner instead of
  failing silently, and errors such as a file locked by the sync app are
  shown with what went wrong.

Status colours and chip labels can be set per status with
`color = "#rrggbb"` and `label = "..."` in the project config.

**Security.** The page is served on `127.0.0.1` only. The link the app opens
carries a one-off secret that the page swaps for a cookie other websites
can't send, and the server rejects requests addressed to any other host name
(which blocks DNS rebinding) or writes from any other origin. Media is only
served from inside the project, never from ignored folders.

## Folder logic

With the default config, files are routed to destinations such as:

- `production/Acts/Act_x/SQ_xx/SC_xx/SHxx/{layout,3d,2d,fx,comp,renders}/`
- `production/assets/3d/{char,props,env}/...`
- `editing/sessions/`, `editing/audio/`
- `concept_art/...`
- `reference/...`
- `incoming/...` (fallback for unmatched files in the project root)

Files that match a rule are moved wherever they are. Files that match no
rule are only moved to `incoming/` if they are in the project root;
anywhere else they are left where someone put them.

Existing folders are reused when only the case differs, so `prop_oillamp`
and `prop_OilLamp` don't split into two folders.

Anything whose name starts with `_` is ignored by both the sorter and the
watcher, for example `_archive/`, `_do_not_sort.png` or
`Act_2/_old_versions/`.

## Requirements

- Python 3.11+ (tested on 3.13 and 3.15)
- `watchdog` (only for watch mode)

```bash
pip install watchdog
```

## Usage

```bash
python app.py                                  # open the GUI
python main.py --root /path/to/project --init      # set up a project config
python main.py --root /path/to/project --dry-run   # preview
python main.py --root /path/to/project             # sort once
python main.py --root /path/to/project --report    # latest versions per shot/asset
python main.py --root /path/to/project --status    # task status grid
python main.py --root /path/to/project --show SH05 # one shot: tasks, versions, notes
python main.py --root /path/to/project --set-status SH05 layout wip
python main.py --root /path/to/project --serve     # project browser web page
python main.py --root /path/to/empty --demo        # sample project to try it on
python main.py --root /path/to/project --watch     # keep sorting as files arrive
python main.py --root /path/to/project --batch --batch-size 50
```

Other options: `--quiet` (less logging), `--fast-mode` (skip the cloud-safety
wait), `--gui`. The root can also be set with the
`FILE_SORTER_ROOT` environment variable.

A standalone executable can be built with PyInstaller:

```bash
pyinstaller TUP_File_Sorter.spec
```

Run the tests with:

```bash
python -m unittest discover -s tests
```


## Project layout

| File            | Purpose                                         |
| --------------- | ----------------------------------------------- |
| `app.py`        | GUI entry point                                 |
| `main.py`       | Command-line entry point                        |
| `gui.py`        | Tkinter interface                               |
| `webapp.py`     | Local web server for the project browser        |
| `web/`          | Project browser page (HTML, CSS, JavaScript)    |
| `theme.py`      | Desktop app colours and styling                 |
| `rules.py`      | Rule engine: where each file goes               |
| `project.py`    | Loads and validates the project config          |
| `versions.py`   | Version index and report                        |
| `demo.py`       | Sample project for `--demo`                     |
| `tracking.py`   | Task statuses, notes, assignments (event logs)  |
| `default_project.toml` | Default (TUP) project config             |
| `filesystem.py` | Safe moves, stability checks, collision renames |
| `watcher.py`    | Watch mode (watchdog)                           |
| `config.py`     | Tool settings (timings, config location)        |
| `tests/`        | Unit tests                                      |
