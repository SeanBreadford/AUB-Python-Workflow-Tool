from __future__ import annotations

import io
import json
import os
import struct
import time
import uuid
import zlib
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from config import CONFIG_DIR_NAME
from filesystem import sort_project
from project import init_project
from tracking import EVENTS_DIR_NAME

# A small sample project for trying the app or showing it to someone. Each
# shot recreates a real production problem the tracker catches:
#
#   SH01  approved on v002, then someone saved v003     -> changed after approval
#   SH02  sent for review on v001, then v002 arrived    -> newer than submitted
#   SH03  retake 9 days ago, nothing since              -> stalled
#   SH04  sam and alex both saved "v004"                -> version clash
#   SH05  assigned to you, the director left feedback   -> new for you
#   SH06  sam saved files, nobody touched anything      -> wip and assigned to sam by itself
#   SH07  retake yesterday, artist saved a fix          -> back to wip by itself

HOUR = 3600
DAY = 24 * HOUR
PALETTES = [
    ((40, 60, 120), (200, 120, 60)), ((20, 90, 70), (220, 200, 90)), ((90, 30, 80), (240, 150, 120)),
    ((30, 30, 30), (90, 140, 200)), ((120, 50, 20), (250, 220, 150)), ((10, 70, 110), (120, 220, 230)),
    ((60, 20, 90), (250, 180, 80)),
]


def _png(path: Path, start: tuple, end: tuple, stripe: int, width: int = 320, height: int = 180) -> None:
    """A small striped gradient, written without any imaging library."""
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            t = x / width
            pixel = [int(start[i] * (1 - t) + end[i] * t) for i in range(3)]
            if (x // stripe + y // stripe) % 7 == 0:
                pixel = [min(255, v + 40) for v in pixel]
            row += bytes(pixel)
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                     + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


class _Builder:
    def __init__(self, root: Path, you: str) -> None:
        self.root = root
        self.you = you
        self.now = time.time()
        self.events: list[dict] = []

    def _sort(self) -> None:
        with redirect_stdout(io.StringIO()):
            sort_project(self.root, fast_mode=True)

    def save(self, name: str, age: float, png: tuple | None = None) -> None:
        """Drop a file in the project root, sort it, and backdate it by `age` seconds."""
        before = set(self.root.rglob(Path(name).stem + "*"))
        if png:
            _png(self.root / name, *png)
        else:
            (self.root / name).write_text(f"demo scene file: {name}\n")
        self._sort()
        # The sorted file may have been renamed (_001) if the name was taken
        new = [p for p in self.root.rglob(Path(name).stem + "*") if p not in before and p.is_file()]
        stamp = self.now - age
        for path in new:
            os.utime(path, (stamp, stamp))

    def event(self, age: float, user: str, kind: str, entity: str, task: str, **data) -> None:
        when = datetime.fromtimestamp(self.now - age, timezone.utc).isoformat(timespec="milliseconds")
        self.events.append({"id": uuid.uuid4().hex, "time": when, "user": user, "type": kind,
                            "entity": entity, "entity_type": "shot", "task": task, **data})

    def write_events(self) -> None:
        folder = self.root / CONFIG_DIR_NAME / EVENTS_DIR_NAME
        folder.mkdir(parents=True, exist_ok=True)
        by_user: dict[str, list[dict]] = {}
        for event in self.events:
            by_user.setdefault(event["user"], []).append(event)
        for user, events in by_user.items():
            with (folder / f"{user}@demo.jsonl").open("w", encoding="utf-8") as handle:
                for event in sorted(events, key=lambda e: e["time"]):
                    handle.write(json.dumps(event) + "\n")


def create_demo(root: Path, you: str) -> Path:
    """Build the sample project in an empty folder. Returns the project root."""
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise FileExistsError(f"The demo needs an empty folder, so nothing of yours is touched: {root}")
    init_project(root)
    # The team, so names in file names are credited (SH05_layout_v002_sam.ma)
    team = ["sam", "alex", "priya"] + ([] if you in ("sam", "alex", "priya") else [you])
    config = root / CONFIG_DIR_NAME / "project.toml"
    with config.open("a", encoding="utf-8") as handle:
        for name in team:
            handle.write(f'\n[[team]]\nname = "{name}"\n')
    b = _Builder(root, you)

    # A comp still per shot, for thumbnails
    for i, (start, end) in enumerate(PALETTES):
        b.save(f"SH0{i + 1}_comp_v001.png", 2 * DAY, png=(start, end, 9 + i))

    # SH01: approved on v002, then alex saved v003
    b.save("SH01_layout_v001_sam.ma", 6 * DAY)
    b.save("SH01_layout_v002_sam.ma", 4 * DAY)
    b.event(3 * DAY, "priya", "status", "SH01", "layout", status="done",
            note="Approved, lovely staging", versions={"SH01_layout.ma": 2})
    b.save("SH01_layout_v003_alex.ma", 5 * HOUR)

    # SH02: sent for review on v001, then v002 arrived
    b.save("SH02_layout_v001_alex.ma", 2 * DAY)
    b.event(2 * DAY - HOUR, "alex", "status", "SH02", "layout", status="wfa",
            note="Ready for a look", versions={"SH02_layout.ma": 1})
    b.save("SH02_layout_v002_alex.ma", 3 * HOUR)

    # SH03: retake 9 days ago, nothing since
    b.save("SH03_layout_v001_sam.ma", 12 * DAY)
    b.event(9 * DAY, "priya", "status", "SH03", "layout", status="retake",
            note="Camera crosses the line at f120, please flip", versions={"SH03_layout.ma": 1})
    b.event(9 * DAY, "priya", "assign", "SH03", "layout", assignee="sam")

    # SH04: sam and alex both saved "v004"
    b.save("SH04_layout_v003_sam.ma", 3 * DAY)
    b.save("SH04_layout_v004_sam.ma", 26 * HOUR)
    b.save("SH04_layout_v004_alex.ma", 25 * HOUR)

    # SH05: assigned to you; the director left feedback today
    b.save(f"SH05_layout_v001_{you}.ma", 3 * DAY)
    b.save(f"SH05_layout_v002_{you}.ma", 1 * DAY)
    b.event(1 * DAY - HOUR, you, "status", "SH05", "layout", status="wfa",
            note="Camera locked", versions={"SH05_layout.ma": 2})
    b.event(1 * DAY - HOUR, "priya", "assign", "SH05", "layout", assignee=you)
    b.event(2 * HOUR, "priya", "status", "SH05", "layout", status="retake",
            note="Crane move at f40 feels slow, can you ease it in?", versions={"SH05_layout.ma": 2})
    b.event(1 * HOUR, "alex", "note", "SH05", "layout",
            text="I can share the reference I used for SH04's crane if it helps")

    # SH06: sam saved files and never touched a status (or an assignment)
    b.save("SH06_layout_v001_sam.ma", 6 * HOUR)
    b.save("SH06_layout_v002_sam.ma", 40 * 60)

    # SH07: retake yesterday, artist saved a fix this morning
    b.save("SH07_layout_v001_alex.ma", 3 * DAY)
    b.event(1 * DAY, "priya", "status", "SH07", "layout", status="retake",
            note="Too dark", versions={"SH07_layout.ma": 1})
    b.save("SH07_layout_v002_alex.ma", 4 * HOUR)

    # Assets, a few days old so the feed opens on the shots
    b.save("prop_OilLamp_diffuse_v002_sam.png", 5 * DAY, png=((90, 50, 10), (250, 200, 90), 16))
    b.save("prop_OilLamp_v002_sam.fbx", 5 * DAY)
    b.save("char_Princess_body_v004_alex.ma", 4 * DAY)
    b.save("env_Castle_v001_priya.blend", 6 * DAY)

    b.write_events()
    return root
