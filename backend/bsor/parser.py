"""BSOR v1 二进制解析器（严格对照官方 C# ReplayDecoder）。

格式要点（官方 README + Replay.cs）:
- little-endian
- magic int32 = 0x442D3D69, version byte = 1
- 每个 section 以 1 byte 类型标识开始: 0=info 1=frames 2=notes 3=walls
  4=heights 5=pauses 6=controller offsets(可选) 7=user data(可选)
- string = int32 字节数 + UTF-8
- playerName 的长度前缀存在官方已知 bug（C# 按 UTF-16 字符数写入），
  非 ASCII 名字需要前向扫描修复（对齐官方 DecodeName 逻辑）。
"""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import Optional

import numpy as np

from .models import (
    MAGIC_V1, MAGIC_QUEST, GOOD, BAD, BOMB,
    Replay, ReplayInfo, NoteEvent, NoteCutInfo, WallEvent,
    HeightEvent, Pause, ControllerOffsets, Transform,
)

FRAME_DTYPE = np.dtype([
    ("time", "<f4"),
    ("fps", "<i4"),
    # head(pos3+rot4) + left(pos3+rot4) + right(pos3+rot4)
    ("pose", "<f4", (21,)),
])
FRAME_BYTES = FRAME_DTYPE.itemsize  # 92


class BsorError(Exception):
    """BSOR 解析错误基类。"""

    def __init__(self, message: str, offset: int = -1):
        self.offset = offset
        super().__init__(message if offset < 0 else f"{message} (offset={offset})")


class UnsupportedFormatError(BsorError):
    """magic/version 不受支持（包括 Quest 旧格式）。"""


class _Reader:
    __slots__ = ("buf", "p", "n")

    def __init__(self, buf: bytes):
        self.buf = buf
        self.p = 0
        self.n = len(buf)

    def _need(self, k: int):
        if self.p + k > self.n:
            raise BsorError(
                f"意外的文件结尾: 需要 {k} 字节, 仅剩 {max(0, self.n - self.p)}", self.p)

    def u8(self) -> int:
        self._need(1)
        v = self.buf[self.p]
        self.p += 1
        return v

    def i32(self) -> int:
        self._need(4)
        v = struct.unpack_from("<i", self.buf, self.p)[0]
        self.p += 4
        return v

    def i64(self) -> int:
        self._need(8)
        v = struct.unpack_from("<q", self.buf, self.p)[0]
        self.p += 8
        return v

    def f32(self) -> float:
        self._need(4)
        v = struct.unpack_from("<f", self.buf, self.p)[0]
        self.p += 4
        return v

    def bool_(self) -> bool:
        return self.u8() != 0

    def vec3(self) -> tuple:
        self._need(12)
        v = struct.unpack_from("<3f", self.buf, self.p)
        self.p += 12
        return v

    def quat(self) -> tuple:
        self._need(16)
        v = struct.unpack_from("<4f", self.buf, self.p)
        self.p += 16
        return v

    def string(self, max_len: int = 4096) -> str:
        length = self.i32()
        if length < 0 or length > max_len:
            raise BsorError(f"非法字符串长度 {length}", self.p - 4)
        self._need(length)
        s = self.buf[self.p:self.p + length].decode("utf-8", errors="replace")
        self.p += length
        return s

    def player_name(self) -> str:
        """读取 playerName，兼容官方 mod 的长度前缀 bug。

        C# 编码器写入的是 UTF-16 字符数而非 UTF-8 字节数；名字含非 ASCII 时
        前缀偏小。官方解码器通过前向扫描到下一个合法 section/字段修复；
        这里以“其后必须跟着合法的 platform 字符串”作为对齐条件，更稳健。
        """
        length = self.i32()
        if length < 0 or length > 4096:
            raise BsorError(f"非法 playerName 长度 {length}", self.p - 4)
        start = self.p

        def plausible_platform(q: int) -> bool:
            if q + 4 > self.n:
                return False
            lp = struct.unpack_from("<i", self.buf, q)[0]
            if lp < 0 or lp > 32 or q + 4 + lp > self.n:
                return False
            try:
                s = self.buf[q + 4:q + 4 + lp].decode("ascii")
            except UnicodeDecodeError:
                return False
            return s.isalpha() or s in ("", "unknown")

        if plausible_platform(start + length):
            self.p = start + length
            return self.buf[start:self.p].decode("utf-8", errors="replace")

        # 前向扫描（官方 DecodeName 的泛化版本）
        for extra in range(1, 512):
            if plausible_platform(start + length + extra):
                end = start + length + extra
                s = self.buf[start:end].decode("utf-8", errors="replace")
                self.p = end
                return s
            if start + length + extra + 4 >= self.n:
                break
        raise BsorError("无法对齐 playerName 边界（platform 字段未找到）", start)


def _decode_info(r: _Reader) -> ReplayInfo:
    info = ReplayInfo()
    info.version = r.string()
    info.game_version = r.string()
    info.timestamp = r.string()
    info.player_id = r.string()
    info.player_name = r.player_name()
    info.platform = r.string()
    info.tracking_system = r.string()
    info.hmd = r.string()
    info.controller = r.string()
    info.map_hash = r.string()
    info.song_name = r.string()
    info.mapper = r.string()
    info.difficulty = r.string()
    info.score = r.i32()
    info.mode = r.string()
    info.environment = r.string()
    info.modifiers = r.string()
    info.jump_distance = r.f32()
    info.left_handed = r.bool_()
    info.height = r.f32()
    info.start_time = r.f32()
    info.fail_time = r.f32()
    info.speed = r.f32()
    return info


def _decode_frames(r: _Reader) -> np.ndarray:
    count = r.i32()
    if count < 0:
        raise BsorError(f"非法 frame 数量 {count}", r.p - 4)
    need = count * FRAME_BYTES
    r._need(need)
    arr = np.frombuffer(r.buf, dtype=FRAME_DTYPE, count=count, offset=r.p).copy()
    r.p += need
    return arr


def _decode_cut_info(r: _Reader) -> NoteCutInfo:
    c = NoteCutInfo()
    c.speed_ok = r.bool_()
    c.direction_ok = r.bool_()
    c.saber_type_ok = r.bool_()
    c.was_cut_too_soon = r.bool_()
    c.saber_speed = r.f32()
    c.saber_dir = r.vec3()
    c.saber_type = r.i32()
    c.time_deviation = r.f32()
    c.cut_dir_deviation = r.f32()
    c.cut_point = r.vec3()
    c.cut_normal = r.vec3()
    c.cut_distance_to_center = r.f32()
    c.cut_angle = r.f32()
    c.before_cut_rating = r.f32()
    c.after_cut_rating = r.f32()
    return c


def _decode_notes(r: _Reader) -> list[NoteEvent]:
    count = r.i32()
    if count < 0:
        raise BsorError(f"非法 note 数量 {count}", r.p - 4)
    notes: list[NoteEvent] = []
    for _ in range(count):
        note_id = r.i32()
        event_time = r.f32()
        spawn_time = r.f32()
        raw_type = r.i32()
        cut = None
        if raw_type in (GOOD, BAD):
            cut = _decode_cut_info(r)
        event_type = raw_type
        # 官方规则: noteID 末位 9（或 -1）为 bomb，重标记事件类型
        if note_id == -1 or note_id % 10 == 9:
            event_type = BOMB
        notes.append(NoteEvent(note_id, event_time, spawn_time,
                                event_type, raw_type, cut))
    return notes


def _decode_walls(r: _Reader) -> list[WallEvent]:
    count = r.i32()
    if count < 0:
        raise BsorError(f"非法 wall 数量 {count}", r.p - 4)
    walls = []
    for _ in range(count):
        wall_id = r.i32()
        energy = r.f32()
        time = r.f32()
        spawn_time = r.f32()
        walls.append(WallEvent(wall_id, energy, time, spawn_time))
    return walls


def _decode_heights(r: _Reader) -> list[HeightEvent]:
    count = r.i32()
    if count < 0:
        raise BsorError(f"非法 height 数量 {count}", r.p - 4)
    return [HeightEvent(r.f32(), r.f32()) for _ in range(count)]


def _decode_pauses(r: _Reader) -> list[Pause]:
    count = r.i32()
    if count < 0:
        raise BsorError(f"非法 pause 数量 {count}", r.p - 4)
    return [Pause(r.i64(), r.f32()) for _ in range(count)]


def _decode_controller_offsets(r: _Reader) -> ControllerOffsets:
    left = Transform(r.vec3(), r.quat())
    right = Transform(r.vec3(), r.quat())
    return ControllerOffsets(left, right)


def parse_bytes(data: bytes, file_path: str = "", file_sha256: str = "") -> Replay:
    """解析 BSOR v1 字节流。失败抛出 BsorError 子类。"""
    if len(data) < 5:
        raise BsorError(f"文件过小 ({len(data)} bytes)", 0)
    r = _Reader(data)
    magic = r.i32()
    version = r.u8()
    if magic == MAGIC_QUEST:
        raise UnsupportedFormatError(
            f"Quest 旧格式 (magic=0x{magic:08X}, version={version})，当前仅支持 BSOR v1", 0)
    if magic != MAGIC_V1:
        raise UnsupportedFormatError(
            f"未知 magic 0x{magic:08X}（期望 0x{MAGIC_V1:08X}）", 0)
    if version != 1:
        raise UnsupportedFormatError(f"不支持的 BSOR 版本 {version}（仅支持 v1）", 4)

    replay = Replay()
    # 官方解码器按 0..5 顺序循环读取；这里改为按实际 tag 读取，
    # 兼容含可选 section(6/7) 的文件。
    while r.p < r.n:
        tag = r.u8()
        if tag == 0:
            replay.info = _decode_info(r)
        elif tag == 1:
            replay.frames = _decode_frames(r)
        elif tag == 2:
            replay.notes = _decode_notes(r)
        elif tag == 3:
            replay.walls = _decode_walls(r)
        elif tag == 4:
            replay.heights = _decode_heights(r)
        elif tag == 5:
            replay.pauses = _decode_pauses(r)
        elif tag == 6:
            replay.controller_offsets = _decode_controller_offsets(r)
        elif tag == 7:
            length = r.i32()
            if length < 0 or length > r.n - r.p:
                raise BsorError(f"非法 userData 长度 {length}", r.p - 4)
            replay.user_data = r.buf[r.p:r.p + length]
            r.p += length
        else:
            raise BsorError(f"未知 section tag {tag}", r.p - 1)

    replay.file_path = file_path
    replay.file_size = len(data)
    replay.file_sha256 = file_sha256
    return replay


def parse_file(path: str | Path) -> Replay:
    """解析 .bsor 文件（计算 sha256 用于去重主键）。"""
    path = Path(path)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    replay = parse_bytes(data, file_path=str(path), file_sha256=digest)
    return replay


def parse_metadata_only(path: str | Path) -> Replay:
    """轻量解析：只读 info section，跳过 frames/notes 等大数组（§分析策略）。

    用途：扫描时秒级入库元数据（列表/历史/搜索立即可用），
    完整分析（motion/windows/fatigue）延迟到点击详情或后台预计算。
    返回的 Replay 只有 info / file_path / file_size / file_sha256 有效。
    """
    path = Path(path)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if len(data) < 5:
        raise BsorError(f"文件过小 ({len(data)} bytes)", 0)
    r = _Reader(data)
    magic = r.i32()
    version = r.u8()
    if magic != MAGIC_V1 or version != 1:
        # 非 v1：走完整解析以得到标准错误
        return parse_bytes(data, file_path=str(path), file_sha256=digest)

    replay = Replay()
    # 只消费 sections 直到拿到 info（tag 0），其余按字节长度跳过。
    while r.p < r.n:
        tag = r.u8()
        if tag == 0:
            replay.info = _decode_info(r)
            break
        elif tag == 1:
            _skip_frames(r)
        elif tag == 2:
            _skip_notes(r)
        elif tag == 3:
            _skip_walls(r)
        elif tag == 4:
            _skip_heights(r)
        elif tag == 5:
            _skip_pauses(r)
        elif tag == 6:
            _skip_controller_offsets(r)
        elif tag == 7:
            length = r.i32()
            if length < 0 or length > r.n - r.p:
                raise BsorError(f"非法 userData 长度 {length}", r.p - 4)
            r.p += length
        else:
            raise BsorError(f"未知 section tag {tag}", r.p - 1)

    replay.file_path = str(path)
    replay.file_size = len(data)
    replay.file_sha256 = digest
    return replay


def _skip_frames(r: _Reader) -> None:
    count = r.i32()
    if count < 0:
        raise BsorError(f"非法 frame 数量 {count}", r.p - 4)
    r._need(count * FRAME_BYTES)
    r.p += count * FRAME_BYTES


def _skip_notes(r: _Reader) -> None:
    count = r.i32()
    if count < 0:
        raise BsorError(f"非法 note 数量 {count}", r.p - 4)
    for _ in range(count):
        r.i32()     # note_id
        r.f32()     # event_time
        r.f32()     # spawn_time
        raw_type = r.i32()
        if raw_type in (GOOD, BAD):
            _skip_cut_info(r)


def _skip_cut_info(r: _Reader) -> None:
    r.bool_()      # speed_ok
    r.bool_()      # direction_ok
    r.bool_()      # saber_type_ok
    r.bool_()      # was_cut_too_soon
    r.f32()        # saber_speed
    r.vec3()       # saber_dir
    r.i32()        # saber_type
    r.f32()        # time_deviation
    r.f32()        # cut_dir_deviation
    r.vec3()       # cut_point
    r.vec3()       # cut_normal
    r.f32()        # cut_distance_to_center
    r.f32()        # cut_angle
    r.f32()        # before_cut_rating
    r.f32()        # after_cut_rating


def _skip_walls(r: _Reader) -> None:
    count = r.i32()
    if count < 0:
        raise BsorError(f"非法 wall 数量 {count}", r.p - 4)
    r._need(count * 16)
    r.p += count * 16


def _skip_heights(r: _Reader) -> None:
    count = r.i32()
    if count < 0:
        raise BsorError(f"非法 height 数量 {count}", r.p - 4)
    r._need(count * 8)
    r.p += count * 8


def _skip_pauses(r: _Reader) -> None:
    count = r.i32()
    if count < 0:
        raise BsorError(f"非法 pause 数量 {count}", r.p - 4)
    r._need(count * 12)
    r.p += count * 12


def _skip_controller_offsets(r: _Reader) -> None:
    # left(pos3+rot4) + right(pos3+rot4) = 14 floats
    r._need(14 * 4)
    r.p += 14 * 4
