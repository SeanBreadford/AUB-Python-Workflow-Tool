from __future__ import annotations

import re
from pathlib import Path

from config import (
    IMAGE_EXT,
    MODEL_EXT,
    ROOT as DEFAULT_ROOT,
    SHOT_MAPPING,
    SHOT_SUBDIRS,
    TEXTURE_EXT,
)

# Pattern Config
SHOT_PREFIX_RE = re.compile(r"^(sh\d{2,3})_", re.IGNORECASE)
RENDER_FRAME_RE = re.compile(
    r"^(sh\d{2,3}.*[_\-]\d{4,6})\.(png|tga|exr|jpg|jpeg|tif|tiff)$",
    re.IGNORECASE,
)


# Root Helpers
def _resolve_roots(root: Path) -> tuple[Path, Path]:
    """
    Resolve (project_root, production_root) robustly.

    Supports both layouts:
    1) Root IS project root, with concept_art/editing/reference/incoming next to production.
    2) Root IS production folder, with project-level folders in parent.

    Also supports project layouts where everything sits directly under root.
    """
    root = Path(root).expanduser().resolve()

    project_markers = ("concept_art", "reference", "editing", "incoming")
    production_markers = ("Acts", "assets", "shots")

    has_project_here = any((root / name).exists() for name in project_markers)
    has_production_here = any((root / name).exists() for name in production_markers)
    parent_has_project = any((root.parent / name).exists() for name in project_markers)

    # Project root
    if has_project_here:
        project_root = root
    elif root.name.lower() == "production" and parent_has_project:
        project_root = root.parent
    else:
        project_root = root

    # Production root
    if has_production_here:
        production_root = root
    elif (project_root / "production").is_dir():
        production_root = project_root / "production"
    elif root.name.lower() == "production":
        production_root = root
    else:
        production_root = project_root / "production"

    return project_root, production_root


# Asset Helpers
def _extract_asset_key(filename: str, prefix: str) -> str:
    """
    Canonical asset key = first token after prefix.

    Examples:
      prop_OilLamp_lambert7_Displacement_001.png -> OilLamp
      prop_boobieperfume_okdjisalkd.ma -> boobieperfume
    """
    stem = Path(filename).stem
    tag = f"{prefix}_"

    if not stem.lower().startswith(tag.lower()):
        return "unknown"

    remaining = stem[len(tag):]
    if not remaining:
        return "unknown"

    key = remaining.split("_", 1)[0].strip()
    return key if key else "unknown"


def _find_existing_asset_folder(group_root: Path, target_folder_name: str) -> Path:
    """
    Reuse existing folder case-insensitively when present.
    """
    if group_root.exists():
        target_lower = target_folder_name.lower()
        for child in group_root.iterdir():
            if child.is_dir() and child.name.lower() == target_lower:
                return child
    return group_root / target_folder_name


def _asset_base(root: Path, group_folder: str, prefix: str, filename: str) -> Path:
    _, production_root = _resolve_roots(root)
    key = _extract_asset_key(filename, prefix)
    folder_name = f"{prefix}_{key}"
    group_root = production_root / "assets" / "3d" / group_folder
    return _find_existing_asset_folder(group_root, folder_name)


# Shot Helpers
def _shot_base(root: Path, shot: str) -> Path:
    _, production_root = _resolve_roots(root)
    shot = shot.upper()
    mapped = SHOT_MAPPING.get(shot)

    if mapped:
        act, sq, sc = mapped
        return production_root / "Acts" / act / sq / sc / shot

    return production_root / "shots" / shot


def _shot_destination(name: str, lower_name: str, root: Path, shot: str) -> Path:
    base = _shot_base(root, shot)
    ext = Path(name).suffix.lower()

    if "layout" in lower_name or ext == ".ma":
        return base / "layout" / name

    if ext in (".blend", ".fbx", ".obj", ".mb"):
        return base / "3d" / name

    if ext in (".xstage", ".psd", ".png") and "anim" in lower_name:
        return base / "2d" / name

    if lower_name.startswith(("fx2d", "fx3d")):
        return base / "fx" / name

    if ext in (".aep", ".aepx", ".nk", ".nknc"):
        return base / "comp" / name

    if ext in IMAGE_EXT or "render" in lower_name:
        return base / "renders" / name

    return base / name


# Rule Engine
def choose_destination(path: Path, root: Path | None = None) -> Path | None:
    """
    Return absolute destination path.

    Supports both call styles:
      choose_destination(path)
      choose_destination(path, root)
    """
    if root is None:
        root = DEFAULT_ROOT

    project_root, production_root = _resolve_roots(root)

    name = path.name
    lower_name = name.lower()
    ext = path.suffix.lower()

    # Render sequence frames with shot prefix + frame numbers
    if RENDER_FRAME_RE.match(lower_name):
        shot_match = SHOT_PREFIX_RE.match(lower_name)
        if shot_match:
            shot = shot_match.group(1).upper()
            return _shot_base(root, shot) / "renders" / name
        return production_root / "shots" / "renders" / name

    # References
    if lower_name.startswith(("ref_", "reference_")) and ext in (".mov", ".mp4", ".avi"):
        return project_root / "reference" / name

    # Editing sessions
    if ext in (".sesx", ".prproj", ".fcpxml", ".logicx", ".band", ".ptx"):
        return project_root / "editing" / "sessions" / name

    # Audio
    if lower_name.startswith("music_") and ext in (".mp3", ".wav", ".aif", ".aiff"):
        return project_root / "editing" / "audio" / name

    # Concept art
    if lower_name.startswith("concept_char_"):
        return project_root / "concept_art" / "characters" / name
    if lower_name.startswith("concept_env_"):
        return project_root / "concept_art" / "locations" / name
    if lower_name.startswith("concept_prop_"):
        return project_root / "concept_art" / "props" / name
    if lower_name.startswith("concept_fx_"):
        return project_root / "concept_art" / "fx" / name
    if lower_name.startswith("concept_"):
        return project_root / "concept_art" / "misc" / name

    # 2D tests
    if lower_name.startswith("animfx_test_") and ext == ".mov":
        return project_root / "concept_art" / "Anim" / "2D" / "fx" / name
    if lower_name.startswith("animchar_test_") and ext == ".mov":
        return project_root / "concept_art" / "Anim" / "2D" / "char" / name

    # 3D assets (canonical folder key from first token only)
    if lower_name.startswith("char_"):
        base = _asset_base(root, "char", "char", name)
        if ext in MODEL_EXT:
            return base / "model" / name
        if ext in TEXTURE_EXT:
            return base / "textures" / name
        return base / name

    if lower_name.startswith("prop_"):
        base = _asset_base(root, "props", "prop", name)
        if ext in MODEL_EXT:
            return base / "model" / name
        if ext in TEXTURE_EXT:
            return base / "textures" / name
        return base / name

    if lower_name.startswith("env_"):
        base = _asset_base(root, "env", "env", name)
        if ext in MODEL_EXT:
            return base / "model" / name
        if ext in TEXTURE_EXT:
            return base / "textures" / name
        return base / name

    # Shot files
    shot_match = SHOT_PREFIX_RE.match(lower_name)
    if shot_match:
        shot = shot_match.group(1).upper()
        return _shot_destination(name, lower_name, root, shot)

    # Fallback
    return project_root / "incoming" / name


def shot_subdirs() -> tuple[str, ...]:
    return SHOT_SUBDIRS
