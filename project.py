from __future__ import annotations

import re
import shutil
import string
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from config import CONFIG_DIR_NAME, CONFIG_FILE_NAME

DEFAULT_CONFIG_PATH = Path(__file__).with_name("default_project.toml")

# Fields every rule can use, and the extra ones a captured `shot` provides.
BASE_FIELDS = frozenset({"production"})
SHOT_FIELDS = frozenset({"shot_dir", "act", "sequence", "scene"})
UNASSIGNED = "unassigned"
# "_v003" / ".v3" / "-v12", followed by a separator or the end of the name
DEFAULT_VERSION_PATTERN = r"[_.\-]v(?P<version>\d+)(?=[_.\-]|$)"


class ProjectNotFoundError(Exception):
    pass


class ProjectConfigError(Exception):
    pass


# Rule Model
@dataclass(frozen=True)
class Rule:
    label: str
    patterns: tuple[re.Pattern[str], ...]
    exts: tuple[frozenset[str], ...]
    contains: tuple[tuple[str, ...], ...]
    dest: str
    # What the file belongs to, for version tracking. entity is a template
    # like "{shot}" or "prop_{asset}"; rules without one aren't tracked.
    entity: str | None = None
    entity_type: str | None = None
    task: str | None = None

    def match(self, name: str) -> dict[str, str] | None:
        """Return captured fields if every condition holds, else None."""
        lower_name = name.lower()
        ext = PurePosixPath(lower_name).suffix

        if any(ext not in group for group in self.exts):
            return None
        if any(not any(part in lower_name for part in parts) for parts in self.contains):
            return None

        fields: dict[str, str] = {}
        for pattern in self.patterns:
            found = pattern.match(name)
            if not found:
                return None
            fields.update({k: v for k, v in found.groupdict().items() if v is not None})
        return fields


# Tracking Model
@dataclass(frozen=True)
class Status:
    code: str
    name: str
    default: bool = False
    done: bool = False
    feedback_request: bool = False
    retake: bool = False
    # "#rrggbb"; None picks a default from the flags (see status_color)
    color: str | None = None


@dataclass(frozen=True)
class Automation:
    """When `task` reaches `status`, move `then_task` to `then_status`."""

    entity_type: str | None
    task: str
    status: str
    then_task: str
    then_status: str


# Project Model
@dataclass(frozen=True)
class Project:
    root: Path
    config_path: Path
    name: str
    code: str
    production_root: Path
    ignore_dirs: frozenset[str]
    ignore_name_parts: tuple[str, ...]
    ignore_prefixes: tuple[str, ...]
    shot_mapping: dict[str, tuple[str, str, str]]
    shot_dir: str
    unmapped_shot_dir: str
    rules: tuple[Rule, ...]
    fallback_dest: str | None
    fallback_from: frozenset[Path]
    version_pattern: re.Pattern[str]
    frame_pattern: re.Pattern[str] | None
    statuses: tuple[Status, ...] = ()
    task_types: dict[str, tuple[str, ...]] | None = None
    automations: tuple[Automation, ...] = ()
    # Statuses kept up to date from files (see [auto_status]); None = off
    auto_start_from: tuple[str, ...] = ()
    auto_started: str | None = None
    auto_after_retake: str | None = None
    # Warn when an active task has had no new file or change for this long
    stale_days: int | None = None

    @property
    def default_status(self) -> Status | None:
        return next((s for s in self.statuses if s.default), None)

    def status(self, code: str) -> Status | None:
        code = code.lower()
        return next((s for s in self.statuses if s.code == code), None)

    def base_fields(self) -> dict[str, str]:
        production = self.production_root.relative_to(self.root).as_posix()
        return {"production": production}

    def shot_fields(self, shot: str, base: dict[str, str]) -> dict[str, str]:
        shot = shot.upper()
        mapped = self.shot_mapping.get(shot)
        if mapped:
            act, sequence, scene = mapped
            template = self.shot_dir
        else:
            act = sequence = scene = UNASSIGNED
            template = self.unmapped_shot_dir
        fields = {**base, "shot": shot, "act": act, "sequence": sequence, "scene": scene}
        fields["shot_dir"] = template.format_map(fields)
        return fields


# Lookup
def find_project_root(start: Path) -> Path | None:
    """Walk up from start to the folder holding _pipeline/project.toml."""
    start = Path(start).expanduser().resolve()
    for folder in (start, *start.parents):
        if (folder / CONFIG_DIR_NAME / CONFIG_FILE_NAME).is_file():
            return folder
    return None


def load_project(start: Path) -> Project:
    root = find_project_root(start)
    if root is None:
        raise ProjectNotFoundError(
            f"No {CONFIG_DIR_NAME}/{CONFIG_FILE_NAME} found in or above {Path(start).resolve()}.\n"
            f"Create one with: python main.py --root \"{start}\" --init"
        )
    config_path = root / CONFIG_DIR_NAME / CONFIG_FILE_NAME
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise ProjectConfigError(f"{config_path}: invalid TOML: {error}") from error
    return _parse_project(root, config_path, data)


def init_project(root: Path) -> Path:
    """Write the default config into root. Never overwrites."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Root does not exist: {root}")
    dest = root / CONFIG_DIR_NAME / CONFIG_FILE_NAME
    if dest.exists():
        raise FileExistsError(f"Project config already exists: {dest}")
    dest.parent.mkdir(exist_ok=True)
    shutil.copyfile(DEFAULT_CONFIG_PATH, dest)
    return dest


# Parsing Helpers
class _Table:
    """Typed access to a TOML table that reports unknown keys (typos)."""

    def __init__(self, data: Any, where: str) -> None:
        if not isinstance(data, dict):
            raise ProjectConfigError(f"{where}: expected a table")
        self.data = data
        self.where = where
        self.used: set[str] = set()

    def get(self, key: str, kind: type | tuple[type, ...], default: Any = ...) -> Any:
        self.used.add(key)
        if key not in self.data:
            if default is ...:
                raise ProjectConfigError(f"{self.where}: missing required key '{key}'")
            return default
        value = self.data[key]
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            names = kind.__name__ if isinstance(kind, type) else "/".join(k.__name__ for k in kind)
            raise ProjectConfigError(f"{self.where}.{key}: expected {names}")
        return value

    def str_list(self, key: str, default: Any = ...) -> list[str]:
        value = self.get(key, list, default)
        if value is None:
            return None
        if not all(isinstance(item, str) for item in value):
            raise ProjectConfigError(f"{self.where}.{key}: expected a list of strings")
        return value

    def done(self) -> None:
        unknown = sorted(set(self.data) - self.used)
        if unknown:
            raise ProjectConfigError(f"{self.where}: unknown key(s): {', '.join(unknown)}")


def _placeholders(template: str, where: str) -> set[str]:
    try:
        names = {field for _, field, _, _ in string.Formatter().parse(template) if field is not None}
    except ValueError as error:
        raise ProjectConfigError(f"{where}: bad template '{template}': {error}") from error
    for name in names:
        if not name.isidentifier():
            raise ProjectConfigError(f"{where}: bad field '{{{name}}}' in '{template}'")
    return names


def _check_relative(template: str, where: str) -> None:
    path = PurePosixPath(template.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", template):
        raise ProjectConfigError(f"{where}: dest must stay inside the project: '{template}'")


def _join_dest(parent: str | None, child: str | None) -> str | None:
    if parent and child:
        return f"{parent.rstrip('/')}/{child}"
    return child or parent


def _compile_pattern(source: str, where: str, key: str = "pattern") -> re.Pattern[str]:
    try:
        return re.compile(source, re.IGNORECASE)
    except re.error as error:
        raise ProjectConfigError(f"{where}.{key}: invalid regex: {error}") from error


# Section Parsers
def _parse_shots(table: _Table) -> tuple[dict[str, tuple[str, str, str]], str, str]:
    shot_dir = table.get("dir", str)
    unmapped_dir = table.get("unmapped_dir", str)
    shot_format = table.get("shot_format", str, "SH{num:02d}")
    scene_format = table.get("scene_format", str, "SC_{num:02d}")

    for key, template, allowed in (
        ("dir", shot_dir, BASE_FIELDS | {"shot", "act", "sequence", "scene"}),
        ("unmapped_dir", unmapped_dir, BASE_FIELDS | {"shot"}),
    ):
        where = f"{table.where}.{key}"
        _check_relative(template, where)
        unknown = _placeholders(template, where) - allowed
        if unknown:
            raise ProjectConfigError(f"{where}: unknown field(s): {', '.join(sorted(unknown))}")

    mapping: dict[str, tuple[str, str, str]] = {}
    for index, raw in enumerate(table.get("ranges", list, [])):
        entry = _Table(raw, f"{table.where}.ranges[{index}]")
        act = entry.get("act", str)
        sequence = entry.get("sequence", str)
        first = entry.get("first", int)
        last = entry.get("last", int)
        entry.done()
        if last < first:
            raise ProjectConfigError(f"{entry.where}: last ({last}) is before first ({first})")
        for num in range(first, last + 1):
            shot = shot_format.format(num=num).upper()
            if shot in mapping:
                raise ProjectConfigError(f"{entry.where}: {shot} is already in another range")
            scene = scene_format.format(num=num - first + 1)
            mapping[shot] = (act, sequence, scene)

    table.done()
    return mapping, shot_dir, unmapped_dir


def _parse_conditions(
    table: _Table, file_groups: dict[str, frozenset[str]]
) -> tuple[list[re.Pattern[str]], list[frozenset[str]], list[tuple[str, ...]]]:
    patterns, exts, contains = [], [], []

    source = table.get("pattern", str, None)
    if source is not None:
        patterns.append(_compile_pattern(source, table.where))

    ext_entries = table.str_list("ext", None)
    if ext_entries is not None:
        group: set[str] = set()
        for entry in ext_entries:
            if entry.startswith("."):
                group.add(entry.lower())
            elif entry in file_groups:
                group |= file_groups[entry]
            else:
                raise ProjectConfigError(f"{table.where}.ext: unknown file group '{entry}'")
        exts.append(frozenset(group))

    parts = table.str_list("contains", None)
    if parts is not None:
        contains.append(tuple(part.lower() for part in parts))

    return patterns, exts, contains


def _parse_tracking(table: _Table, inherited: dict[str, str | None]) -> dict[str, str | None]:
    """entity / entity_type / task, falling back to the parent rule's values."""
    return {key: table.get(key, str, inherited.get(key)) for key in ("entity", "entity_type", "task")}


def _make_rule(label, patterns, exts, contains, dest, tracking, where) -> Rule:
    if not dest:
        raise ProjectConfigError(f"{where}: missing dest")
    _check_relative(dest, where)

    available = set(BASE_FIELDS)
    for pattern in patterns:
        available |= set(pattern.groupindex)
    if "shot" in available:
        available |= SHOT_FIELDS
    for key, template in (("dest", dest), ("entity", tracking["entity"])):
        if template is None:
            continue
        unknown = _placeholders(template, where) - available
        if unknown:
            raise ProjectConfigError(
                f"{where}: {key} uses unknown field(s) {', '.join(sorted(unknown))}; "
                f"available: {', '.join(sorted(available))}"
            )
    if tracking["entity"] is None and (tracking["entity_type"] or tracking["task"]):
        raise ProjectConfigError(f"{where}: entity_type and task need an entity")
    return Rule(label, tuple(patterns), tuple(exts), tuple(contains), dest, **tracking)


def _parse_rules(raw_rules: list, file_groups: dict[str, frozenset[str]]) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    for index, raw in enumerate(raw_rules):
        table = _Table(raw, f"rules[{index}]")
        name = table.get("name", str, f"rule {index + 1}")
        table.where = f"rules[{index}] ({name})"
        patterns, exts, contains = _parse_conditions(table, file_groups)
        dest = table.get("dest", str, None)
        tracking = _parse_tracking(table, {})
        raw_cases = table.get("cases", list, None)
        table.done()

        if raw_cases is None:
            if not (patterns or exts or contains):
                raise ProjectConfigError(f"{table.where}: needs at least one of pattern, ext, contains")
            rules.append(_make_rule(name, patterns, exts, contains, dest, tracking, table.where))
            continue

        # Flatten cases into plain rules: parent and case conditions both apply
        for case_index, raw_case in enumerate(raw_cases):
            case = _Table(raw_case, f"{table.where}.cases[{case_index}]")
            case_patterns, case_exts, case_contains = _parse_conditions(case, file_groups)
            case_dest = case.get("dest", str, None)
            case_tracking = _parse_tracking(case, tracking)
            case.done()
            rules.append(
                _make_rule(
                    f"{name} [{case_dest or 'default'}]",
                    patterns + case_patterns,
                    exts + case_exts,
                    contains + case_contains,
                    _join_dest(dest, case_dest),
                    case_tracking,
                    case.where,
                )
            )
    return tuple(rules)


def _parse_color(value: str | None, where: str) -> str | None:
    if value is None:
        return None
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ProjectConfigError(f"{where}.color: expected a colour like \"#2e7d32\", got '{value}'")
    return value.lower()


def _parse_statuses(raw_statuses: list) -> tuple[Status, ...]:
    statuses: list[Status] = []
    for index, raw in enumerate(raw_statuses):
        table = _Table(raw, f"statuses[{index}]")
        code = table.get("code", str).lower()
        table.where = f"statuses[{index}] ({code})"
        if not re.fullmatch(r"[a-z0-9_\-]+", code):
            raise ProjectConfigError(f"{table.where}: code may only use letters, digits, _ and -")
        if any(s.code == code for s in statuses):
            raise ProjectConfigError(f"{table.where}: duplicate status code")
        statuses.append(
            Status(
                code=code,
                name=table.get("name", str, code),
                default=table.get("default", bool, False),
                done=table.get("done", bool, False),
                feedback_request=table.get("feedback_request", bool, False),
                retake=table.get("retake", bool, False),
                color=_parse_color(table.get("color", str, None), table.where),
            )
        )
        table.done()

    defaults = [s.code for s in statuses if s.default]
    if statuses and len(defaults) != 1:
        found = ", ".join(defaults) or "none"
        raise ProjectConfigError(f"statuses: exactly one needs default = true (found: {found})")
    return tuple(statuses)


def _parse_task_types(table: _Table) -> dict[str, tuple[str, ...]]:
    task_types = {}
    for entity_type in list(table.data):
        tasks = table.str_list(entity_type)
        if len(set(tasks)) != len(tasks):
            raise ProjectConfigError(f"task_types.{entity_type}: duplicate task")
        task_types[entity_type] = tuple(tasks)
    table.done()
    return task_types


def _parse_automations(
    raw_automations: list, statuses: tuple[Status, ...], task_types: dict[str, tuple[str, ...]]
) -> tuple[Automation, ...]:
    codes = {s.code for s in statuses}
    automations = []
    for index, raw in enumerate(raw_automations):
        table = _Table(raw, f"automations[{index}]")
        automation = Automation(
            entity_type=table.get("entity_type", str, None),
            task=table.get("task", str),
            status=table.get("status", str).lower(),
            then_task=table.get("then_task", str),
            then_status=table.get("then_status", str).lower(),
        )
        table.done()

        for key in ("status", "then_status"):
            if getattr(automation, key) not in codes:
                raise ProjectConfigError(f"{table.where}.{key}: unknown status '{getattr(automation, key)}'")
        if automation.entity_type is not None:
            known = task_types.get(automation.entity_type)
            if known is None:
                raise ProjectConfigError(
                    f"{table.where}.entity_type: no task_types for '{automation.entity_type}'"
                )
            for key in ("task", "then_task"):
                if getattr(automation, key) not in known:
                    raise ProjectConfigError(
                        f"{table.where}.{key}: '{getattr(automation, key)}' is not a "
                        f"{automation.entity_type} task"
                    )
        automations.append(automation)
    return tuple(automations)


def _parse_project(root: Path, config_path: Path, data: dict) -> Project:
    top = _Table(data, config_path.name)

    info = _Table(top.get("project", dict), "project")
    name = info.get("name", str)
    code = info.get("code", str, "")
    production = info.get("production", str, "production")
    info.done()
    _check_relative(production, "project.production")

    ignore = _Table(top.get("ignore", dict, {}), "ignore")
    ignore_dirs = frozenset(d.lower() for d in ignore.str_list("dirs", []))
    ignore_name_parts = tuple(p.lower() for p in ignore.str_list("name_parts", []))
    ignore_prefixes = tuple(ignore.str_list("prefixes", [".", "~", "_"]))
    ignore.done()

    groups = _Table(top.get("file_groups", dict, {}), "file_groups")
    file_groups = {}
    for group_name in list(groups.data):
        exts = groups.str_list(group_name)
        bad = [e for e in exts if not e.startswith(".")]
        if bad:
            raise ProjectConfigError(f"file_groups.{group_name}: extensions must start with '.': {bad}")
        file_groups[group_name] = frozenset(e.lower() for e in exts)
    groups.done()

    shot_mapping, shot_dir, unmapped_dir = _parse_shots(_Table(top.get("shots", dict), "shots"))
    rules = _parse_rules(top.get("rules", list, []), file_groups)

    fallback = _Table(top.get("fallback", dict, {}), "fallback")
    fallback_dest = fallback.get("dest", str, None)
    fallback_from = fallback.str_list("from", ["."])
    fallback.done()
    if fallback_dest is not None:
        _check_relative(fallback_dest, "fallback.dest")
        if _placeholders(fallback_dest, "fallback.dest"):
            raise ProjectConfigError("fallback.dest: fields are not available here")
    for folder in fallback_from:
        _check_relative(folder, "fallback.from")

    versions = _Table(top.get("versions", dict, {}), "versions")
    version_pattern = _compile_pattern(
        versions.get("pattern", str, DEFAULT_VERSION_PATTERN), "versions"
    )
    if "version" not in version_pattern.groupindex:
        raise ProjectConfigError("versions.pattern: needs a (?P<version>...) group")
    frame_source = versions.get("frame_pattern", str, None)
    frame_pattern = _compile_pattern(frame_source, "versions", "frame_pattern") if frame_source else None
    if frame_pattern and "frame" not in frame_pattern.groupindex:
        raise ProjectConfigError("versions.frame_pattern: needs a (?P<frame>...) group")
    versions.done()

    statuses = _parse_statuses(top.get("statuses", list, []))
    task_types = _parse_task_types(_Table(top.get("task_types", dict, {}), "task_types"))
    automations = _parse_automations(top.get("automations", list, []), statuses, task_types)

    codes = {s.code for s in statuses}

    def status_code(table: _Table, key: str) -> str | None:
        code = table.get(key, str, None)
        if code is not None and code.lower() not in codes:
            raise ProjectConfigError(f"{table.where}.{key}: unknown status '{code}'")
        return code.lower() if code else None

    auto = _Table(top.get("auto_status", dict, {}), "auto_status")
    auto_start_from = tuple(c.lower() for c in auto.str_list("start_from", []))
    for code in auto_start_from:
        if code not in codes:
            raise ProjectConfigError(f"auto_status.start_from: unknown status '{code}'")
    auto_started = status_code(auto, "started")
    auto_after_retake = status_code(auto, "after_retake")
    auto.done()
    if auto_started and not auto_start_from:
        raise ProjectConfigError("auto_status: 'started' needs 'start_from' (which statuses count as not started)")

    health = _Table(top.get("health", dict, {}), "health")
    stale_days = health.get("stale_days", int, None)
    health.done()
    if stale_days is not None and stale_days < 1:
        raise ProjectConfigError("health.stale_days: must be at least 1")

    top.done()

    return Project(
        root=root,
        config_path=config_path,
        name=name,
        code=code,
        production_root=(root / production).resolve(),
        ignore_dirs=ignore_dirs,
        ignore_name_parts=ignore_name_parts,
        ignore_prefixes=ignore_prefixes,
        shot_mapping=shot_mapping,
        shot_dir=shot_dir,
        unmapped_shot_dir=unmapped_dir,
        rules=rules,
        fallback_dest=fallback_dest,
        fallback_from=frozenset((root / folder).resolve() for folder in fallback_from),
        version_pattern=version_pattern,
        frame_pattern=frame_pattern,
        statuses=statuses,
        task_types=task_types,
        automations=automations,
        auto_start_from=auto_start_from,
        auto_started=auto_started,
        auto_after_retake=auto_after_retake,
        stale_days=stale_days,
    )
