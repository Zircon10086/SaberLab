"""BeatLeader API client (dual-platform cloud data, 2026-08).

The player ID is SHARED with ScoreSaber (= Steam ID, parsed from BSOR), so
`player_id` works for both platforms without any user input.

Output structures of fetch_profile / fetch_scores are aligned with
scoresaber.py so the personal-palette classify_player() and the cloud page
consume both platforms identically (the frontend only sees platform-specific
values; rows are stored per platform in the same cache tables).

Measured BeatLeader API facts (2026-08):
- GET /player/{id}                      -> profile (scoreStats.averageRankedAccuracy is 0-1)
- GET /player/{id}/scores?page&count    -> {metadata, data: [score...]}
- GET /leaderboards/hash/{hash}         -> {leaderboards: [...], song: {...}} (all difficulties in one call)
- score.modifiers is a comma-separated string: "" / "NF" / "SF,NF" ...
- leaderboard.difficulty.status: 3 = Ranked (stars + pp eligible);
  5/7 = official OST / unrated-with-stars: stars exist but NO pp is produced
  (user decision: OST maps show stars but never pp)
- leaderboard.song.hash is lowercase (normalized to upper, like ScoreSaber)
- difficulty.stars may be null (not rated)
- timepost is unix seconds
"""
from __future__ import annotations

import json
import pathlib
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

BASE = "https://api.beatleader.xyz"

# BeatLeader difficulty.status semantics (2026-08, user-verified against the
# official API docs): 3 = Ranked (the ONLY formally ranked state). All other
# statuses (0 None / 1 Unranked / 2 Qualified / 5,7 OST etc.) are NOT formally
# ranked — and because BeatLeader's star algorithm is PUBLIC, unranked maps
# often still carry stars (unlike ScoreSaber, whose black-box algorithm means
# stars imply ranked). So stars alone never imply ranked on BeatLeader.
#
# Recommended rules (used throughout SaberLab):
#   isRanked                = (status == 3)
#   isUsableRankedScore     = (status == 3 and stars > 0 and pp > 0)
# A stars-without-pp record must NOT count toward ranked score analysis
# (player ability / top-single-pp / yellow baseline etc.).
RANKED_STATUS = 3


def classify_record(record: dict) -> dict:
    """Classify one BeatLeader record by its formal ranked status.

    record: dict with difficulty.status / difficulty.stars / pp (as returned
    by fetch_scores or the raw API item).

    Returns {category: "ranked"|"unranked", usable: bool, reason: str}.
    """
    status = (record.get("difficulty") or {}).get("status")
    stars = (record.get("difficulty") or {}).get("stars")
    pp = record.get("pp")

    def ok(v):
        return isinstance(v, (int, float)) and v > 0

    has_stars, has_pp = ok(stars), ok(pp)
    if status == RANKED_STATUS:
        if has_stars and has_pp:
            return {"category": "ranked", "usable": True,
                    "reason": "ranked"}
        return {"category": "ranked", "usable": False,
                "reason": "ranked，但 stars 或 pp 数据不完整"}
    return {"category": "unranked", "usable": False,
            "reason": "歌曲不是正式 ranked"}


class BeatLeaderError(Exception):
    """Network/API failure. status=None means a transport error (retryable)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _get(cfg, url: str):
    req = urllib.request.Request(
        url, headers={"User-Agent": "SaberLab/2.0 (BeatLeader integration)"})
    try:
        with urllib.request.urlopen(req, timeout=getattr(cfg, "timeout_seconds", 30)) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise BeatLeaderError(f"BeatLeader API 请求失败: {url}: {e}", status=e.code) from e
    except OSError as e:
        raise BeatLeaderError(f"BeatLeader API 请求失败: {url}: {e}") from e


def _iso_time(timepost) -> str | None:
    """unix seconds -> ISO string (aligned with scoresaber fetch_scores time_set)."""
    if not isinstance(timepost, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(timepost), tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def fetch_profile(cfg, player_id: str) -> dict:
    """Player profile, field-aligned with scoresaber.fetch_profile (the
    frontend cloud page reads name/country/rank/countryRank/pp/scoreStats)."""
    p = _get(cfg, f"{BASE}/player/{player_id}")
    stats = p.get("scoreStats") or {}
    avg_acc = stats.get("averageRankedAccuracy")
    return {
        "name": p.get("name"),
        "country": p.get("country"),
        "rank": p.get("rank"),
        "countryRank": p.get("countryRank"),
        "pp": p.get("pp"),
        "scoreStats": {
            # BeatLeader returns 0-1; the frontend divides by 100 (ScoreSaber
            # convention), so normalize to percent here.
            "averageRankedAccuracy": (avg_acc * 100) if isinstance(avg_acc, (int, float)) else None,
            "totalPlayCount": stats.get("totalPlayCount"),
            "rankedPlayCount": stats.get("rankedPlayCount"),
        },
    }


def fetch_scores(cfg, player_id: str, limit: int = 100,
                 max_pages: int = 3) -> list[dict]:
    """Recent scores, field-aligned with scoresaber.fetch_scores (so
    classify_player() and the frontend table work unchanged).

    ranked = difficulty.status == 3 (Ranked). Non-ranked maps (incl. official
    OST status 5/7) keep their stars in the row; pp is only meaningful on
    ranked maps (beatleader returns pp=0 elsewhere).
    """
    out: list[dict] = []
    page = 1
    per_page = min(max(1, limit), 100)
    while len(out) < limit and page <= max_pages:
        data = _get(cfg, f"{BASE}/player/{player_id}/scores"
                         f"?page={page}&count={per_page}&sortBy=date")
        batch = data.get("data") or []
        if not batch:
            break
        for item in batch:
            lb = item.get("leaderboard") or {}
            song = lb.get("song") or {}
            diff = lb.get("difficulty") or {}
            out.append({
                "score_id": item.get("id"),
                "time_set": _iso_time(item.get("timepost")),
                "timepost": item.get("timepost"),
                "score": item.get("modifiedScore") or item.get("baseScore"),
                "pp": item.get("pp"),
                "weight": item.get("weight"),
                "rank": item.get("rank"),
                "bad_cuts": item.get("badCuts"),
                "missed_notes": item.get("missedNotes"),
                "max_combo": item.get("maxCombo"),
                "full_combo": item.get("fullCombo"),
                "modifiers": item.get("modifiers") or "",
                "has_replay": bool(item.get("replay")),
                "device_hmd": item.get("hmd"),
                "leaderboard_id": lb.get("id"),
                "song_hash": (song.get("hash") or "").upper(),
                "song_name": song.get("name"),
                "song_author": song.get("author"),
                "level_author": song.get("mapper"),
                "difficulty": diff.get("difficultyName"),
                "difficulty_rank": diff.get("value"),
                "stars": diff.get("stars"),
                # status == 3 is the ONLY formally ranked state; other
                # statuses (incl. OST 5/7) are unranked even when they carry
                # stars (BL's star algorithm is public)
                "ranked": 1 if diff.get("status") == RANKED_STATUS else 0,
                "max_pp": None,
            })
        if len(batch) < per_page:
            break
        page += 1
    return out[:limit]


def build_ranked_index(cfg, player_id: str) -> dict[tuple, dict]:
    """(song_hash, difficulty) -> {stars, pp} from the player's own scores.

    pp is only recorded for ranked maps (status==3): official OST maps
    (status 5/7) show stars but NEVER pp (user decision). Stars are kept for
    any map that has them.
    """
    idx: dict[tuple, dict] = {}
    try:
        scores = fetch_scores(cfg, player_id, limit=100)
    except BeatLeaderError:
        return idx
    for s in scores:
        if not s.get("song_hash"):
            continue
        key = (s["song_hash"].upper(), s.get("difficulty") or "")
        # isUsableRankedScore: pp only for status==3 AND pp>0. Unranked maps
        # (status != 3) show stars but NEVER produce pp (user decision).
        pp = s.get("pp") if s.get("ranked") and s.get("pp") else None
        idx[key] = {"stars": s.get("stars"), "pp": pp}
    return idx


def sync_map_leaderboards(cfg, repo, map_hash: str, force: bool = False) -> dict:
    """Fetch ALL difficulties of one map from BeatLeader and cache them
    (platform-scoped). One request covers the whole map (vs ScoreSaber's
    get-difficulties + by-id two-step). Returns {total, cached, fetched, failed}.

    404 = the map is not on BeatLeader (custom/not uploaded).
    """
    from datetime import datetime, timezone as _tz

    try:
        data = _get(cfg, f"{BASE}/leaderboards/hash/{map_hash.lower()}")
    except BeatLeaderError as e:
        if e.status == 404:
            return {"total": 0, "cached": 0, "fetched": 0, "failed": 0,
                    "map_hash": map_hash, "not_on_scoresaber": True}
        raise
    lbs = data.get("leaderboards") or []
    song = data.get("song") or {}
    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    stats = {"total": len(lbs), "cached": 0, "fetched": 0, "failed": 0,
             "map_hash": map_hash}
    if not lbs:
        stats["not_on_scoresaber"] = True
        return stats

    for lb in lbs:
        lbid = lb.get("id")
        diff = lb.get("difficulty") or {}
        if not lbid:
            stats["failed"] += 1
            continue
        cached = repo.get_ss_leaderboard(lbid, platform="beatleader")
        if cached and not force and cached.get("last_synced"):
            stats["cached"] += 1
            continue
        status = diff.get("status") or 0
        repo.upsert_ss_leaderboard({
            "leaderboard_id": lbid,
            "map_hash": map_hash.upper(),
            "difficulty_rank": diff.get("value"),
            "difficulty_name": diff.get("difficultyName"),
            "game_mode": diff.get("modeName") or "",
            "difficulty_raw": None,
            "song_name": song.get("name"),
            "level_author": song.get("mapper"),
            "stars": diff.get("stars"),
            # RANKED_STATUS is the only formally ranked state; everything else
            # (incl. OST 5/7) is unranked but MAY carry stars (public star
            # algorithm) — the UI marks such stars as unranked
            "ranked": 1 if status == RANKED_STATUS else 0,
            "qualified": 1 if status == 2 else 0,
            "loved": None,
            "max_pp": None,
            "plays": None,
            "last_synced": now,
        }, platform="beatleader")
        stats["fetched"] += 1
    return stats


def sync_maps_batch(cfg, repo, map_hashes: list[str],
                    progress_cb=None, force: bool = False,
                    workers: int = 8, only_missing: bool = False) -> dict:
    """Batch-sync leaderboard metadata for multiple maps (background task).

    Concurrent per-hash requests (BeatLeader has no 43s first-request penalty
    like ScoreSaber, so a simple thread pool is enough). Incremental mode
    (only_missing) skips maps that already have leaderboard cache.
    """
    total_stats = {"maps": len(map_hashes), "fetched": 0, "cached": 0,
                   "failed": 0, "network_failed": 0, "not_on_scoresaber": 0,
                   "failed_songs": []}
    if not map_hashes:
        return total_stats

    pending = list(map_hashes)
    if only_missing:
        pending = [mh for mh in map_hashes
                   if not repo.get_ss_leaderboards_by_hash(mh, platform="beatleader")]
        total_stats["cached"] = len(map_hashes) - len(pending)
    total_hashes = len(pending)

    done = 0
    fail_count: dict[str, int] = {}
    lock = threading.Lock()

    def sync_one(mh: str):
        nonlocal done
        try:
            s = sync_map_leaderboards(cfg, repo, mh, force=force)
        except BeatLeaderError as e:
            s = {"failed": 1, "network_failed": 1 if e.status is None else 0}
        with lock:
            for k in ("fetched", "cached", "failed", "network_failed"):
                total_stats[k] += s.get(k, 0)
            if s.get("not_on_scoresaber"):
                total_stats["not_on_scoresaber"] += 1
            done += 1
            if progress_cb:
                progress_cb(done, total_hashes, mh[:12])
        if s.get("failed"):
            with lock:
                fail_count[mh] = fail_count.get(mh, 0) + 1
            return mh
        return None

    while pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(sync_one, pending))
        retry = []
        for mh, res in zip(pending, results):
            if res is None:
                continue
            if fail_count[res] < 3:
                retry.append(res)
            else:
                row = repo.get_map(res)
                name = (row or {}).get("song_name") or res[:16]
                total_stats["failed_songs"].append(name)
        pending = retry
    return total_stats
