from __future__ import annotations

import re
from pathlib import Path

from config import MODEL_EXT, TEXTURE_EXT, SHOT_SUBDIRS, SHOT_MAPPING

# Pattern Config
SHOT_PREFIX_RE = re.compile(r"^(sh\d{2,3})_", re.IGNORECASE)
RENDER_FRAME_RE = re.compile(
    r".*[_\-](\d{4,6})\.(png|tga|exr|jpg|jpeg|tif|tiff)$",
    re.IGNORECASE,
)

# Texture / Shader suffix cleanup tokens
MAP_TOKENS = {
    "albedo",
    "diffuse",
    "basecolor",
    "base_color",
    "color",
    "col",
    "normal",
    "nrm",
    "roughness",
    "rough",
    "metallic",
    "metalness",
    "metal",
    "ao",
    "occlusion",
    "height",
    "displacement",
    "disp",
    "opacity",
    "alpha",
    "emissive",
    "emission",
    "specular",
    "spec",
    "gloss",
    "glossiness",
    "bump",
    "mask",
    "orm",
}

TECH_TOKEN_RE = re.compile(
    r"^(openpbr|shader\d*|material\d*|mat\d*|mtl\d*|v\d+|ver\d+|lod\d+|udim\d+|10\d{2}|\d{4})$",
    re.IGNORECASE,
)


# Rule Helpers
def _stem_after_prefix(filename: str, prefix: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    if stem.lower().startswith(prefix.lower()):
        return stem[len(prefix):]
    return stem


def _clean_asset_key(raw: str) -> str:
    parts = [p for p in raw.split("_") if p]
    if not parts:
        return raw

    # Strip known technical/map suffix tokens from the right
    while len(parts) > 1:
        last = parts[-1].lower()
        if last in MAP_TOKENS or TECH_TOKEN_RE.match(last):
            parts.pop()
            continue
        break

    return "_".join(parts) if parts else raw


def _existing_asset_folder(path: Path, root: Path, group_folder: str, prefix: str) -> Path | None:
    root_resolved = root.resolve()
    wanted_prefix = f"{prefix.lower()}_"
    wanted_head = ("production", "assets", "3d", group_folder.lower())

    for parent in path.parents:
        if not parent.name.lower().startswith(wanted_prefix):
            continue

        try:
            rel = parent.resolve().relative_to(root_resolved)
        except Exception:
            continue

        rel_parts = tuple(p.lower() for p in rel.parts)
        if len(rel_parts) >= 5 and rel_parts[:4] == wanted_head:
            return parent.resolve()

    return None


def _shot_base(root: Path, shot: str) -> Path:
    shot_key = shot.upper()
    data = SHOT_MAPPING.get(shot_key)
    if not data:
        return root / "incoming" / shot_key

    act, sequence, scene = data
    return root / "production" / "Acts" / act / sequence / scene / shot_key


def _asset_base(root: Path, group_folder: str, prefix: str, path: Path) -> Path:
    # If already inside a proper asset folder, keep using it
    existing = _existing_asset_folder(path, root, group_folder, prefix)
    if existing is not None:
        return existing

    raw = _stem_after_prefix(path.name, f"{prefix}_")
    asset_key = _clean_asset_key(raw).strip("_")
    if not asset_key:
        asset_key = "unknown"

    return root / "production" / "assets" / "3d" / group_folder / f"{prefix}_{asset_key}"


def _shot_destination(name: str, lower_name: str, root: Path, shot: str) -> Path:
    base = _shot_base(root, shot)

    if "layout" in lower_name or lower_name.endswith(".ma"):
        return base / "layout" / name

    if lower_name.endswith((".blend", ".fbx", ".obj", ".mb")):
        return base / "3d" / name

    if lower_name.endswith((".xstage", ".psd", ".png")) and "anim" in lower_name:
        return base / "2d" / name

    if lower_name.startswith(("fx2d", "fx3d")):
        return base / "fx" / name

    if lower_name.endswith((".aep", ".aepx", ".nk", ".nknc")):
        return base / "comp" / name

    if lower_name.endswith((".exr", ".png", ".jpg", ".jpeg", ".tif", ".tiff")) or "render" in lower_name:
        return base / "renders" / name

    return base / name


# Core Rule Engine
def choose_destination(path: Path, root: Path) -> Path | None:
    """
    Return destination path for a file.
    This function has no side effects and does not create folders.
    """
    name = path.name
    lower_name = name.lower()
    ext = path.suffix.lower()

    # Render sequences
    if RENDER_FRAME_RE.match(lower_name):
        return root / "production" / "shots" / "renders" / name

    # Reference videos
    if lower_name.startswith(("ref_", "reference_")) and ext in (".mov", ".mp4", ".avi"):
        return root / "reference" / name

    # Edit sessions
    if ext in (".sesx", ".prproj", ".fcpxml", ".logicx", ".band", ".ptx"):
        return root / "editing" / "sessions" / name

    # Audio
    if lower_name.startswith("music_") and ext in (".mp3", ".wav", ".aif", ".aiff"):
        return root / "editing" / "audio" / name

    # Concept art
    if lower_name.startswith("concept_char_"):
        return root / "concept_art" / "characters" / name
    if lower_name.startswith("concept_env_"):
        return root / "concept_art" / "locations" / name
    if lower_name.startswith("concept_prop_"):
        return root / "concept_art" / "props" / name
    if lower_name.startswith("concept_fx_"):
        return root / "concept_art" / "fx" / name
    if lower_name.startswith("concept_"):
        return root / "concept_art" / "misc" / name

    # 2D animation tests
    if lower_name.startswith("animfx_test_") and ext == ".mov":
        return root / "concept_art" / "Anim" / "2D" / "fx" / name
    if lower_name.startswith("animchar_test_") and ext == ".mov":
        return root / "concept_art" / "Anim" / "2D" / "char" / name

    # 3D assets
    if lower_name.startswith("char_"):
        base = _asset_base(root, "char", "char", path)
        if ext in MODEL_EXT:
            return base / "model" / name
        if ext in TEXTURE_EXT:
            return base / "textures" / name
        return base / name

    if lower_name.startswith("prop_"):
        base = _asset_base(root, "props", "prop", path)
        if ext in MODEL_EXT:
            return base / "model" / name
        if ext in TEXTURE_EXT:
            return base / "textures" / name
        return base / name

    if lower_name.startswith("env_"):
        base = _asset_base(root, "env", "env", path)
        if ext in MODEL_EXT:
            return base / "model" / name
        if ext in TEXTURE_EXT:
            return base / "textures" / name
        return base / name

    # Shot files
    shot_match = SHOT_PREFIX_RE.match(lower_name)
    if shot_match:
        shot = shot_match.group(1).upper()
        if shot not in SHOT_MAPPING:
            return root / "incoming" / name
        return _shot_destination(name, lower_name, root, shot)

    # Fallback
    return root / "incoming" / name


def shot_subdirs() -> tuple[str, ...]:
    return SHOT_SUBDIRS
