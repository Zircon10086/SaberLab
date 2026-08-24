"""SliceDetails analysis (v1.6.0, ported from SliceDetails by qqrz997 / ckosmic).

Pure deterministic computation. For each replay it aggregates the good cuts into
a 4x3 note grid (12 tiles), and for each tile into 2 color x 9 direction cells
(18 cells). Original project: https://github.com/qqrz997/SliceDetails (C# / GPL).

Port notes (differences from the C# original, deliberate):
- No runtime deps: the original's SiraUtil (DI) and BSML (UI) are C#-ecosystem
  glue used only for wiring/rendering; the algorithm itself only uses Mathf
  (cos/sin/atan2) which maps 1:1 onto Python's math.
- Cut offset sign: the original flips the sign via
  dot(cutNormal, cutPoint - noteCenter) using the note's in-game world
  position. BSOR has no note position, so the center is reconstructed from the
  grid formula (x=(line-1.5)*0.6, y=0.85/1.4/1.9 + height offset, z=cutPoint.z
  — the cut lands on the note surface, so the z term vanishes). Verified to
  mm accuracy against the BSOR cutDistanceToCenter (median 6mm over 8881 cuts)
  and against the independent SimSaber reverse-engineered motion model
  (<1mm x, ~2mm y, 0.1mm distance self-consistency; 2026 research).
- Mean denominator fix: the C# operator+ always rebuilds Score with
  CountPreSwing/CountPostSwing=true, so slider head/tail notes (which only
  contribute one of the two swings) dilute the other swing's average
  denominator. Here the effective counts are used, matching the mod's intent.
- "No data" is detected by cell count == 0 (the original compares
  angle == 0f && offset == 0f which is fragile).

Config-equivalent defaults (from SettingsStore): CountArcs=true,
CountChains=true, TrueCutOffsets=true, ShowSliceCounts=true (UI concern).
"""
from __future__ import annotations

import math

from ..bsor.models import (
    NoteEvent, GOOD,
    SCORING_DEFAULT, SCORING_NORMAL, SCORING_SLIDER_HEAD, SCORING_SLIDER_TAIL,
    SCORING_BURST_SLIDER_HEAD, SCORING_BURST_SLIDER_ELEMENT,
)
from .scoring import cut_scores

# Note grid: 3 layers (rows) x 4 lines (columns); tile index = layer*4 + line.
GRID_ROWS = 3
GRID_COLS = 4
TILE_COUNT = GRID_ROWS * GRID_COLS
CELLS_PER_TILE = 18          # 2 colors x 9 directions
DIRECTIONS_PER_HAND = 9

# OrderedNoteCutDirection (SliceDetails enum): 9 directions laid out in the
# 3x3 compass (row-major): UpLeft, Up, UpRight / Left, Any, Right /
# DownLeft, Down, DownRight. The game's NoteCutDirection enum orders
# directions differently (Up=0, Down=1, Left=2, Right=3, UpLeft=4, UpRight=5,
# DownLeft=6, DownRight=7, Any=8), so each direction maps to its compass slot.
_DIR_TO_SLOT = {
    0: 1,   # Up       -> Up slot
    1: 7,   # Down     -> Down slot
    2: 3,   # Left     -> Left slot
    3: 5,   # Right    -> Right slot
    4: 0,   # UpLeft   -> UpLeft slot
    5: 2,   # UpRight  -> UpRight slot
    6: 6,   # DownLeft -> DownLeft slot
    7: 8,   # DownRight-> DownRight slot
    8: 4,   # Any      -> center dot slot
}

# Note scoring types that count toward slice statistics. Default/Normal count
# both swings; slider head counts pre only, slider tail counts post only,
# burst slider head counts pre only (CountArcs/CountChains=true defaults);
# burst slider elements and ignore/no-score notes are excluded.
_SCORING_INCLUDED = {
    SCORING_DEFAULT, SCORING_NORMAL,
    SCORING_SLIDER_HEAD, SCORING_SLIDER_TAIL, SCORING_BURST_SLIDER_HEAD,
}
_SCORING_PRE_ONLY = {SCORING_SLIDER_HEAD, SCORING_BURST_SLIDER_HEAD}
_SCORING_POST_ONLY = {SCORING_SLIDER_TAIL}


def _cut_angle(cut_normal) -> float:
    """Cut plane direction angle in degrees (0..360), port of the original:
    cutDirection = (-normal.y, normal.x); angle = atan2(dy, dx) + 180."""
    nx, ny = cut_normal[0], cut_normal[1]
    return math.degrees(math.atan2(nx, -ny)) + 180.0


def _circular_mean(angles_deg: list[float]) -> float:
    """Mean of circular angles via cos/sin averaging (port of the original)."""
    sx = sum(math.cos(math.radians(a)) for a in angles_deg)
    sy = sum(math.sin(math.radians(a)) for a in angles_deg)
    return math.degrees(math.atan2(sy, sx))


def _empty_cell() -> dict:
    return {"count": 0, "angle": 0.0, "offset": 0.0,
            "pre": 0.0, "post": 0.0, "acc": 0.0, "total": 0.0}


def _signed_offset(note: NoteEvent, params, height: float, left_handed: bool) -> float:
    """Signed cut offset (m): negative when the saber cut the note from the
    "back" side, port of SliceDetails' sign rule:
        dot(cutNormal, cutPoint - noteCenter) > 0  ->  offset = -offset

    Note world center reconstruction (empirically verified):
    - x = (lineIndex - 1.5) * 0.6            (mirrored when left-handed)
    - y = 0.85 / 1.4 / 1.9 (layer) + clamp((height-1.8)*0.5, -0.2, 0.6)
    - z = cutPoint.z  (the cut lands on the note surface, so the z term of
      the dot product vanishes)
    The grid formula matches the SimSaber reverse-engineered motion model to
    <1mm (x) / a few mm (y) on real replays, and the reconstructed center is
    self-consistent with the BSOR cutDistanceToCenter to ~6mm (2026 research).
    """
    x = ((3 - params.line_index) if left_handed else params.line_index) - 1.5
    hoff = max(-0.2, min(0.6, (height - 1.8) * 0.5))
    cx = x * 0.6
    cy = _LAYER_Y.get(params.note_line_layer, params.note_line_layer * 0.6 + 0.85) + hoff
    px, py = note.cut.cut_point[0], note.cut.cut_point[1]
    d = note.cut.cut_normal[0] * (px - cx) + note.cut.cut_normal[1] * (py - cy)
    offset = abs(note.cut.cut_distance_to_center)
    return -offset if d > 0 else offset


_LAYER_Y = {0: 0.85, 1: 1.4, 2: 1.9}


def analyze_slice_details(notes: list[NoteEvent], height: float = 1.8,
                          left_handed: bool = False) -> dict:
    """Aggregate good cuts into the 4x3 tile grid.

    Returns {"tiles": [ {count, score_avg, cells: [18 cells]} x 12 ]} where
    each cell is {count, angle, offset, pre, post, acc, total}:
    - angle: circular mean cut angle (degrees)
    - offset: mean SIGNED cut distance to center (m; negative = cut from the
      note's back side, see _signed_offset)
    - pre/post/acc: mean swing/center scores (115 = pre70 + center15 + post30)
    - total: pre + post + acc (mean TotalScore)
    - pre/post are averages over the cells that actually contribute that swing
      (slider head/tail only contribute one swing)
    Cells/tiles with count == 0 carry angle/offset/score of 0 (UI greys them).
    """
    # Per-tile accumulators: cell -> (angles, offsets, pre_sum, pre_n,
    # post_sum, post_n, acc_sum, total_sum, count)
    cells: list[list[dict]] = [
        [{"angles": [], "offsets": [], "pre": 0.0, "pre_n": 0,
          "post": 0.0, "post_n": 0, "acc": 0.0, "total": 0.0, "count": 0}
         for _ in range(CELLS_PER_TILE)]
        for _ in range(TILE_COUNT)
    ]
    tile_totals = [0.0] * TILE_COUNT
    tile_counts = [0] * TILE_COUNT

    for note in notes:
        if note.event_type != GOOD or note.cut is None:
            continue
        p = note.params
        # Only the standard 4x3 grid (no ME/NE notes).
        if p.line_index < 0 or p.line_index >= GRID_COLS:
            continue
        if p.note_line_layer < 0 or p.note_line_layer >= GRID_ROWS:
            continue
        st = p.scoring_type
        if st not in _SCORING_INCLUDED:
            continue
        # Red/blue notes only (colorType 0/1); color 2 is the wall-penalty tag.
        if p.color_type not in (0, 1):
            continue
        slot = _DIR_TO_SLOT.get(p.cut_direction)
        if slot is None:
            continue

        before, center, after = cut_scores(note)
        # Pre is absent for slider tails (post-only); post is absent for
        # slider/burst heads (pre-only). Absent swings are excluded from both
        # the sum and the mean denominator (effective counts).
        pre = None if st in _SCORING_POST_ONLY else float(before)
        post = None if st in _SCORING_PRE_ONLY else float(after)
        acc = float(center)
        total = (0.0 if pre is None else pre) \
            + (0.0 if post is None else post) + acc

        cell = cells[p.note_line_layer * GRID_COLS + p.line_index][p.color_type * DIRECTIONS_PER_HAND + slot]
        cell["angles"].append(_cut_angle(note.cut.cut_normal))
        cell["offsets"].append(_signed_offset(note, p, height, left_handed))
        if pre is not None:
            cell["pre"] += pre
            cell["pre_n"] += 1
        if post is not None:
            cell["post"] += post
            cell["post_n"] += 1
        cell["acc"] += acc
        cell["total"] += total
        cell["count"] += 1
        tile_totals[p.note_line_layer * GRID_COLS + p.line_index] += total
        tile_counts[p.note_line_layer * GRID_COLS + p.line_index] += 1

    tiles = []
    for t in range(TILE_COUNT):
        out_cells = []
        for c in cells[t]:
            n = c["count"]
            if n == 0:
                out_cells.append(_empty_cell())
                continue
            out_cells.append({
                "count": n,
                "angle": round(_circular_mean(c["angles"]), 2),
                "offset": round(c["offsets"][0] if n == 1 else sum(c["offsets"]) / n, 3),
                "pre": round(c["pre"] / c["pre_n"], 2) if c["pre_n"] else 0.0,
                "post": round(c["post"] / c["post_n"], 2) if c["post_n"] else 0.0,
                "acc": round(c["acc"] / n, 2),
                "total": round(c["total"] / n, 2),
            })
        tiles.append({
            "count": tile_counts[t],
            "score_avg": round(tile_totals[t] / tile_counts[t], 2) if tile_counts[t] else 0.0,
            "cells": out_cells,
        })

    return {"tiles": tiles}
