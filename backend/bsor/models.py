"""BSOR v1 data models.

Field names stay consistent with the official C# implementation
(BeatLeader/BS-Open-Replay Replay.cs), with Pythonization (snake_case)
only where necessary.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

MAGIC_V1 = 0x442D3D69          # BSOR v1 magic (official ReplayEncoder)
MAGIC_QUEST = 0x443D3D38       # legacy Quest format magic (not supported at this project's v1 stage)

# note event type (official enum)
GOOD, BAD, MISS, BOMB = 0, 1, 2, 3
EVENT_NAMES = {GOOD: "good", BAD: "bad", MISS: "miss", BOMB: "bomb"}

# scoring type (official ScoringType enum)
SCORING_DEFAULT = 0
SCORING_IGNORE = 1
SCORING_NO_SCORE = 2
SCORING_NORMAL = 3
SCORING_SLIDER_HEAD = 4
SCORING_SLIDER_TAIL = 5
SCORING_BURST_SLIDER_HEAD = 6
SCORING_BURST_SLIDER_ELEMENT = 7
SCORING_NAMES = {
    0: "default", 1: "ignore", 2: "no_score", 3: "normal",
    4: "slider_head", 5: "slider_tail", 6: "burst_slider_head",
    7: "burst_slider_element",
}


@dataclass
class NoteParams:
    """noteID decode result (official ReplayStatisticUtils.NoteParams).

    noteID = scoringType*10000 + lineIndex*1000 + noteLineLayer*100
             + colorType*10 + cutDirection
    colorType: 0=red (left), 1=blue (right), 2=used for no-wall-penalty
    counting, 3=bomb (in V2, cutDirection=9 marks a bomb)
    """
    scoring_type: int
    line_index: int
    note_line_layer: int
    color_type: int
    cut_direction: int

    @property
    def scoring_name(self) -> str:
        return SCORING_NAMES.get(self.scoring_type, f"unknown_{self.scoring_type}")

    @property
    def saber(self) -> str:
        """Hand that should be used, inferred from the color (colorType 0=left/red, 1=right/blue)."""
        return "left" if self.color_type == 0 else "right"

    @staticmethod
    def decode(note_id: int) -> "NoteParams":
        if note_id < 0:
            return NoteParams(-1, -1, -1, -1, -1)
        if note_id < 100_000:
            scoring_type = note_id // 10_000
            r = note_id % 10_000
            line_index = r // 1_000
            r %= 1_000
            note_line_layer = r // 100
            r %= 100
            color_type = r // 10
            cut_direction = r % 10
        else:
            # New style (V2 large IDs): high digits expand by 10^7/10^6/10^5; the low two digits are still color*10+cutDir
            scoring_type = note_id // 10_000_000
            r = note_id % 10_000_000
            line_index = r // 1_000_000
            r %= 1_000_000
            note_line_layer = r // 100_000
            r %= 100_000
            color_type = r // 10
            cut_direction = r % 10
        return NoteParams(scoring_type, line_index, note_line_layer,
                          color_type, cut_direction)


@dataclass
class NoteCutInfo:
    speed_ok: bool = False
    direction_ok: bool = False
    saber_type_ok: bool = False
    was_cut_too_soon: bool = False
    saber_speed: float = 0.0
    saber_dir: tuple = (0.0, 0.0, 0.0)
    saber_type: int = 0            # 0=left, 1=right
    time_deviation: float = 0.0
    cut_dir_deviation: float = 0.0
    cut_point: tuple = (0.0, 0.0, 0.0)
    cut_normal: tuple = (0.0, 0.0, 0.0)
    cut_distance_to_center: float = 0.0
    cut_angle: float = 0.0
    before_cut_rating: float = 0.0   # not clamped, may exceed 1; *70 = Pre score
    after_cut_rating: float = 0.0    # not clamped; *30 = Post score

    @property
    def saber(self) -> str:
        return "left" if self.saber_type == 0 else "right"

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class NoteEvent:
    note_id: int
    event_time: float
    spawn_time: float
    event_type: int                # effective type (bombs re-tagged per the official rule)
    raw_event_type: int            # raw type as stored in the file
    cut: Optional[NoteCutInfo] = None

    @property
    def params(self) -> NoteParams:
        return NoteParams.decode(self.note_id)

    @property
    def is_bomb(self) -> bool:
        return self.note_id == -1 or self.note_id % 10 == 9

    @property
    def event_name(self) -> str:
        return EVENT_NAMES.get(self.event_type, f"unknown_{self.event_type}")

    @property
    def saber(self) -> str:
        """Hand this event belongs to: good/bad use the actual cutting saber, miss/bomb use the note color."""
        if self.cut is not None and self.event_type in (GOOD, BAD):
            return self.cut.saber
        return self.params.saber


@dataclass
class WallEvent:
    wall_id: int
    energy: float
    time: float
    spawn_time: float


@dataclass
class HeightEvent:
    height: float
    time: float


@dataclass
class Pause:
    duration: int      # seconds (long)
    time: float


@dataclass
class Transform:
    position: tuple    # (x, y, z)
    rotation: tuple    # (x, y, z, w) quaternion


@dataclass
class ControllerOffsets:
    left: Transform
    right: Transform


@dataclass
class ReplayInfo:
    version: str = ""            # mod version
    game_version: str = ""
    timestamp: str = ""          # unix timestamp (string)
    player_id: str = ""
    player_name: str = ""
    platform: str = ""
    tracking_system: str = ""
    hmd: str = ""
    controller: str = ""
    map_hash: str = ""
    song_name: str = ""
    mapper: str = ""
    difficulty: str = ""
    score: int = 0               # unmodified total score
    mode: str = ""
    environment: str = ""
    modifiers: str = ""
    jump_distance: float = 0.0
    left_handed: bool = False
    height: float = 0.0
    start_time: float = 0.0
    fail_time: float = 0.0
    speed: float = 0.0

    @property
    def timestamp_int(self) -> int:
        try:
            return int(float(self.timestamp))
        except (TypeError, ValueError):
            return 0

    @property
    def won(self) -> bool:
        """Official WinTracker rule: failTime < 0.01 counts as not failed."""
        return self.fail_time < 0.01


@dataclass
class Replay:
    info: ReplayInfo = field(default_factory=ReplayInfo)
    # frames: stored as a numpy structured array (see parser); this holds a reference
    frames: object = None          # np.ndarray [('time',f4),('fps',i4),('pose',f4,(21,))]
    notes: list = field(default_factory=list)
    walls: list = field(default_factory=list)
    heights: list = field(default_factory=list)
    pauses: list = field(default_factory=list)
    controller_offsets: Optional[ControllerOffsets] = None
    user_data: bytes = b""

    # parse metadata
    file_path: str = ""
    file_size: int = 0
    file_sha256: str = ""

    @property
    def frame_count(self) -> int:
        return 0 if self.frames is None else len(self.frames)

    def summary_counts(self) -> dict:
        counts = {"good": 0, "bad": 0, "miss": 0, "bomb": 0}
        for n in self.notes:
            counts[EVENT_NAMES.get(n.event_type, "unknown")] = \
                counts.get(EVENT_NAMES.get(n.event_type, "unknown"), 0) + 1
        return counts
