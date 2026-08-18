from __future__ import annotations

from pathlib import Path
import os

# Project Config
# Change this to your production root if needed.
# You can also set FILE_SORTER_ROOT in your environment.
DEFAULT_ROOT = Path.cwd()
ROOT = Path(os.getenv("FILE_SORTER_ROOT", str(DEFAULT_ROOT))).expanduser().resolve()

# Ignore Config
IGNORE_DIRS = {
    ".git",
    "__pycache__",
    ".idea",
    ".vscode",
    ".vs",
    ".history",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "temp",
    "tmp",
    "cache",
    "backup",
    "backups",
    "archive",
    "old",
    "OLD",
    "autosave",
    "render_temp",
}

IGNORED_NAME_PARTS = (
    ".tmp",
    ".part",
    ".partial",
    ".download",
    ".crdownload",
    ".onedrive",
    ".tmp.drivedownload",
    ".sync",
    ".cloud",
    ".onecache",
    ".!sync",
    ".autosave",
    ".bkp",
    ".backup",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
)

# Leading "_" means "do not touch" for both files and folders.
IGNORED_PREFIXES = (".", "~", "_")

# File Groups
MODEL_EXT = (".ma", ".mb", ".fbx", ".obj", ".blend")
TEXTURE_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".tga")
VIDEO_EXT = (".mov", ".mp4", ".avi", ".mkv")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".tga")

# Shot Config
SHOT_SUBDIRS = ("layout", "3d", "2d", "fx", "comp", "renders")

# Shot Mapping
# Format: SHxx -> (Act, Sequence, Scene)
def _build_shot_mapping() -> dict[str, tuple[str, str, str]]:
    mapping: dict[str, tuple[str, str, str]] = {}

    ranges = [
        ("Act_1", "SQ_01", 1, 20),   # SH01..SH19
        ("Act_2", "SQ_02", 20, 28),  # SH20..SH27
        ("Act_2", "SQ_03", 28, 37),  # SH28..SH36
        ("Act_3", "SQ_04", 37, 43),  # SH37..SH42
        ("Act_3", "SQ_05", 43, 55),  # SH43..SH54
    ]

    for act, sq, start, end in ranges:
        for i in range(start, end):
            shot = f"SH{i:02d}"
            scene_num = (i - start) + 1
            scene = f"SC_{scene_num:02d}"
            mapping[shot] = (act, sq, scene)

    return mapping

SHOT_MAPPING = _build_shot_mapping()

# Cloud Safety Config
STABILITY_RETRIES = 10
STABILITY_RETRY_DELAY = 1.0
STABILITY_SAMPLE_DELAY = 0.4

# Watch Config
DEBOUNCE_SECONDS = 2.0

# Batch Config
DEFAULT_BATCH_SIZE = 50
INTER_BATCH_DELAY = 1.0
