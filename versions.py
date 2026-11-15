from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from filesystem import is_already_sorted, is_ignored_file, iter_files
from project import Project
from rules import match_rule

if TYPE_CHECKING:
    from tracking import Tracker

# Version tracking works like ShotGrid publishes: a file's "name" is its
# filename with the version removed (SH05_layout_v003.ma -> SH05_layout.ma),
# and files are grouped by entity, then task, then name, then version.
# Everything is read from the files on disk, so there is nothing to sync.


# Name Parsing
@dataclass(frozen=True)
class ParsedName:
    name: str
    version: int | None
    frame: int | None


def _last_match(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    found = None
    for found in pattern.finditer(text):
        pass
    return found


def parse_name(filename: str, project: Project) -> ParsedName:
    """
    Split a filename into name, version and frame number.

    SH05_layout_v003.ma        -> SH05_layout.ma, 3, None
    SH05_beauty_v002_0001.exr  -> SH05_beauty_####.exr, 2, 1
    """
    path = PurePosixPath(filename)
    stem, suffix = path.stem, path.suffix

    frame = None
    if project.frame_pattern:
        found = _last_match(project.frame_pattern, stem)
        if found:
            frame = int(found.group("frame"))
            start, end = found.span("frame")
            stem = stem[:start] + "#" * (end - start) + stem[end:]

    version = None
    found = _last_match(project.version_pattern, stem)
    if found:
        version = int(found.group("version"))
        stem = stem[: found.start()] + stem[found.end() :]

    return ParsedName(stem + suffix, version, frame)


# Index Model
@dataclass
class Version:
    number: int | None
    files: list[Path] = field(default_factory=list)
    modified: float = 0.0
    is_sequence: bool = False

    @property
    def label(self) -> str:
        return "-" if self.number is None else f"v{self.number:03d}"


@dataclass
class Product:
    """One versioned thing, e.g. SH05's layout scene."""

    name: str
    task: str | None
    versions: dict[int | None, Version] = field(default_factory=dict)

    def add(self, number: int | None, path: Path, is_sequence: bool) -> None:
        version = self.versions.setdefault(number, Version(number))
        version.files.append(path)
        version.is_sequence = version.is_sequence or is_sequence
        version.modified = max(version.modified, path.stat().st_mtime)

    def history(self) -> list[Version]:
        """Oldest first; unversioned files sort before v001."""
        return sorted(
            self.versions.values(),
            key=lambda v: (v.number is not None, v.number or 0, v.modified),
        )

    @property
    def latest(self) -> Version:
        return self.history()[-1]


@dataclass
class Entity:
    """A shot or asset."""

    name: str
    entity_type: str | None
    context: str | None = None
    products: dict[tuple[str, str], Product] = field(default_factory=dict)

    def sorted_products(self) -> list[Product]:
        return sorted(self.products.values(), key=lambda p: (p.task or "~", p.name.lower()))


@dataclass
class VersionIndex:
    project: Project
    entities: list[Entity]
    unsorted: list[Path]

    def find(self, name: str) -> Entity | None:
        name = name.lower()
        return next((e for e in self.entities if e.name.lower() == name), None)


def natural_key(text: str) -> list:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


# Scanning
def scan_versions(project: Project, root: Path | None = None) -> VersionIndex:
    """Build the version index from the sorted files under root."""
    entities: dict[str, Entity] = {}
    unsorted: list[Path] = []

    for path in iter_files(root or project.root, project):
        if is_ignored_file(path, project):
            continue
        matched = match_rule(path, project)
        if matched is None or matched.entity is None:
            continue
        # Only files already in place count; the rest still need a sort
        if not is_already_sorted(path, matched.folder / path.name):
            unsorted.append(path)
            continue

        rule, fields = matched.rule, matched.fields or {}
        entity_name = matched.entity
        entity = entities.get(entity_name.lower())
        if entity is None:
            context = None
            if "shot" in fields and fields["act"] != "unassigned":
                context = f"{fields['act']} / {fields['sequence']} / {fields['scene']}"
            entity = Entity(entity_name, rule.entity_type, context)
            entities[entity_name.lower()] = entity

        parsed = parse_name(path.name, project)
        key = (rule.task or "", parsed.name.lower())
        product = entity.products.setdefault(key, Product(parsed.name, rule.task))
        product.add(parsed.version, path, parsed.frame is not None)

    # Entity types appear in the order the config first uses them
    type_order: dict[str | None, int] = {}
    for rule in project.rules:
        type_order.setdefault(rule.entity_type, len(type_order))

    ordered = sorted(
        entities.values(),
        key=lambda e: (type_order.get(e.entity_type, len(type_order)), natural_key(e.name)),
    )
    return VersionIndex(project, ordered, sorted(unsorted))


# Report
def format_report(index: VersionIndex, tracker: Tracker | None = None) -> str:
    """The version report; with a tracker, each row also shows its task's status."""
    lines = [f"Project: {index.project.name}"]
    if not index.entities:
        lines.append("No tracked files found.")

    all_products = [p for e in index.entities for p in e.products.values()]
    task_width = max((len(p.task or "-") for p in all_products), default=1)
    name_width = max((len(p.name) for p in all_products), default=1)
    status_width = max((len(s.code) for s in tracker.project.statuses), default=1) if tracker else 0

    current_type: object = object()
    for entity in index.entities:
        if entity.entity_type != current_type:
            current_type = entity.entity_type
            count = sum(1 for e in index.entities if e.entity_type == current_type)
            lines += ["", f"== {current_type or 'other'} ({count}) =="]

        lines.append(f"{entity.name}" + (f"  ({entity.context})" if entity.context else ""))
        for product in entity.sorted_products():
            latest = product.latest
            notes = []
            if len(product.versions) > 1:
                notes.append(f"{len(product.versions)} versions")
            if latest.is_sequence:
                notes.append(f"{len(latest.files)} frames")
            when = datetime.fromtimestamp(latest.modified).strftime("%Y-%m-%d %H:%M")
            status = ""
            if tracker is not None:
                code = tracker.task(entity.name, product.task).status if product.task else "-"
                status = f"  {code:<{status_width}}"
            lines.append(
                f"  {product.task or '-':<{task_width}}  {product.name:<{name_width}}  "
                f"{latest.label:>4}{status}  {when}" + (f"  {', '.join(notes)}" if notes else "")
            )

    if index.unsorted:
        lines += ["", f"{len(index.unsorted)} tracked file(s) not sorted yet; run a sort to include them."]
    return "\n".join(lines)
