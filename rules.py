from __future__ import annotations

from pathlib import Path

from project import Project


# Path Helpers
def _reuse_existing_case(root: Path, rel: Path) -> Path:
    """
    Join rel onto root, reusing existing folders whose name differs only in
    case, so prop_oillamp and prop_OilLamp never become two folders.
    """
    current = root
    for part in rel.parts:
        candidate = current / part
        if not candidate.exists() and current.is_dir():
            part_lower = part.lower()
            for child in current.iterdir():
                if child.is_dir() and child.name.lower() == part_lower:
                    candidate = child
                    break
        current = candidate
    return current


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


# Rule Engine
def match_rule(path: Path, project: Project) -> tuple[Path, str] | None:
    """
    Return (destination folder, rule label) for a file, or None to leave it.
    """
    name = path.name
    base = project.base_fields()

    for rule in project.rules:
        fields = rule.match(name)
        if fields is None:
            continue
        fields = {**base, **fields}
        if "shot" in fields:
            fields = project.shot_fields(fields["shot"], fields)
        rel = Path(rule.dest.format_map(fields))
        folder = _reuse_existing_case(project.root, rel)
        # Captured fields come from filenames, so make sure they can't escape
        if not _inside(folder.resolve(strict=False), project.root):
            return None
        return folder, rule.label

    # Fallback only applies to files in the configured drop folders
    if project.fallback_dest and path.resolve().parent in project.fallback_from:
        folder = _reuse_existing_case(project.root, Path(project.fallback_dest))
        return folder, "fallback"

    return None


def choose_destination(path: Path, project: Project) -> Path | None:
    """Return the absolute destination path for a file, or None to leave it."""
    matched = match_rule(path, project)
    if matched is None:
        return None
    folder, _ = matched
    return folder / path.name
