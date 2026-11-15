from __future__ import annotations

from pathlib import Path
import os

# Tool settings. Project settings (rules, shots, ignore lists) live in each
# project's _pipeline/project.toml; see default_project.toml.

# Project Config
# Default folder to sort. You can also set FILE_SORTER_ROOT in your environment.
# Sorting only runs if a project config is found in or above this folder.
DEFAULT_ROOT = Path.cwd()
ROOT = Path(os.getenv("FILE_SORTER_ROOT", str(DEFAULT_ROOT))).expanduser().resolve()

# Starts with "_", so the sorter and watcher never touch it.
CONFIG_DIR_NAME = "_pipeline"
CONFIG_FILE_NAME = "project.toml"

# Cloud Safety Config
STABILITY_RETRIES = 10
STABILITY_RETRY_DELAY = 1.0
STABILITY_SAMPLE_DELAY = 0.4

# Watch Config
DEBOUNCE_SECONDS = 2.0

# Batch Config
DEFAULT_BATCH_SIZE = 50
INTER_BATCH_DELAY = 1.0
