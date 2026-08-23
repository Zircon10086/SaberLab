"""Replay list enrichment: attach beatmap_key / stars / pp / nps (with in-process cache).

Extracted from main.py's _enrich_replays (architecture review P1-3.1). The old
implementation did, on every /api/replays, /api/replays/{id}, and /api/history request:
  1. Load the whole maps table (list_maps(limit=100000))
  2. Load all scoresaber_leaderboards
  3. Load all map_ranked_cache
  4. Re-run json.loads on the same level's nps_json for every replay

This service builds the three tables into one in-process snapshot (nps_json parsed
only once), invalidated write-through by data mutators (invalidate). Invalidation
is triggered by main.py when a task finishes (_set_task(running=False)) and at the
sync endpoints (scoresaber refresh, cache clear).

Threading model: snapshot build/read is lock-free - the worst case is one redundant
rebuild under concurrency, and the data read is as fresh as a direct DB query without
the cache, so there is no correctness impact.
"""
from __future__ import annotations

import json


def _lb_better(a: dict, b: dict) -> bool:
    """Whether a is better than b (ranked first > SoloStandard first > has stars first)."""

    def score(lb):
        s = 0
        if lb.get("ranked"):
            s += 100
        if (lb.get("game_mode") or "") == "SoloStandard":
            s += 10
        if (lb.get("stars") or 0) > 0:
            s += 1
        return s

    return score(a) > score(b)


class EnrichmentService:
    """Map metadata snapshot + replay enrichment.

    snapshot structure:
      key_map: {map_hash: {beatmap_key, nps: dict}}
      lb_map:  {(map_hash, difficulty_name): leaderboard row}
      rc:      {(map_hash, difficulty): ranked cache row}
    """

    def __init__(self, repo):
        self._repo = repo
        self._snapshot: tuple[dict, dict, dict] | None = None

    def invalidate(self) -> None:
        """Call after data changes (rescan / ranked sync / NPS update / cache clear)."""
        self._snapshot = None

    def _ensure_snapshot(self) -> tuple[dict, dict, dict]:
        if self._snapshot is not None:
            return self._snapshot
        key_map: dict[str, dict] = {}
        for m in self._repo.list_maps(limit=100000):
            try:
                nps = json.loads(m.get("nps_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                nps = {}
            key_map[m["map_hash"]] = {
                "beatmap_key": m.get("beatmap_key") or "",
                "nps": nps if isinstance(nps, dict) else {},
            }
        lb_map: dict[tuple, dict] = {}
        for lb in self._repo.list_ss_leaderboards():
            key = (lb["map_hash"], lb["difficulty_name"] or "")
            cur = lb_map.get(key)
            if cur is None or _lb_better(lb, cur):
                lb_map[key] = lb
        rc: dict[tuple, dict] = {}
        for r in self._repo.list_ranked_cache():
            rc[(r["map_hash"], r["difficulty"])] = r
        self._snapshot = (key_map, lb_map, rc)
        return self._snapshot

    def enrich(self, days: list[dict]) -> None:
        """Attach beatmap_key / stars / pp / nps to the per-day grouped replay lists."""
        if not days:
            return
        key_map, lb_map, rc = self._ensure_snapshot()
        for day in days:
            for r in day.get("replays", []):
                mh = (r.get("map_hash") or "").upper()
                meta = key_map.get(mh, {})
                r["beatmap_key"] = meta.get("beatmap_key", "")
                # NPS: note density (match difficulty file by mode|difficulty)
                nps_map = meta.get("nps") or {}
                diff = r.get("difficulty") or ""
                mode = r.get("mode") or ""
                r["nps"] = nps_map.get(f"{mode}|{diff}") or nps_map.get(f"Standard|{diff}")
                # Stars: scoresaber_leaderboards (level property, independent of player scores)
                lb = lb_map.get((mh, diff))
                r["stars"] = lb.get("stars") if lb else None
                r["ranked"] = bool(lb.get("ranked")) if lb else None
                # ---- 0.00 star fallback: stars of 0/None = unranked ----
                # ScoreSaber writes stars=0/ranked=0 for unranked leaderboards; the
                # display layer uniformly treats that as "no stars" (list page "-",
                # detail page UNRANKED) to avoid a misleading "0.00★"
                if r.get("stars") in (None, 0, 0.0):
                    r["stars"] = None
                    r["ranked"] = False
                # pp: player score index (personal play records)
                c = rc.get((mh, diff))
                pp = c.get("pp") if c else None
                # ---- pp fallback strategy ----
                # 1. A quit-in-progress play (incomplete) cannot have produced pp
                # 2. 0 stars or no stars = the level is not ranked, so no pp is produced
                if r.get("completion_status") == "incomplete":
                    pp = None
                stars = r.get("stars")
                if stars in (None, 0, 0.0):
                    pp = None
                r["pp"] = pp

    def enrich_flat(self, replays: list[dict]) -> None:
        """Attach the same enrichment fields to a flat replay list (not grouped by day)."""
        if replays:
            self.enrich([{"replays": replays}])
