"""Skill-track palette service (moved out of main.py, 2026-09).

Owns everything around the ACC-weighted skill model that is NOT routing: the
palette-cache row shape, the public track ids, palette tiers, per-option
availability and the API payloads.

The deterministic model itself stays in `backend/analysis/skill_model.py`; this
module only converts and persists, so the repository is passed in explicitly
instead of being reached for as a module global. That keeps `main.py` to routes,
validation and orchestration (the goal of the 2026-09 slimming pass).

Each track (80% / 94% / 96% accuracy) becomes its own selectable palette, but only
when the model could actually produce a rating for it. A track with insufficient
direct evidence stays out of the selectable set entirely: the settings UI greys it
out with its "not enough data" label (`scoresaber.track_insufficient`) — the model
never guesses a number to fill the gap.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from ..analysis import skill_model
from ..analysis.player_palette import build_tiers
from ..config.schema import get_star_palette

# Best track first: personal96 > personal94 > personal80.
PALETTE_TRACK_KEYS = ("personal96", "personal94", "personal80")
# The public ids are `personal<target>` (config enum, /api payloads, i18n). The
# cache columns kept short names (r80/r94/r96) — they are a storage detail and the
# live database already has them that way — so this map is the single translation
# point.
TRACK_CACHE_COLUMNS = {"personal96": "r96", "personal94": "r94", "personal80": "r80"}
# API convenience only: these labels ship inside the `/api/status` payload, but the
# frontend renders track names with its own i18n keys (`scoresaber.track_*`), so
# nothing reads them today. Kept so the payload keeps a human-readable label next
# to every id (the community entry has one too) — do not treat them as UI text.
TRACK_LABELS = {"personal80": "80% 基准", "personal94": "94% 基准", "personal96": "96% 基准"}


def track_column(key: str) -> str:
    return TRACK_CACHE_COLUMNS[key]


def track_label(key: str) -> str:
    return TRACK_LABELS[key]


def palette_cache_for(repo, platform: str, pid: str) -> dict | None:
    """The cached palette row for a player, or None when it carries no rating."""
    if not pid:
        return None
    cached = repo.get_player_palette(platform, pid)
    if not cached:
        return None
    if not any(cached.get(track_column(k)) is not None for k in PALETTE_TRACK_KEYS):
        return None
    return cached


def palette_meta(key: str, cached: dict) -> dict:
    column = track_column(key)
    return {
        "stars": cached.get(column),
        "direct_count": cached.get(f"{column}_direct"),
        "lower_bound": cached.get(f"{column}_lower_bound"),
        "confidence": cached.get(f"{column}_confidence"),
        "computed_at": cached.get("computed_at"),
    }


def palette_entries(cached: dict | None) -> list[dict]:
    """Palette definitions for every track that produced a rating."""
    entries: list[dict] = []
    for key in PALETTE_TRACK_KEYS:
        stars = cached.get(track_column(key)) if cached else None
        if stars is None:
            continue
        entries.append({
            "id": key,
            "label": track_label(key),
            "tiers": build_tiers(stars),
            "meta": palette_meta(key, cached),
        })
    return entries


def available_palette_keys(cached: dict | None) -> list[str]:
    return [e["id"] for e in palette_entries(cached)]


def palette_availability(cached: dict | None) -> dict:
    """Per-option availability for the settings UI.

    A track without a rating is reported as unavailable with a short factual
    reason, so the dropdown can grey it out instead of offering a choice that
    cannot work.
    """
    available = available_palette_keys(cached)
    options: dict = {"community": {"available": True}}
    for key in PALETTE_TRACK_KEYS:
        if key in available:
            options[key] = {"available": True, "meta": palette_meta(key, cached or {})}
        else:
            # A stable KEY, not a display string: the frontend looks it up as
            # `scoresaber.track_<reason>` so the reason is translated in the UI
            # language. Shipping the Chinese label here leaked Chinese into the
            # en/ja settings dropdown (2026-09).
            options[key] = {"available": False, "reason": skill_model.STATUS_INSUFFICIENT}
    return options


def active_palette_id(selected: str, cached: dict | None) -> str:
    """Selected palette id with safe fallback.

    Historic configs stored ``personal`` (the replaced classifier's single
    palette); it now means "whichever track is available", best first, so an
    existing choice keeps working instead of silently reverting to the community
    palette.
    """
    want = selected
    available = available_palette_keys(cached)
    if want == "personal":
        want = available[0] if available else "community"
    if want in PALETTE_TRACK_KEYS:
        return want if want in available else (available[0] if available else "community")
    return want if get_star_palette(want) else "community"


def palette_result(repo, platform: str, pid: str) -> dict | None:
    """/api payload of the cached palette for a player/platform (public ids)."""
    cached = repo.get_player_palette(platform, pid)
    if not cached:
        return None
    if not any(cached.get(track_column(k)) is not None for k in PALETTE_TRACK_KEYS):
        return None
    return palette_public_payload(cached)


def score_record_from_payload(s: dict) -> "skill_model.ScoreRecord | None":
    """One cloud score row -> model record.

    ACC follows the data source: ScoreSaber exposes baseScore / leaderboard.maxScore,
    BeatLeader reports accuracy as a percentage. Rows without a positive pp / max_pp
    are unranked or unranked-with-stars; the model rejects them anyway, so they are
    skipped here to keep the evidence list honest.
    """
    max_score = s.get("max_score")
    base_score = s.get("base_score")
    acc = s.get("accuracy")
    if isinstance(acc, (int, float)) and acc > 1.0:
        acc = acc / 100.0
    if acc is None and base_score and max_score:
        acc = base_score / max_score
    stars = s.get("stars")
    pp = s.get("pp")
    if not stars or not pp or not max_score or acc is None:
        return None
    timestamp = s.get("timepost")
    if timestamp is None:
        raw = s.get("time_set") or ""
        try:
            timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            timestamp = 0.0
    return skill_model.ScoreRecord(
        stars=float(stars),
        acc=float(acc),
        timestamp=float(timestamp or 0.0),
        leaderboard_key=f"{s.get('leaderboard_id')}|{s.get('song_hash')}",
        pp=float(pp),
        max_pp=float(max_score),
        modifiers=s.get("modifiers") or "",
    )


def palette_public_payload(cached: dict) -> dict:
    """Cache row -> API payload, translating cache columns to public ids.

    Cache stores ratings in short columns (r80/r94/r96); every API consumer sees
    them as `personal80/personal94/personal96` (plus `_direct` / `_lower_bound` /
    `_confidence` suffixes). ``method`` carries the selected track in public form.
    """
    payload: dict = {
        "status": "known" if cached.get("yellow_stars") is not None else "unknown",
        "yellow_stars": cached.get("yellow_stars"),
        "computed_at": cached.get("computed_at"),
        "skill_params": cached.get("skill_params"),
    }
    for key in PALETTE_TRACK_KEYS:
        column = track_column(key)
        payload[key] = cached.get(column)
        payload[f"{key}_direct"] = cached.get(f"{column}_direct")
        payload[f"{key}_lower_bound"] = cached.get(f"{column}_lower_bound")
        payload[f"{key}_confidence"] = cached.get(f"{column}_confidence")
    method = cached.get("method")
    if method not in PALETTE_TRACK_KEYS:
        # a cache row written before the ids were renamed still names the track "r80";
        # map it so the UI can still tell which baseline is in effect
        method = next((k for k in PALETTE_TRACK_KEYS if track_column(k) == method), None)
    payload["method"] = method
    return payload


def skill_palette_payload(repo, platform: str, pid: str, scores: list) -> dict:
    """Run the ACC-weighted skill model over freshly fetched scores and cache it.

    ``yellow_stars`` stays the colour anchor: the best track that produced a rating
    (personal96 > personal94 > personal80). Tracks without enough direct evidence
    stay NULL, which the settings UI turns into a greyed-out option labelled with
    its "not enough data" string — the model is not allowed to guess a number there.
    """
    records = [r for r in (score_record_from_payload(s) for s in scores) if r is not None]
    ratings = skill_model.rate_player(records, time.time())

    payload: dict = {
        "stage": None,
        "max_single_pp": max((r.pp for r in records), default=None),
        "fallback_stars": None,
        "yellow_stars": None,
        "sample_count": len(records),
        "method": "skill_model",
        "valid_count": len(records),
        "nf_excluded": None,
        "skill_params": skill_model.parameters(),
    }
    for track in skill_model.TRACKS:
        rating = ratings[track.key]
        column = track_column(track.key)           # cache columns are short (r80...)
        payload[column] = rating.stars
        payload[f"{column}_direct"] = rating.direct_count
        payload[f"{column}_lower_bound"] = rating.lower_bound_stars
        payload[f"{column}_confidence"] = rating.confidence if rating.stars is not None else None

    anchor = skill_model.best_available_track(ratings)
    if anchor is not None:
        visible = ratings[anchor.key]
        payload["yellow_stars"] = visible.stars
        payload["fallback_stars"] = visible.potential
        payload["method"] = anchor.key          # public id (personalNN)
    payload["computed_at"] = datetime.now(timezone.utc).isoformat()
    repo.save_player_palette(platform, pid, payload)
    # callers and the API see public ids; only the cache keeps the short columns
    return palette_public_payload(payload)
