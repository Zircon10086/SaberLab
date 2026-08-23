"""Port of the official scoring convention (authoritative source: BeatLeader
ReplayDecoder's ScoreCalculator.cs and ReplayStatisticUtils.cs).

Each cut scores up to 115 = Pre(70) + Center(15) + Post(30):
- before = clamp(round(70 * beforeCutRating), 0, 70); SliderTail fixed at 70;
  BurstSliderElement has no Pre.
- after  = clamp(round(30 * afterCutRating), 0, 30); SliderHead fixed at 30;
  BurstSliderHead fixed at 0; BurstSliderElement has no Post.
- center = round(15 * (1 - clamp(cutDistanceToCenter / 0.3)));
  BurstSliderElement fixed at 20 (also counted as 20 in maxScore).
Penalties: bad=-2, miss=-3, bomb=-4, wall=-5 (affect multiplier and combo).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..bsor.models import (
    NoteEvent, GOOD, BAD, MISS, BOMB,
    SCORING_SLIDER_HEAD, SCORING_SLIDER_TAIL,
    SCORING_BURST_SLIDER_HEAD, SCORING_BURST_SLIDER_ELEMENT,
)


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def cut_scores(note: NoteEvent) -> tuple[int, int, int]:
    """Return (before, center, after). Port of the official CutScoresForNote."""
    st = note.params.scoring_type
    c = note.cut
    before = after = center = 0
    if c is None:
        return 0, 0, 0
    if st != SCORING_BURST_SLIDER_ELEMENT:
        if st == SCORING_SLIDER_TAIL:
            before = 70
        else:
            before = int(clamp(round(70 * c.before_cut_rating), 0, 70))
    if st != SCORING_BURST_SLIDER_ELEMENT:
        if st == SCORING_BURST_SLIDER_HEAD:
            after = 0
        elif st == SCORING_SLIDER_HEAD:
            after = 30
        else:
            after = int(clamp(round(30 * c.after_cut_rating), 0, 30))
    if st == SCORING_BURST_SLIDER_ELEMENT:
        center = 20
    else:
        center = int(round(15 * (1 - clamp(c.cut_distance_to_center / 0.3))))
    return before, center, after


def score_for_note(note: NoteEvent) -> int:
    """Port of the official ScoreForNote."""
    if note.event_type == GOOD:
        b, c, a = cut_scores(note)
        return b + c + a
    if note.event_type == BAD:
        return -2
    if note.event_type == MISS:
        return -3
    if note.event_type == BOMB:
        return -4
    return -1


class MultiplierCounter:
    """Port of the official MultiplierCounter (x1→x8, one level up per 2 consecutive)."""

    def __init__(self):
        self.multiplier = 1
        self.progress = 0
        self.max_progress = 2

    def increase(self):
        if self.multiplier >= 8:
            return
        if self.progress < self.max_progress:
            self.progress += 1
        if self.progress >= self.max_progress:
            self.multiplier *= 2
            self.progress = 0
            self.max_progress = self.multiplier * 2

    def decrease(self):
        if self.progress > 0:
            self.progress = 0
        if self.multiplier > 1:
            self.multiplier //= 2
            self.max_progress = self.multiplier * 2


@dataclass
class ScoreResult:
    total_score: int
    accuracy: float          # BeatLeader-convention cumulative accuracy (at the last block note)
    max_combo: int
    score_graph: list[float]  # per-second accuracy curve (official ScoreGraph convention)
    block_accuracy: list[tuple]  # (event time, official running_accuracy), one point per block note
    # (2026-08: per-note curve data source — same convention as replay records / 3D playback)


def compute_score(replay) -> ScoreResult:
    """Combined port of the official Accuracy()/ScoreGraph().

    Events sorted by time (notes + walls as penalty events with score=-5);
    the multiplier increases on good and decreases on bad/miss/bomb/wall.
    """
    structs = []
    for note in replay.notes:
        structs.append({
            "time": note.event_time,
            "score": score_for_note(note),
            "scoring_type": note.params.scoring_type,
            "is_block": note.params.color_type != 2,
        })
    for wall in replay.walls:
        structs.append({"time": wall.time, "score": -5,
                        "scoring_type": -1, "is_block": False})
    structs.sort(key=lambda s: s["time"])

    multiplier = 1
    score = 0
    combo = 0
    max_combo = 0
    max_score = 0
    max_counter = MultiplierCounter()
    normal_counter = MultiplierCounter()
    last_accuracy = 0.0
    prev_accuracy = 0.0
    block_accuracy: list[tuple] = []

    for i, s in enumerate(structs):
        score_for_max = 20 if s["scoring_type"] == SCORING_BURST_SLIDER_ELEMENT else 115
        max_counter.increase()
        max_score += max_counter.multiplier * score_for_max

        if s["score"] < 0:
            normal_counter.decrease()
            multiplier = normal_counter.multiplier
            combo = 0
        else:
            normal_counter.increase()
            combo += 1
            multiplier = normal_counter.multiplier
            score += multiplier * s["score"]
        if combo > max_combo:
            max_combo = combo
        # Official convention: block notes record totalScore/maxScore; others carry the previous value
        if s["is_block"]:
            prev_accuracy = (score / max_score) if max_score > 0 else 0.0
            block_accuracy.append((s["time"], prev_accuracy))
        elif i == 0:
            prev_accuracy = 0.0
        s["running_accuracy"] = prev_accuracy
        if s["is_block"]:
            last_accuracy = prev_accuracy

    # ScoreGraph: per-second average running-accuracy (official ScoreGraph port)
    graph: list[float] = []
    if structs:
        end_time = int(structs[-1]["time"])
        idx = 0
        for sec in range(end_time):
            cumulative = 0.0
            n = 0
            while idx < len(structs) and structs[idx]["time"] < sec + 1:
                cumulative += structs[idx]["running_accuracy"]
                idx += 1
                n += 1
            val = (cumulative / n) if n > 0 else 0.0
            if val == 0:
                val = 1.0 if sec == 0 else graph[sec - 1]
            graph.append(val)

    return ScoreResult(total_score=score, accuracy=last_accuracy,
                       max_combo=max_combo, score_graph=graph,
                       block_accuracy=block_accuracy)
