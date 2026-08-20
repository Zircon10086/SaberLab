"""BSOR v1 数据模型。

字段命名与官方 C# 实现 (BeatLeader/BS-Open-Replay Replay.cs) 保持一致，
仅在必要处做 Python 化（snake_case）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

MAGIC_V1 = 0x442D3D69          # BSOR v1 magic (官方 ReplayEncoder)
MAGIC_QUEST = 0x443D3D38       # Quest 旧格式 magic（本项目 v1 阶段不支持）

# note event type（官方枚举）
GOOD, BAD, MISS, BOMB = 0, 1, 2, 3
EVENT_NAMES = {GOOD: "good", BAD: "bad", MISS: "miss", BOMB: "bomb"}

# scoring type（官方 ScoringType 枚举）
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
    """noteID 解码结果（官方 ReplayStatisticUtils.NoteParams）。

    noteID = scoringType*10000 + lineIndex*1000 + noteLineLayer*100
             + colorType*10 + cutDirection
    colorType: 0=红(左), 1=蓝(右), 2=无墙惩罚计数用, 3=bomb（V2 中 cutDirection=9 标记 bomb）
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
        """按颜色推断应该使用的手（colorType 0=left/red, 1=right/blue）。"""
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
            # 新版（V2 大 ID）：高位按 10^7/10^6/10^5 扩展，低两位仍是 color*10+cutDir
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
    before_cut_rating: float = 0.0   # 未 clamp，可 >1；*70 = Pre 分
    after_cut_rating: float = 0.0    # 未 clamp；*30 = Post 分

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
    event_type: int                # 有效类型（bomb 已按官方规则重标记）
    raw_event_type: int            # 文件中原始类型
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
        """该事件归属的手：good/bad 用实际切割的 saber，miss/bomb 用 note 颜色。"""
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
    duration: int      # 秒（long）
    time: float


@dataclass
class Transform:
    position: tuple    # (x, y, z)
    rotation: tuple    # (x, y, z, w) 四元数


@dataclass
class ControllerOffsets:
    left: Transform
    right: Transform


@dataclass
class ReplayInfo:
    version: str = ""            # mod 版本
    game_version: str = ""
    timestamp: str = ""          # unix 时间戳（字符串）
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
    score: int = 0               # 未修改总分
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
        """官方 WinTracker 判定：failTime < 0.01 视为未失败。"""
        return self.fail_time < 0.01


@dataclass
class Replay:
    info: ReplayInfo = field(default_factory=ReplayInfo)
    # frames: 用 numpy 结构化数组存储（见 parser），这里保存引用
    frames: object = None          # np.ndarray [('time',f4),('fps',i4),('pose',f4,(21,))]
    notes: list = field(default_factory=list)
    walls: list = field(default_factory=list)
    heights: list = field(default_factory=list)
    pauses: list = field(default_factory=list)
    controller_offsets: Optional[ControllerOffsets] = None
    user_data: bytes = b""

    # 解析元数据
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
