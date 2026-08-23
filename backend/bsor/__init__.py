"""BSOR (Beat Saber Open Replay) parsing package.

Authoritative format sources:
- https://github.com/BeatLeader/BS-Open-Replay (README structure description)
- Official C# decoder ReplayDecoder/Replay.cs (this package is a
  field-by-field port)
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
