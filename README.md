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
- **Rule-based routing** by filename and extension
- **Shot mapping**: `SHxx` goes to its act, sequence and scene folders
- **Cloud-safe**: waits until a file has stopped changing before moving it,
  and skips temporary and partial sync files
- **Never overwrites**: name collisions are renamed instead
- **Ignore rule**: anything starting with `_` is left alone

## Example

```text
$ python main.py --root ~/TUP --dry-run
DRY RUN: SH05_layout_v003.ma -> production/Acts/Act_1/SQ_01/SC_05/SH05/layout/SH05_layout_v003.ma
DRY RUN: prop_OilLamp_disp_001.png -> production/assets/3d/props/prop_OilLamp/textures/prop_OilLamp_disp_001.png
DRY RUN: music_theme_v3.wav -> editing/audio/music_theme_v3.wav
DRY RUN: concept_mask_design.psd -> concept_art/misc/concept_mask_design.psd
```

`_do_not_sort.png` in the same folder is skipped.

## Folder logic

Files are routed to destinations such as:

- `production/Acts/Act_x/SQ_xx/SC_xx/SHxx/{layout,3d,2d,fx,comp,renders}/`
- `production/assets/3d/{char,props,env}/...`
- `editing/sessions/`, `editing/audio/`
- `concept_art/...`
- `reference/...`
- `incoming/...` (fallback when no rule matches)

The shot-to-scene structure is set in `config.py` (`SHOT_MAPPING`), along
with the ignore lists, file groups and stability-check timings.

Anything whose name starts with `_` is ignored by both the sorter and the
watcher, for example `_archive/`, `_do_not_sort.png` or
`Act_2/_old_versions/`.

## Requirements

- Python 3.10+ (tested on 3.13)
- `watchdog` (only for watch mode)

```bash
pip install watchdog
```

## Usage

```bash
python app.py                                  # open the GUI
python main.py --root /path/to/project --dry-run   # preview
python main.py --root /path/to/project             # sort once
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

## Project layout

| File            | Purpose                                         |
| --------------- | ----------------------------------------------- |
| `app.py`        | GUI entry point                                 |
| `main.py`       | Command-line entry point                        |
| `gui.py`        | Tkinter interface                               |
| `rules.py`      | Routing rules: where each file goes             |
| `filesystem.py` | Safe moves, stability checks, collision renames |
| `watcher.py`    | Watch mode (watchdog)                           |
| `config.py`     | Project settings and shot mapping               |
