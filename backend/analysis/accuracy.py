"""Accuracy Analyzer：Pre/Center/Post、左右手、网格、切准细节。

口径与官方 ReplayStatisticUtils.AccuracyTracker 对齐（含 scoringType 排除规则），
并补充 SaberLab 需要的原始量（cut 距离 cm、saber 速度、timing 偏差）。
设计文档 §13 重点：高密度/高压力下 Center 是否退化。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..bsor.models import (
    Replay, NoteEvent, GOOD, BAD, MISS, BOMB,
    SCORING_SLIDER_TAIL, SCORING_SLIDER_HEAD,
    SCORING_BURST_SLIDER_HEAD, SCORING_BURST_SLIDER_ELEMENT,
)
from .scoring import cut_scores


@dataclass
class HandStats:
    saber: str
    cuts: int = 0                     # 参与统计的 good cut 数（按分量各自计数）
    pre_score_avg: float = 0.0        # Pre 均分 (0-70) —— fixture 口径
    center_score_avg: float = 0.0     # Center 均分 (0-15) —— fixture 口径
    post_score_avg: float = 0.0       # Post 均分 (0-30) —— fixture 口径
    pre_rating_avg: float = 0.0       # beforeCutRating 原始均值（未 clamp）
    post_rating_avg: float = 0.0
    cut_distance_cm_avg: float = 0.0  # cutDistanceToCenter 均值（厘米）
    saber_speed_avg: float = 0.0
    time_devs: list = field(default_factory=list)   # 原始 time_deviation 序列（秒）
    time_dependence: float = 0.0      # 官方 LeftTimeDependence: avg |cutNormal.z|
    late_count: int = 0
    pre_n: int = 0
    center_n: int = 0
    post_n: int = 0
    good: int = 0
    bad: int = 0
    miss: int = 0
    bomb: int = 0
    grid: list = field(default_factory=lambda: [0.0] * 12)   # 12 宫格平均分
    grid_n: list = field(default_factory=lambda: [0] * 12)


def analyze_accuracy(replay: Replay) -> dict:
    hands = {"left": HandStats("left"), "right": HandStats("right")}
    counts = {"good": 0, "bad": 0, "miss": 0, "bomb": 0}
    note_rows: list[dict] = []

    for idx, note in enumerate(replay.notes):
        p = note.params
        ev = note.event_type
        ev_name = {GOOD: "good", BAD: "bad", MISS: "miss", BOMB: "bomb"}[ev]
        counts[ev_name] += 1
        saber = note.saber
        hs = hands[saber] if saber in hands else None

        before = center = after = 0
        if ev == GOOD and note.cut is not None:
            before, center, after = cut_scores(note)
        note_score = before + center + after if ev == GOOD else 0

        if ev == GOOD and hs is not None:
            hs.good += 1
        elif ev == BAD and hs is not None:
            hs.bad += 1
        elif ev == MISS and hs is not None:
            hs.miss += 1
        elif ev == BOMB and hs is not None:
            hs.bomb += 1

        if ev == GOOD and note.cut is not None and hs is not None:
            c = note.cut
            st = p.scoring_type
            # —— 官方排除规则（ReplayStatisticUtils.Accuracy）——
            if st not in (SCORING_SLIDER_TAIL, SCORING_BURST_SLIDER_ELEMENT):
                hs.pre_score_avg += before
                hs.pre_rating_avg += c.before_cut_rating
                hs.pre_n += 1
            if st != SCORING_BURST_SLIDER_ELEMENT:
                hs.center_score_avg += center
                hs.cut_distance_cm_avg += c.cut_distance_to_center * 100.0
                hs.saber_speed_avg += c.saber_speed
                hs.time_devs.append(c.time_deviation)
                if c.time_deviation > 0:
                    hs.late_count += 1
                hs.time_dependence += abs(c.cut_normal[2]) if len(c.cut_normal) > 2 else 0.0
                hs.center_n += 1
                gi = p.note_line_layer * 4 + p.line_index
                if 0 <= gi <= 11:
                    hs.grid[gi] += note_score
                    hs.grid_n[gi] += 1
            if st not in (SCORING_SLIDER_HEAD, SCORING_BURST_SLIDER_HEAD,
                          SCORING_BURST_SLIDER_ELEMENT):
                hs.post_score_avg += after
                hs.post_rating_avg += c.after_cut_rating
                hs.post_n += 1

        note_rows.append({
            "idx": idx,
            "note_id": note.note_id,
            "event_time": round(note.event_time, 4),
            "spawn_time": round(note.spawn_time, 4),
            "event_type": ev,
            "saber": saber,
            "scoring_type": p.scoring_type,
            "line_index": p.line_index,
            "layer": p.note_line_layer,
            "color_type": p.color_type,
            "cut_direction": p.cut_direction,
            "before_score": before,
            "center_score": center,
            "after_score": after,
            "note_score": note_score,
            "cut_distance": (note.cut.cut_distance_to_center if note.cut else None),
            "saber_speed": (note.cut.saber_speed if note.cut else None),
            "time_deviation": (note.cut.time_deviation if note.cut else None),
        })

    out_hands = {}
    for name, hs in hands.items():
        n_pre, n_c, n_post = hs.pre_n, hs.center_n, hs.post_n
        grid_avg = [round(hs.grid[i] / hs.grid_n[i], 3) if hs.grid_n[i] else None
                    for i in range(12)]
        tds = hs.time_devs
        td_avg = sum(tds) / len(tds) if tds else 0.0
        td_abs_avg = sum(abs(x) for x in tds) / len(tds) if tds else 0.0
        if len(tds) > 1:
            var = sum((x - td_avg) ** 2 for x in tds) / (len(tds) - 1)
            td_std = math.sqrt(var)
        else:
            td_std = 0.0
        out_hands[name] = {
            "good": hs.good, "bad": hs.bad, "miss": hs.miss, "bomb": hs.bomb,
            "pre_score_avg": round(hs.pre_score_avg / n_pre, 4) if n_pre else 0.0,
            "center_score_avg": round(hs.center_score_avg / n_c, 4) if n_c else 0.0,
            "post_score_avg": round(hs.post_score_avg / n_post, 4) if n_post else 0.0,
            "pre_rating_avg": round(hs.pre_rating_avg / n_pre, 4) if n_pre else 0.0,
            "post_rating_avg": round(hs.post_rating_avg / n_post, 4) if n_post else 0.0,
            "cut_distance_cm_avg": round(hs.cut_distance_cm_avg / n_c, 4) if n_c else 0.0,
            "saber_speed_avg": round(hs.saber_speed_avg / n_c, 3) if n_c else 0.0,
            "time_dev_avg_ms": round(td_avg * 1000, 2),
            "time_dev_abs_avg_ms": round(td_abs_avg * 1000, 2),
            "time_dev_std_ms": round(td_std * 1000, 2),
            "late_ratio": round(hs.late_count / len(tds), 4) if tds else 0.0,
            "time_dependence": round(hs.time_dependence / n_c, 4) if n_c else 0.0,
            "pre_n": n_pre, "center_n": n_c, "post_n": n_post,
            "grid_acc": grid_avg,
        }

    return {
        "counts": counts,
        "hands": out_hands,
        "note_rows": note_rows,
    }
