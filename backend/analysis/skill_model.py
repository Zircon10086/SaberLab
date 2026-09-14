"""ACC-weighted player skill model (2026-09 spec).

Answers "which star rating can this player hold **at a given accuracy**", instead of
"what is their best play". Three complementary tracks share one physical model and one
official PP curve, differing only in the target accuracy, the evidence they accept and
the extrapolation limit:

    R96  precision  96%  high-accuracy capability
    R94  standard   94%  default map picking / colour baseline
    R80  challenge  80%  what they can clear when accuracy is not the goal

Spec: ``docs/ACC_WEIGHTED_SKILL_MODEL.md`` (model + parameters) and
``docs/ACC_WEIGHTED_SKILL_MODEL_VALIDATION.md`` (direct-evidence gate, lower bounds,
insufficient-data rules). This module implements those documents; when they change the
comments here must change with them.

Design rules from the spec (do not "improve" these silently):
  * the accuracy difference is expressed as ``ln(curve(acc))``, never as a plain
    percentage difference -- 94%->96% carries far more value than 80%->82%;
  * difficulty uses a linear star axis (``GAMMA = 1``); high-star plays get more
    *evidence* weight, which is a modelling choice, not a ScoreSaber fact;
  * a record only contributes a number to a track when it is close enough to that
    track's target accuracy (``DIRECT_TRANSFER_LIMIT``); farther records are still
    reported as an observed lower bound, never extrapolated into a fake figure;
  * "not enough evidence" is a legitimate answer (``DISPLAY_INSUFFICIENT``), not a
    reason to fall back to a different method.

Deterministic, pure functions: no network, no LLM, no UI, no database access. The
caller decides where records come from.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from .pp_predict import curve_multiplier

# ---------------------------------------------------------------------------
# Model parameters (spec defaults; only the calibration script may refit these)
# ---------------------------------------------------------------------------

#: Difficulty-coordinate exponent. The spec's evidence does NOT support gamma > 1,
#: so the axis stays linear and high-star influence is expressed by RHO below.
GAMMA = 1.0
#: Difficulty units of ACC-utility travel per star (initial calibration).
LAMBDA = 0.09
#: Star coordinate cap used by the evidence weight only.
STAR_CAP = 14.0
#: A 14★ record carries at most (1 + RHO) times the weight of a low-star record.
RHO = 1.0
#: A record enters a track's numeric estimate only within this many difficulty units
#: of that track's target accuracy (validation doc: "direct evidence only").
DIRECT_TRANSFER_LIMIT = 2.0
#: Accuracy clamp: below 80% the curve is not meaningful for this model (the validation
#: doc excludes those records rather than clamping them into the 80% track).
ACC_MIN = 0.80
ACC_MAX = 0.995
#: Recency half-life in days.
RECENCY_HALF_LIFE_DAYS = 180.0
#: Aggregation sizes and mix.
POTENTIAL_TOP_N = 20
CURRENT_LATEST_N = 30
POTENTIAL_WEIGHT = 0.70
#: Below this many direct records a track reports "insufficient data".
MIN_DIRECT_RECORDS = 8

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient"

INSUFFICIENT_LABEL = "数据不足"


@dataclass(frozen=True)
class Track:
    """One rating track: target accuracy + how far records may be extrapolated."""

    key: str
    target_acc: float


TRACKS: tuple[Track, ...] = (
    Track("personal96", 0.96),
    Track("personal94", 0.94),
    Track("personal80", 0.80),
)

DEFAULT_STAR_PALETTE = "community"


# ---------------------------------------------------------------------------
# Curve-derived helpers
# ---------------------------------------------------------------------------


def acc_utility(acc: float) -> float:
    """Utility of an accuracy value: ``ln(curve(acc))`` on the official PP curve.

    The clamp keeps the steep 99%+ end of the curve (and the near-zero <80% end) from
    letting a single play dominate the estimate.
    """
    safe = min(max(float(acc), ACC_MIN), ACC_MAX)
    multiplier = curve_multiplier(safe)
    if multiplier <= 0:
        return 0.0
    return math.log(multiplier)


def difficulty_axis(stars: float) -> float:
    """Star rating on the model's difficulty axis (linear when GAMMA == 1)."""
    stars = max(float(stars), 0.0)
    return STAR_CAP * (stars / STAR_CAP) ** GAMMA


def inverse_difficulty_axis(value: float) -> float:
    """Inverse of :func:`difficulty_axis`."""
    return STAR_CAP * (max(float(value), 0.0) / STAR_CAP) ** (1.0 / GAMMA)


def star_evidence_weight(stars: float) -> float:
    """How much say a record gets when aggregating: 1 at 0★, 2 at 14★."""
    normalized = min(max(float(stars) / STAR_CAP, 0.0), 1.0)
    return 1.0 + RHO * normalized**2


def weighted_median(values: list[float], weights: list[float]) -> float | None:
    """Weighted median (lower value wins on an exact 50% split). None when empty."""
    pairs = [(v, w) for v, w in zip(values, weights) if w > 0]
    if not pairs:
        return None
    pairs.sort(key=lambda p: p[0])
    total = sum(w for _, w in pairs)
    acc = 0.0
    for value, weight in pairs:
        acc += weight
        if acc >= total / 2.0:
            return value
    return pairs[-1][0]


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@dataclass
class ScoreRecord:
    """One ranked ScoreSaber play, in the fields this model needs.

    ``acc`` must be the accuracy ScoreSaber scored the play at
    (``baseScore / leaderboard.maxScore``). SaberLab's replay ``accuracy`` is the same
    quantity (verified against the pp curve: median deviation 4e-6, max 1e-3).
    """

    stars: float
    acc: float
    timestamp: float = 0.0
    leaderboard_key: str = ""
    pp: float = 0.0
    max_pp: float = 0.0
    modifiers: str = ""

    def usable(self) -> bool:
        """Phase 1 of the spec's filter: ranked, starred, scored, unmodified."""
        return (self.stars > 0 and self.max_pp > 0 and self.pp > 0
                and (self.modifiers or "") == ""
                and ACC_MIN <= self.acc <= ACC_MAX)


def usable_records(records: list[ScoreRecord]) -> list[ScoreRecord]:
    """Filter to the plays the model accepts, one per leaderboard (best accuracy wins)."""
    by_key: dict[str, ScoreRecord] = {}
    for rec in records:
        if not rec.usable():
            continue
        key = rec.leaderboard_key or f"{rec.stars}:{rec.acc}:{rec.timestamp}"
        current = by_key.get(key)
        if current is None or rec.acc > current.acc:
            by_key[key] = rec
    return list(by_key.values())


def raw_transfer(record: ScoreRecord, track: Track) -> float:
    """Difficulty-axis travel needed to move this play onto the track's target ACC."""
    return (acc_utility(record.acc) - acc_utility(track.target_acc)) / LAMBDA


def equivalent_stars(record: ScoreRecord, track: Track) -> float | None:
    """Stars this play implies at the track's target ACC, or None without direct
    evidence (|transfer| beyond :data:`DIRECT_TRANSFER_LIMIT`)."""
    transfer = raw_transfer(record, track)
    if abs(transfer) > DIRECT_TRANSFER_LIMIT:
        return None
    axis = difficulty_axis(record.stars) + transfer
    return inverse_difficulty_axis(max(axis, 0.01))


def evidence_weight(record: ScoreRecord, track: Track, now: float) -> float:
    """Star weight x accuracy-proximity weight x recency weight."""
    transfer = abs(raw_transfer(record, track))
    acc_weight = math.exp(-0.5 * (transfer / DIRECT_TRANSFER_LIMIT) ** 2)
    age_days = max((now - record.timestamp) / 86400.0, 0.0) if record.timestamp else 0.0
    recency = 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)
    return star_evidence_weight(record.stars) * acc_weight * recency


@dataclass
class TrackRating:
    """Result for one track."""

    key: str
    target_acc: float
    status: str
    stars: float | None = None
    direct_count: int = 0
    potential: float | None = None
    current: float | None = None
    lower_bound_stars: float | None = None
    spread: float | None = None
    confidence: str = "low"


def _confidence(direct: list[ScoreRecord], now: float) -> tuple[str, float | None]:
    """Coarse confidence from evidence count, star coverage and recency."""
    if len(direct) < MIN_DIRECT_RECORDS:
        return "low", None
    stars = [r.stars for r in direct]
    coverage = max(stars) - min(stars)
    recent = sum(1 for r in direct
                 if r.timestamp and (now - r.timestamp) / 86400.0 <= 60.0)
    level = "low"
    if len(direct) >= 30 and coverage >= 2.0 and recent >= 8:
        level = "high"
    elif len(direct) >= 12 and coverage >= 1.0:
        level = "medium"
    return level, coverage


def rate_track(records: list[ScoreRecord], track: Track, now: float) -> TrackRating:
    """Rate one track over already-filtered, deduplicated usable records.

    ``potential`` uses the best 20 equivalents; ``current`` the latest 30 -- form is
    part of the answer, but old personal bests must not dominate forever.
    """
    direct: list[tuple[ScoreRecord, float, float]] = []   # record, equivalent, weight
    for rec in records:
        equiv = equivalent_stars(rec, track)
        if equiv is None:
            continue
        direct.append((rec, equiv, evidence_weight(rec, track, now)))

    lower_bound = max((r.stars for r in records if r.acc >= track.target_acc), default=None)

    if len(direct) < MIN_DIRECT_RECORDS:
        return TrackRating(
            key=track.key,
            target_acc=track.target_acc,
            status=STATUS_INSUFFICIENT,
            direct_count=len(direct),
            lower_bound_stars=lower_bound,
        )

    top = sorted(direct, key=lambda item: item[1], reverse=True)[:POTENTIAL_TOP_N]
    potential = weighted_median([e for _, e, _ in top], [w for _, _, w in top])

    latest = sorted(direct, key=lambda item: item[0].timestamp or 0.0,
                    reverse=True)[:CURRENT_LATEST_N]
    current = weighted_median([e for _, e, _ in latest], [w for _, _, w in latest])

    if len(latest) < MIN_DIRECT_RECORDS:
        current = potential

    if potential is None:
        return TrackRating(key=track.key, target_acc=track.target_acc,
                           status=STATUS_INSUFFICIENT, direct_count=len(direct),
                           lower_bound_stars=lower_bound)
    if current is None:
        current = potential

    stars = POTENTIAL_WEIGHT * potential + (1.0 - POTENTIAL_WEIGHT) * current
    confidence, coverage = _confidence([r for r, _, _ in direct], now)
    return TrackRating(
        key=track.key,
        target_acc=track.target_acc,
        status=STATUS_OK,
        stars=stars,
        direct_count=len(direct),
        potential=potential,
        current=current,
        lower_bound_stars=lower_bound,
        spread=coverage,
        confidence=confidence,
    )


def enforce_monotonic(ratings: dict[str, TrackRating]) -> None:
    """Keep R96 >= R94 >= R80 (spec product rule 6) by clamping in place.

    The order is a property of the model -- hitting 96% can never be easier than
    hitting 80% -- so a violation means the evidence or parameters are inconsistent.
    Ratings that are merely "insufficient" are left alone.
    """
    ok = [r for r in (ratings.get("personal96"), ratings.get("personal94"), ratings.get("personal80"))
          if r is not None and r.status == STATUS_OK and r.stars is not None]
    if len(ok) < 2:
        return
    previous: float | None = None
    for rating in ok:
        if previous is not None and rating.stars is not None and rating.stars > previous:
            rating.stars = previous
        previous = rating.stars


def rate_player(records: list[ScoreRecord], now: float) -> dict[str, TrackRating]:
    """Rate all three tracks over the same evidence set."""
    usable = usable_records(records)
    ratings = {track.key: rate_track(usable, track, now) for track in TRACKS}
    enforce_monotonic(ratings)
    return ratings


def available_track_keys(ratings: dict[str, TrackRating]) -> list[str]:
    """Track keys that may be offered to the user (evidence sufficient), best first."""
    order = [t.key for t in TRACKS]
    return [k for k in order if ratings.get(k) and ratings[k].status == STATUS_OK]


def best_available_track(ratings: dict[str, TrackRating]) -> Track | None:
    """The strongest track that produced a rating (personal96 > personal94 > personal80), or None."""
    available = available_track_keys(ratings)
    if not available:
        return None
    by_key = {t.key: t for t in TRACKS}
    return by_key[available[0]]


def parameters() -> dict:
    """The parameter set in force, so cached results can be traced to a model version."""
    return {
        "gamma": GAMMA,
        "lambda": LAMBDA,
        "rho": RHO,
        "star_cap": STAR_CAP,
        "direct_transfer_limit": DIRECT_TRANSFER_LIMIT,
        "acc_min": ACC_MIN,
        "acc_max": ACC_MAX,
        "recency_half_life_days": RECENCY_HALF_LIFE_DAYS,
        "potential_top_n": POTENTIAL_TOP_N,
        "current_latest_n": CURRENT_LATEST_N,
        "potential_weight": POTENTIAL_WEIGHT,
        "min_direct_records": MIN_DIRECT_RECORDS,
    }


def predicted_acc(map_stars: float, rating_stars: float) -> float | None:
    """Accuracy predicted for a map of ``map_stars`` from a rating at 94% ... or the
    track's own target.

    Kept for the (backlogged) prediction-based colour bands; returns None when the
    curve cannot be inverted (accuracy beyond the clamped range).
    """
    utility = acc_utility(0.94) + LAMBDA * (
        difficulty_axis(rating_stars) - difficulty_axis(map_stars)
    )
    target = math.exp(utility)
    if target <= 0:
        return None
    lo, hi = ACC_MIN, 1.0
    if target <= curve_multiplier(lo):
        return ACC_MIN
    if target >= curve_multiplier(hi):
        return hi
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if curve_multiplier(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0
