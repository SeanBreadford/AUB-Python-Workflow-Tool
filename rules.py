from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from project import Project, Rule


@dataclass(frozen=True)
class Match:
    folder: Path
    label: str
    rule: Rule | None = None
    fields: dict[str, str] | None = None

    @property
    def entity(self) -> str | None:
        if self.rule is None or self.rule.entity is None:
            return None
        return self.rule.entity.format_map(self.fields or {})


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
def match_rule(path: Path, project: Project) -> Match | None:
    """Return where a file belongs and which rule said so, or None to leave it."""
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
        return Match(folder, rule.label, rule, fields)

    # Fallback only applies to files in the configured drop folders
    if project.fallback_dest and path.resolve().parent in project.fallback_from:
        folder = _reuse_existing_case(project.root, Path(project.fallback_dest))
        return Match(folder, "fallback")

    return None


def choose_destination(path: Path, project: Project) -> Path | None:
    """Return the absolute destination path for a file, or None to leave it."""
    matched = match_rule(path, project)
    if matched is None:
        return None
    return matched.folder / path.name
