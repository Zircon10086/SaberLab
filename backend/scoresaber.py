"""ScoreSaber API client (unofficial public API, used only for verification and display).

Endpoints (verified working):
- GET https://scoresaber.com/api/player/{id}/full    -> player profile
- GET https://scoresaber.com/api/player/{id}/scores?limit=N&sort=recent|top&page=N
"""
from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .config import Config

BASE = "https://scoresaber.com/api"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 SaberLab/1.0")

# ScoreSaber difficulty name -> Replay/BSOR difficulty name
_DIFF_NORM = {
    "easy": "Easy", "normal": "Normal", "hard": "Hard",
    "expert": "Expert", "expert+": "ExpertPlus", "expertplus": "ExpertPlus",
}


def norm_difficulty(name) -> str:
    if not name:
        return ""
    return _DIFF_NORM.get(str(name).strip().lower(), str(name).strip())


class ScoreSaberError(Exception):
    """A ScoreSaber request failed. status: HTTP status code (int), None = network/parse error.

    Distinguishing the two is key to cache correctness (P0-2.4):
    - status=404 -> the map truly is not on ScoreSaber; a "not found" cache can be written safely
    - status=None (timeout/offline/5xx) -> never write "not found", otherwise the cache is
      poisoned: one offline moment loses stars for a whole batch of maps, recoverable only by
      manually clearing the cache
    """

    def __init__(self, msg: str, status: Optional[int] = None):
        super().__init__(msg)
        self.status = status


# One persistent HTTPS connection per thread (http.client explicitly reuses keep-alive).
# Background (measured): ScoreSaber adds a fixed ~43s delay to the first request on a
# new TCP connection; subsequent requests on the same connection take ~0.2s.
# urllib.request no longer has a keep-alive connection pool in Python 3.12+
# (every urlopen opens a new connection), so http.client connections are managed
# directly here: after the first request eats the 43s, all later requests on this
# thread are millisecond-fast.
_conn_local = threading.local()
_REQUEST_TIMEOUT = 120.0  # socket timeout (a 43s first request is normal; 30s would kill it spuriously)
# 429 (rate limit) backoff: millisecond bursts on a reused connection trigger
# ScoreSaber rate limiting (measured: ~1/3 of by-id requests got 429 under
# 8-way concurrency). Retry after backoff so each sync does not end up with a
# fixed batch of failures that never write cache.
_429_RETRY_DELAYS = (1.0, 2.0, 4.0)


def _conn(cfg: Config) -> http.client.HTTPSConnection:
    cached = getattr(_conn_local, "conn", None)
    if cached is not None:
        return cached
    conn = http.client.HTTPSConnection("scoresaber.com",
                                       timeout=_REQUEST_TIMEOUT)
    if cfg.proxy:
        # Proxy: CONNECT tunnel (keep the existing proxy support)
        proxy = urllib.parse.urlsplit(cfg.proxy if "://" in cfg.proxy
                                      else f"http://{cfg.proxy}")
        conn.set_tunnel(proxy.hostname, proxy.port or 443)
    _conn_local.conn = conn
    return conn


def _drop_conn():
    conn = getattr(_conn_local, "conn", None)
    _conn_local.conn = None
    if conn is not None:
        try:
            conn.close()
        except OSError:
            pass


def _get(cfg: Config, url: str):
    parsed = urllib.parse.urlsplit(url)
    target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    retries = 0
    while True:
        conn = _conn(cfg)
        try:
            conn.request("GET", target, headers={"User-Agent": UA,
                                                 "Accept": "application/json"})
            resp = conn.getresponse()
            body = resp.read()
            if resp.status == 429 and retries < len(_429_RETRY_DELAYS):
                # Rate limited: back off and retry (the connection itself can keep being reused)
                time.sleep(_429_RETRY_DELAYS[retries])
                retries += 1
                continue
            if resp.status >= 400:
                raise ScoreSaberError(
                    f"ScoreSaber API HTTP {resp.status}: {url}",
                    status=resp.status)
            return json.loads(body.decode("utf-8"))
        except ScoreSaberError:
            raise
        except (http.client.HTTPException, OSError, ValueError) as e:
            # Dead connection / timeout / parse failure: drop this thread's
            # connection and rebuild next time (a new connection pays the 43s
            # first request again, but only when it truly disconnected).
            _drop_conn()
            raise ScoreSaberError(f"ScoreSaber API 请求失败: {url}: {e}") from e


def fetch_profile(cfg: Config, player_id: str) -> dict:
    return _get(cfg, f"{BASE}/player/{player_id}/full")


def fetch_scores(cfg: Config, player_id: str, limit: int = 100,
                 sort: str = "recent", max_pages: int = 3) -> list[dict]:
    """Fetch the recent/top score list (auto-paginates up to limit)."""
    out: list[dict] = []
    page = 1
    per_page = min(max(1, limit), 100)
    while len(out) < limit and page <= max_pages:
        q = urllib.parse.urlencode({"limit": per_page, "sort": sort, "page": page})
        data = _get(cfg, f"{BASE}/player/{player_id}/scores?{q}")
        batch = data.get("playerScores") or []
        if not batch:
            break
        for item in batch:
            sc = item.get("score") or {}
            lb = item.get("leaderboard") or {}
            diff = lb.get("difficulty") or {}
            # difficultyRaw format: "_Expert_SoloStandard" -> "Expert"
            diff_raw = diff.get("difficultyRaw") or ""
            diff_name = diff_raw.split("_")[1] if "_" in diff_raw else diff_raw
            out.append({
                "score_id": sc.get("id"),
                "time_set": sc.get("timeSet"),
                "score": sc.get("modifiedScore") or sc.get("baseScore"),
                "pp": sc.get("pp"),
                "weight": sc.get("weight"),
                "rank": sc.get("rank"),
                "bad_cuts": sc.get("badCuts"),
                "missed_notes": sc.get("missedNotes"),
                "max_combo": sc.get("maxCombo"),
                "full_combo": sc.get("fullCombo"),
                "modifiers": sc.get("modifiers"),
                "has_replay": sc.get("hasReplay"),
                "device_hmd": sc.get("deviceHmd"),
                "leaderboard_id": lb.get("id"),
                "song_hash": lb.get("songHash"),
                "song_name": lb.get("songName"),
                "song_author": lb.get("songAuthorName"),
                "level_author": lb.get("levelAuthorName"),
                "difficulty": diff_name,
                "difficulty_rank": diff.get("difficulty"),
                "stars": lb.get("stars"),
                "ranked": lb.get("ranked"),
                "max_pp": lb.get("maxPP"),
            })
        if len(batch) < per_page:
            break
        page += 1
    return out[:limit]


def cross_validate(cfg: Config, player_id: str,
                   local_replays: list[dict]) -> dict:
    """Validate local parsing results against ScoreSaber scores.

    For each (song_hash, difficulty), compare the ScoreSaber score vs the local
    replay score. Returns {matched: [...], unmatched_local: n, score_diffs: [...]}
    """
    try:
        ss_scores = fetch_scores(cfg, player_id, limit=100, sort="recent")
    except ScoreSaberError as e:
        return {"error": str(e)}
    ss_index: dict[tuple, dict] = {}
    for s in ss_scores:
        if s.get("song_hash"):
            key = (s["song_hash"].upper(), norm_difficulty(s.get("difficulty")))
            ss_index.setdefault(key, s)
    matched, diffs = [], []
    for r in local_replays:
        key = ((r.get("map_hash") or "").upper(),
               norm_difficulty(r.get("difficulty")))
        ss = ss_index.get(key)
        if ss is None:
            continue
        row = {
            "song_name": r.get("song_name"),
            "difficulty": r.get("difficulty"),
            "local_score": r.get("score"),
            "scoresaber_score": ss.get("score"),
            "local_accuracy": r.get("accuracy"),
            "scoresaber_pp": ss.get("pp"),
            "stars": ss.get("stars"),
            "time_set": ss.get("time_set"),
        }
        if isinstance(row["local_score"], int) and isinstance(row["scoresaber_score"], int):
            row["score_diff"] = row["local_score"] - row["scoresaber_score"]
            diffs.append(row["score_diff"])
        matched.append(row)
    return {"matched": matched, "fetched_scores": len(ss_scores),
            "matched_count": len(matched)}


def fetch_map_difficulties(cfg: Config, map_hash: str) -> list[dict]:
    """GET /api/leaderboard/get-difficulties/{hash}

    Fetch all difficulties of a map by hash (including leaderboardId).
    Measured: this endpoint is fast and returns correct results; its fields do
    not include stars (needs by-id to fill in).
    Returns [{"leaderboardId", "difficulty", "gameMode", "difficultyRaw"}, ...]
    """
    if not map_hash:
        return []
    data = _get(cfg, f"{BASE}/leaderboard/get-difficulties/{map_hash.upper()}")
    return data if isinstance(data, list) else []


def fetch_leaderboard_info(cfg: Config, leaderboard_id: int) -> Optional[dict]:
    """GET /api/leaderboard/by-id/{id}/info

    Returns full info for a single leaderboard (stars/ranked/qualified/loved/maxPP).
    """
    data = _get(cfg, f"{BASE}/leaderboard/by-id/{leaderboard_id}/info")
    if not isinstance(data, dict):
        return None
    diff = data.get("difficulty") or {}
    diff_raw = diff.get("difficultyRaw") or ""
    diff_name = diff_raw.split("_")[1] if "_" in diff_raw else ""
    return {
        "leaderboard_id": data.get("id"),
        "map_hash": (data.get("songHash") or "").upper(),
        "difficulty_rank": diff.get("difficulty"),
        "difficulty_name": diff_name,
        "game_mode": diff.get("gameMode") or "",
        "difficulty_raw": diff_raw,
        "song_name": data.get("songName"),
        "level_author": data.get("levelAuthorName"),
        "stars": data.get("stars"),
        "ranked": 1 if data.get("ranked") else 0,
        "qualified": 1 if data.get("qualified") else 0,
        "loved": 1 if data.get("loved") else 0,
        "max_pp": data.get("maxPP"),
        "plays": data.get("plays"),
    }


_DIFF_RANK_NAME = {1: "Easy", 3: "Normal", 5: "Hard", 7: "Expert",
                   9: "ExpertPlus", 11: "ExpertPlus"}


def diff_rank_to_name(rank) -> str:
    if rank is None:
        return ""
    return _DIFF_RANK_NAME.get(int(rank), f"Rank{rank}")


def sync_map_leaderboards(cfg: Config, repo, map_hash: str,
                          force: bool = False) -> dict:
    """Sync all ScoreSaber leaderboard metadata for one map, rooted at the local map.

    Flow (the verified-correct route):
      1. get-difficulties/{hash} -> leaderboardId for all difficulties (fast)
      2. for each leaderboardId: skip when cache hit and not expired;
         otherwise by-id/{id}/info fetches stars/ranked/maxPP (slow, ~44s/request)

    Cached into the scoresaber_leaderboards table (leaderboard_id primary key).
    Returns {total, cached, fetched, failed}
    """
    from datetime import datetime, timezone

    try:
        diffs = fetch_map_difficulties(cfg, map_hash)
    except ScoreSaberError as e:
        if e.status == 404:
            # The map is truly not on ScoreSaber (custom map not uploaded): a legitimate "not found"
            return {"total": 0, "cached": 0, "fetched": 0, "failed": 0,
                    "map_hash": map_hash, "not_on_scoresaber": True}
        raise  # network/timeout/5xx: let the caller count it as failed, retried on next sync
    stats = {"total": len(diffs), "cached": 0, "fetched": 0, "failed": 0,
             "map_hash": map_hash}
    if not diffs:
        stats["not_on_scoresaber"] = True
        return stats

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    for d in diffs:
        lbid = d.get("leaderboardId")
        if not lbid:
            stats["failed"] += 1
            continue
        cached = repo.get_ss_leaderboard(lbid)
        if cached and not force and cached.get("last_synced"):
            # Direct cache hit within TTL (stars change slowly, default 30 days)
            stats["cached"] += 1
            continue
        try:
            info = fetch_leaderboard_info(cfg, lbid)
        except ScoreSaberError as e:
            if e.status == 404:
                # The leaderboard truly does not exist: save a basic entry to avoid repeated queries
                info = None
            else:
                # Network failure: do not write cache (retry on next sync), and do not count it as "not found"
                stats["failed"] += 1
                continue
        if info is None:
            # No details: at least save the basic entry given by get-difficulties
            diff_raw = d.get("difficultyRaw") or ""
            rank = d.get("difficulty")
            repo.upsert_ss_leaderboard({
                "leaderboard_id": lbid,
                "map_hash": map_hash.upper(),
                "difficulty_rank": rank,
                "difficulty_name": diff_rank_to_name(rank),
                "game_mode": d.get("gameMode") or "",
                "difficulty_raw": diff_raw,
                "song_name": None, "level_author": None,
                "stars": None, "ranked": None, "qualified": None,
                "loved": None, "max_pp": None, "plays": None,
                "last_synced": now,
            })
            stats["failed"] += 1
            continue
        info["last_synced"] = now
        repo.upsert_ss_leaderboard(info)
        stats["fetched"] += 1
    return stats


def sync_maps_batch(cfg: Config, repo, map_hashes: list[str],
                    progress_cb=None, force: bool = False,
                    workers: int = 8, only_missing: bool = False) -> dict:
    """Batch-sync leaderboard metadata for multiple maps (for background tasks).

    Concurrency notes: ScoreSaber adds a fixed ~43s delay to the first request
    on a new TCP connection; later requests on the same connection are
    millisecond-fast. Therefore:
    1. each worker thread reuses its own opener (keep-alive connection pool,
       see _opener); after the first request eats the 43s, later requests on
       that thread are millisecond-fast;
    2. 8 worker threads process different maps in parallel, compressing the
       total time from "serial ×43s/request" to "requests ÷ 8 × ~0.2s + 43s"
       (full 1000+ map refresh: hours -> minutes).

    Incremental mode (v1.4.1, one-click refresh): with only_missing=True, maps
    that already have leaderboard cache are skipped (no network) — the essence
    of one-click refresh is finding new data, stale cloud values are not
    re-pulled; "refresh cloud data online" uses force=True to force a full
    re-fetch of cloud values.
    (Note: a leaderboard-level cache hit only saves the detail request;
    get-difficulties still costs one request per map; hash-level skipping saves
    even that. Adding new difficulties to existing maps relies on a forced refresh.)

    Failure retry: after each round, failed maps are re-queued and re-synced
    (auto-recovered once rate limits / transient errors settle); a single map
    that fails >=3 times cumulatively is abandoned and recorded in
    failed_songs (frontend toast).

    Stats aggregation and the progress callback update under a lock, so
    thread-safety is guaranteed.
    """
    total_stats = {"maps": len(map_hashes), "fetched": 0, "cached": 0,
                   "failed": 0, "network_failed": 0, "not_on_scoresaber": 0,
                   "failed_songs": []}
    if not map_hashes:
        return total_stats
    lock = threading.Lock()
    done = 0
    fail_count: dict[str, int] = {}
    if only_missing:
        # Incremental: only sync maps that have no leaderboard cache yet (new maps)
        pending = [mh for mh in map_hashes
                   if not repo.get_ss_leaderboards_by_hash(mh)]
        total_stats["cached"] = len(map_hashes) - len(pending)
    else:
        pending = list(map_hashes)   # current-round queue (first round = all)
    total_hashes = len(pending)      # progress counted by actual work (incremental mode = number of new maps)

    def sync_one(mh: str):
        nonlocal done
        try:
            s = sync_map_leaderboards(cfg, repo, mh, force=force)
        except ScoreSaberError as e:
            s = {"failed": 1,
                 "network_failed": 1 if e.status is None else 0}
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
            return mh   # needs retry
        return None

    while pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(sync_one, pending))
        retry = []
        for mh, res in zip(pending, results):
            if res is None:
                continue
            if fail_count[res] < 3:
                retry.append(res)          # re-queue (next round)
            else:
                row = repo.get_map(res)    # give up: record the map name for the frontend toast
                name = (row or {}).get("song_name") or res[:16]
                total_stats["failed_songs"].append(name)
        pending = retry
    return total_stats


def build_ranked_index(cfg: Config, player_id: str) -> dict[tuple, dict]:
    """Fetch the player's scores and build a (song_hash, difficulty) -> {stars, pp} index.

    Used to bulk-fill map_ranked_cache: one request covers up to 100 songs.
    """
    idx: dict[tuple, dict] = {}
    try:
        scores = fetch_scores(cfg, player_id, limit=100, sort="top")
    except ScoreSaberError:
        return idx
    for s in scores:
        if s.get("song_hash"):
            key = (s["song_hash"].upper(), norm_difficulty(s.get("difficulty")))
            idx[key] = {"stars": s.get("stars"), "pp": s.get("pp")}
    return idx
