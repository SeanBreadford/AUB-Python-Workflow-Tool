# TUP File Sorter

A Python desktop tool that sorts animation production files into a consistent
folder structure.

It was built for *The Ugly Princess* (TUP), a hybrid 2D/3D student short film.
The team shared one project folder through cloud storage, and files kept
landing in the wrong place. The sorter ran on one machine, watched the synced
folder and moved each new file to where it belonged. The cloud sync then
carried the tidy tree back to everyone.

## Features

- **GUI** (Tkinter) and **command line**
- **Dry run**: preview every move without changing any files
- **Watch mode**: sort automatically as files arrive (needs `watchdog`)
- **Batch mode** for large folders
- **Config-driven rules**: each project's folder structure, naming rules and
  shot list live in one TOML file, so new projects need no code changes
- **Version tracking**: lists the latest version of every shot and asset
  file, read straight from the sorted files
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

`--report` (or **Version Report** in the GUI) lists what exists for every shot
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
python main.py --root /path/to/project --watch     # keep sorting as files arrive
python main.py --root /path/to/project --batch --batch-size 50
```

Other options: `--quiet` (less logging), `--fast-mode` (skip the full
stability check), `--gui`. The root can also be set with the
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
| `rules.py`      | Rule engine: where each file goes               |
| `project.py`    | Loads and validates the project config          |
| `versions.py`   | Version index and report                        |
| `default_project.toml` | Default (TUP) project config             |
| `filesystem.py` | Safe moves, stability checks, collision renames |
| `watcher.py`    | Watch mode (watchdog)                           |
| `config.py`     | Tool settings (timings, config location)        |
| `tests/`        | Unit tests                                      |
