"""Personal star palette: relative-to-player difficulty coloring (2026-08 spec).

Core idea: color means "map difficulty relative to THIS player's current
level", not absolute difficulty:

    grey   far below the player's level
    green  slightly below
    yellow matching the player's comfortable level  (the yellow baseline)
    red    clearly above
    purple extreme / far beyond

The yellow baseline is computed per player from their own ScoreSaber records:
the top-20 records by **pp** (time intentionally ignored — a player's recent
form can be unstable; pp reflects sustained ability), filtered to ranked /
stars>0 / pp>0 / no-NF.

Deterministic, pure computation: no LLM, no UI, no network. The caller decides
where records come from (cached ScoreSaber scores are enough; offline OK).

Spec boundaries (delta = map_stars - yellow_stars):
    grey   delta < -1.5
    green  -1.5 <= delta < -0.5
    yellow -0.5 <= delta <= +0.5
    red    +0.5 < delta <= +1.5
    purple delta > +1.5
"""
from __future__ import annotations

import statistics


def round_to_quarter(x: float) -> float:
    """Round to the nearest 0.25 (keeps tier boundaries tidy)."""
    return round(x * 4) / 4


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile (numpy-style default, deterministic)."""
    if not values:
        raise ValueError("percentile of empty list")
    vs = sorted(values)
    if len(vs) == 1:
        return vs[0]
    k = (len(vs) - 1) * p
    f = int(k)
    c = min(f + 1, len(vs) - 1)
    return vs[f] + (k - f) * (vs[c] - vs[f])


def _valid_records(records: list[dict]) -> list[dict]:
    """Keep only records usable for ability estimation:
    ranked, stars > 0, pp > 0, valid numbers, no NF modifier."""
    valid = []
    for r in records:
        if not r.get("ranked"):
            continue
        stars = r.get("stars")
        pp = r.get("pp")
        if not isinstance(stars, (int, float)) or stars <= 0:
            continue
        if not isinstance(pp, (int, float)) or pp <= 0:
            continue
        mods = (r.get("modifiers") or "").upper()
        if "NF" in mods:
            continue
        valid.append(r)
    return valid


# Fallback tiers by the player's single-best pp (used only when the sample is
# too small to trust percentiles; see classify_player).
_FALLBACK_BY_PP: list[tuple[float, str, float]] = [
    (200.0, "初级/休闲", 5.75),
    (350.0, "进阶/高阶", 7.00),
    (float("inf"), "竞技向", 8.75),
]


def classify_player(records: list[dict]) -> dict:
    """Estimate the player's yellow baseline from their ScoreSaber records.

    records: list of dicts with at least stars / pp / time_set / modifiers /
             ranked (any order; time is ignored for the sample selection).

    Returns:
      status: "unknown" (no valid records) or "known"
      valid_count / nf_excluded / sample_count / method
      max_single_pp / stage / fallback_stars
      yellow_stars (the baseline; None when unknown)
    """
    valid = _valid_records(records)
    nf_excluded = len(records) - len(valid)
    if not valid:
        return {"status": "unknown", "valid_count": 0, "nf_excluded": nf_excluded,
                "sample_count": 0, "method": "unknown",
                "max_single_pp": None, "stage": None, "fallback_stars": None,
                "yellow_stars": None}

    max_single_pp = max(r["pp"] for r in valid)
    stage = "竞技向"
    fallback = 8.75
    for threshold, name, fb in _FALLBACK_BY_PP:
        if max_single_pp < threshold:
            stage, fallback = name, fb
            break

    # Top 20 records by pp (time deliberately ignored: recent form can be
    # unstable; pp reflects sustained ability across difficulties).
    top = sorted(valid, key=lambda r: r["pp"], reverse=True)[:20]
    sample_stars = [r["stars"] for r in top]
    n = len(sample_stars)

    if n >= 20:
        q25 = percentile(sample_stars, 0.25)
        q50 = statistics.median(sample_stars)
        yellow = round_to_quarter((q25 + q50) / 2)
        method = "top20"
    elif n >= 8:
        personal = round_to_quarter((percentile(sample_stars, 0.25)
                                     + statistics.median(sample_stars)) / 2)
        yellow = round_to_quarter((personal + fallback) / 2)
        method = "blend8-19"
    else:
        yellow = fallback
        method = "fallback"

    return {
        "status": "known", "valid_count": len(valid),
        "nf_excluded": nf_excluded, "sample_count": n, "method": method,
        "max_single_pp": max_single_pp, "stage": stage,
        "fallback_stars": fallback, "yellow_stars": yellow,
    }


def build_tiers(yellow_stars: float) -> list[dict]:
    """Absolute tiers for the frontend's existing tier mechanism
    (first tier where stars < max wins; max=None = unbounded).

    The yellow/red boundary at delta=+0.5 is inclusive on the yellow side
    (stars == yellow + 0.5 stays yellow); the tier mechanism uses strict <,
    so a tiny epsilon keeps that exact value yellow (stars carry 2 decimals,
    yellow is a 0.25 multiple — no practical ambiguity).
    """
    eps = 1e-6
    return [
        {"max": round(yellow_stars - 1.5, 4), "cls": "star-gray"},
        {"max": round(yellow_stars - 0.5, 4), "cls": "star-green"},
        # eps added AFTER rounding: rounding first keeps the value tidy, then
        # the epsilon makes stars == yellow+0.5 stay yellow (strict < in tiers)
        {"max": round(yellow_stars + 0.5, 4) + eps, "cls": "star-yellow"},
        # red's right edge (delta = +1.5) is also inclusive per the spec
        {"max": round(yellow_stars + 1.5, 4) + eps, "cls": "star-red"},
        {"max": None, "cls": "star-purple"},
    ]
