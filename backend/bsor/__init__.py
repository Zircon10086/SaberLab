"""BSOR (Beat Saber Open Replay) 解析包。

格式权威来源:
- https://github.com/BeatLeader/BS-Open-Replay (README 结构说明)
- 官方 C# 解码器 ReplayDecoder/Replay.cs（本包逐字段对照实现）
"""
from .models import (
    Replay, ReplayInfo, NoteEvent, NoteCutInfo, WallEvent,
    HeightEvent, Pause, ControllerOffsets, NoteParams,
)
from .parser import parse_file, parse_bytes, BsorError, UnsupportedFormatError

__all__ = [
    "Replay", "ReplayInfo", "NoteEvent", "NoteCutInfo", "WallEvent",
    "HeightEvent", "Pause", "ControllerOffsets", "NoteParams",
    "parse_file", "parse_bytes", "BsorError", "UnsupportedFormatError",
]
