# TUP File Sorter

Production tracking for small animation teams that keeps itself up to date
from the files artists save. It runs on the shared drive the team already
uses, with no server and no accounts.

It began as a file sorter for *The Ugly Princess* (TUP), a hybrid 2D/3D
student short film. The team shared one project folder through cloud storage,
and files kept landing in the wrong place. Feedback got missed, people worked
on outdated versions, nobody was sure what was done, and the director spent
time chasing updates. Tools like ShotGrid address these problems, but only as
well as people remember to update them. On a small team, they mostly don't.

This tool takes a different approach. The sorter already sees every file that
arrives, so it uses the files as evidence:

- a task's first file moves it to *In progress*
- an approval is tied to the version that was approved
- it warns when an approved shot changes, when someone saves a newer version
  after a review was requested, when two people save the same version, and
  when work stalls

The project browser presents all of this in ShotGrid's familiar layout,
in plain words.

## Try it

```bash
python main.py --root ~/tup-demo --demo     # builds a sample project in an empty folder
python main.py --root ~/tup-demo --serve    # opens the project browser
```

The demo recreates one real production problem per shot:

| Shot | Situation |
| --- | --- |
| SH01 | Changed after it was approved |
| SH02 | A newer version arrived after it was sent for review |
| SH03 | A retake nobody has answered |
| SH04 | Two people both saved v004 |
| SH05 | Assigned to you, with new feedback in your Inbox |
| SH06, SH07 | Moved to *In progress* just from files being saved |

## Features

- **Sorting**: files dropped anywhere in the project are moved to the right
  folder, once or continuously (watch mode). Never overwrites, waits for
  files that are still syncing, and previews moves with a dry run.
- **Config-driven rules**: each project's folder structure, naming rules,
  shot list and workflow live in one TOML file, so a new project needs no
  code changes.
- **Version tracking**: every shot and asset's version history, read
  straight from the files on disk.
- **Production tracking**: task statuses, notes, assignments and status
  automations, shared through the synced folder.
- **Keeps itself up to date**: statuses move as files arrive, approvals are
  tied to versions, and problems are flagged.
- **Project browser**: a status grid with thumbnails, image and video
  previews, one-click review actions, an activity feed and an inbox.
- **Desktop app** (Tkinter) and a **command line** for everything.
- **Safe by default**: refuses to run on a folder that isn't set up as a
  project, and never sorts anything whose name starts with `_`.

## Project browser

A web page the app serves from your own computer. Open it with **Open
Project Browser** in the desktop app, or:

```bash
python main.py --root ~/TUP --serve
```

- **Shots & Assets**: one row per shot (grouped by act and sequence) or
  asset, one column per task, each cell a status chip. Chips show plain
  labels (*Waiting*, *In progress*, *In review*, *Retake*, *Approved*), with
  the full name and code on hover. Colours meet WCAG AA contrast, and
  status never relies on colour alone. Tasks that haven't started are drawn
  quietly so progress stands out.
- **Warnings**: a solid ring and `!` marks a problem (version clash,
  changed after approval, newer than what was sent for review). A dashed
  outline and `…` marks a stalled task.
- **Filters**: click statuses in the legend to show only those; **My
  Tasks**, **Needs attention**, and search by name (press `/`).
- **Detail panel**: click a shot or chip. One-click actions follow the
  workflow: **Start working**, **Submit for review**, **Approve**,
  **Request changes** (needs a note) or **Reopen**, plus **Post note** and
  **Assign to me**. A note sent with a button is attached to that change.
  **More options** sets any status or assignee. Warnings explain what to
  do, with a button where there's an obvious fix, such as **Approve newest
  version**.
- **Versions**: every version of every file, marked *latest* and
  *approved* / *sent for review*. Images and H.264 video play in the page,
  and image sequences show their middle frame. **Show in folder** opens the
  file's folder. Formats browsers can't show (EXR, TIFF, scene files) are
  listed without a preview.
- **Activity and Inbox**: a feed of every saved version, status change and
  note, newest first. Changes by others on your tasks that you haven't seen
  are highlighted and counted in your **Inbox**.
- **Live**: other people's changes appear within a few seconds as cloud sync
  brings them in, without refreshing. Files are rescanned every 30 seconds,
  after a sort, or when you press **Refresh**.
- **Friendly**: the first visit asks your name, **?** opens a short guide,
  links like `#SH05/layout` open straight to a task, and if the app is
  closed or a file is locked by the sync app, the page says so plainly.

**Security.** The page is served on `127.0.0.1` only. The link the app opens
carries a one-off secret that the page swaps for a cookie other websites
can't send. The server rejects requests addressed to any other host name
(which blocks DNS rebinding) and writes from any other origin. Media is only
served from inside the project, never from ignored folders.

## Tracking that keeps itself up to date

- **Automatic statuses**: a task's first file moves it from *Waiting* or
  *Ready* to *In progress*, and a new version after a retake moves it back
  to *In progress*. Files never undo a decision someone made.
- **Approvals are of a version**: setting a status records which versions
  existed at that moment, so *Approved* means "v003 approved".
- **Warnings**, most serious first:
  - *Version clash*: two files claim the same version (two people saved
    `v004`, and the sorter kept both).
  - *Changed after approval*: a newer version appeared after sign-off.
  - *Newer than what was sent for review*: the reviewer may be looking at
    an old version.
  - *Stalled*: an active task with no new file or update for `stale_days`,
    or a retake nobody has answered.

All of this is worked out from the files and the notes logs each time, so
nothing extra is stored or synced. It's set up in the project config's
`[auto_status]` and `[health]` sections; remove them to turn it off.

**How it syncs without a server.** Every change is appended to
`_pipeline/events/<user>@<computer>.jsonl`. Each machine only writes its own
file, so cloud sync never has two people editing the same file (which is
what corrupts a shared database). Everyone reads all the files, and the
latest change to a task wins. Duplicate "conflicted copy" files and lines
that are only half synced are handled. Because the order comes from each
computer's clock, keep clocks set automatically.

## Desktop app

```bash
python app.py
```

Pick the **Project folder** (the first time, it offers to set it up), then:

- **Open Project Browser**: the page above.
- **Sort files now**: sort everything once. Tick **Preview only** to see
  where files would go without moving anything.
- **Auto-sort new files**: keep sorting as files arrive (needs `watchdog`).

**Skip the sync check** skips the short wait that makes sure files have
finished uploading. **Work in batches** splits very large folders into
chunks.

## Command line

```bash
python main.py --root /path/to/project --init         # set up a project config
python main.py --root /path/to/project --dry-run      # preview where files would go
python main.py --root /path/to/project                # sort once
python main.py --root /path/to/project --watch        # keep sorting as files arrive
python main.py --root /path/to/project --serve        # project browser
python main.py --root /path/to/project --status       # status grid and warnings
python main.py --root /path/to/project --show SH05    # one shot: tasks, versions, notes, warnings
python main.py --root /path/to/project --report       # latest version of everything
python main.py --root /path/to/project --set-status SH05 layout wfa --comment "Camera locked"
python main.py --root /path/to/project --assign SH05 3d alex
python main.py --root /path/to/project --add-note SH05 layout "Check f40"
python main.py --root /path/to/empty   --demo         # sample project
```

Changes are recorded under your login, or `--user` / `FILE_SORTER_USER`. To
track an asset that has no files yet, add `--entity-type prop`. Other
options: `--quiet`, `--fast-mode` (skip the sync check), `--batch
--batch-size N`, `--port N` and `--no-open` for `--serve`, and `--gui`. The
root can also come from the `FILE_SORTER_ROOT` environment variable.

```text
$ python main.py --root ~/TUP --dry-run
DRY RUN: SH05_layout_v003.ma -> production/Acts/Act_1/SQ_01/SC_05/SH05/layout/SH05_layout_v003.ma
DRY RUN: prop_OilLamp_disp_001.png -> production/assets/3d/props/prop_OilLamp/textures/prop_OilLamp_disp_001.png
DRY RUN: music_theme_v3.wav -> editing/audio/music_theme_v3.wav
DRY RUN: concept_mask_design.psd -> concept_art/misc/concept_mask_design.psd
```

## Project config

Each project has a config at `_pipeline/project.toml`. `--init` (or the
desktop app, the first time you pick a folder) creates one from
`default_project.toml`, which reproduces the TUP structure. The config is
found by searching upward, so you can also point the tool at a subfolder such
as `production/`. It holds:

- **Rules**, checked top to bottom: a regex `pattern`, an `ext` list and/or
  `contains` words, and a `dest` folder with fields such as `{shot_dir}` or
  `{asset}` taken from the filename. Rules can have `cases` for subfolders,
  and `entity`, `entity_type` and `task` for tracking.
- **Shot ranges**: which shots belong to which act and sequence.
- **Statuses**, with Kitsu-style flags (default, done, feedback request,
  retake), an optional short `label` and `color`. The default names follow
  ShotGrid: *Waiting to start*, *Ready to start*, *In progress*, *Pending
  review*, *Retake*, *Approved*.
- **Task types** per entity type, **automations** (for example, when layout is
  approved, 3D and 2D become ready), `[auto_status]` and `[health]`.
- **Version and frame patterns**, **file groups**, **ignore lists**, and the
  **fallback** for unmatched files.

The config is checked when it loads. Typos in keys, unknown fields or
statuses, bad regexes and destinations outside the project are reported by
name, and nothing runs until they're fixed. Watch mode and the project
browser reload the config when it changes.

### Versions

Versions follow ShotGrid's publish convention: a file's name is its filename
with the version removed, so `SH05_layout_v001.ma` and `SH05_layout_v002.ma`
are two versions of `SH05_layout.ma`. Frame numbers become `####`, so a
render sequence is one item. The latest version is the highest number, not
the newest file. When the sorter has to rename a clash
(`SH05_layout_v004_001.ma`), it's recognised as a second file claiming v004.

### Folder logic

With the default config, files go to destinations such as:

- `production/Acts/Act_x/SQ_xx/SC_xx/SHxx/{layout,3d,2d,fx,comp,renders}/`
- `production/assets/3d/{char,props,env}/...`
- `editing/sessions/`, `editing/audio/`, `concept_art/...`, `reference/...`
- `incoming/` for unmatched files in the project root

Files that match a rule are moved wherever they are. Files that match no rule
are only moved to `incoming/` from the project root; anywhere else, they are
left where someone put them. Existing folders are reused when only the case
differs, so `prop_oillamp` and `prop_OilLamp` don't split into two folders.
Anything whose name starts with `_` (`_archive/`, `_do_not_sort.png`) is
never touched.

## Requirements

- Python 3.11+ (tested on 3.13 and 3.15). The standard library covers
  everything else, including the web server.
- `watchdog`, only for watch mode: `pip install watchdog`

A standalone executable can be built with `pyinstaller TUP_File_Sorter.spec`.

Run the tests with:

```bash
python -m unittest discover -s tests
```

## Status

Tested: the full suite (90 tests: sorting, config validation, versions,
tracking, warnings, the web server's security and API, and the demo end to
end). The project browser was also clicked through in a Chromium-based
browser and checked visually in Firefox, on Linux.

Not yet tested:

- **Windows and macOS, with a real OneDrive or Google Drive folder.** In
  particular, the automatic features rely on file modified times surviving
  sync.
- **The standalone executable.**
- **Use on a real production.**

Known limitations:

- The feed can't say who saved a file, only that a version was saved.
- There are no permissions: anyone can set any status.
- Browsers can't preview EXR or TIFF, and Firefox may not play `.mov`.

## Project layout

| File | Purpose |
| --- | --- |
| `app.py` | Desktop app entry point |
| `main.py` | Command-line entry point |
| `gui.py` | Desktop app (Tkinter) |
| `theme.py` | Desktop app colours and styling |
| `webapp.py` | Local web server for the project browser |
| `web/` | Project browser page (HTML, CSS, JavaScript) |
| `project.py` | Loads and validates the project config |
| `default_project.toml` | Default (TUP) project config |
| `rules.py` | Rule engine: where each file goes |
| `filesystem.py` | Safe moves, the sync check, collision renames |
| `watcher.py` | Watch mode (watchdog) |
| `versions.py` | Version index and report |
| `tracking.py` | Statuses, notes, assignments, automations and warnings |
| `demo.py` | Sample project for `--demo` |
| `config.py` | Tool settings (timings, config location) |
| `tests/` | Unit tests |
