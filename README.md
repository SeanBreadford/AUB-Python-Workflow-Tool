\# TUP File Sorter



A Python desktop app for sorting animation production files into a consistent folder structure.



Built for a student short film pipeline with:

\- Act / Sequence / Scene / Shot mapping

\- Rule-based sorting for assets, shots, references, and editing files

\- Cloud-storage-friendly behavior (OneDrive-safe checks)

\- Optional watch mode for automatic sorting

\- Dry run mode for safe previews



---



\## Features



\- \*\*GUI app\*\* (Tkinter) for easy use

\- \*\*Dry Run\*\* mode (preview moves without changing files)

\- \*\*Watch Mode\*\* (auto-sort when files change)

\- \*\*Rule-based routing\*\* by filename + extension

\- \*\*Shot mapping\*\* (`SHxx -> Act/SQ/SC`)

\- \*\*Ignore rules\*\* for files/folders that start with `\_`

\- \*\*Collision handling\*\* (auto-rename instead of overwrite)

\- \*\*Cloud-safe processing\*\* with stability checks and retries



---



\## Folder Logic (High Level)



The sorter routes files to destinations such as:



\- `production/Acts/Act\_x/SQ\_xx/SC\_xx/SHxx/...`

\- `production/assets/3d/{char|props|env}/...`

\- `editing/sessions`, `editing/audio`

\- `concept\_art/...`

\- `reference/...`

\- `incoming/...` (fallback if no rule matches)



Shot/scene structure is controlled in `config.py` via `SHOT\_MAPPING`.



---



\## Ignore Behavior



Any file/folder starting with `\_` is ignored by sorter and watcher.



Examples:

\- `\_archive/`

\- `\_do\_not\_sort.png`

\- `Act\_2/\_old\_versions/...`



---



\## Requirements



\- Python 3.10+ (tested on Python 3.13)

\- `watchdog`



Install:



```bash

pip install watchdog



