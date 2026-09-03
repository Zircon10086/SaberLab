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

from ..analysis.pp_predict import predict_pp, ss_accuracy


def _mode_name(mode: str) -> str:
    """Normalize a leaderboard game mode to its core name.

    ScoreSaber stores "SoloStandard" while BeatLeader stores "Standard";
    stripping the "Solo" prefix lets both hit the same tiebreak. Without this,
    the SoloStandard preference below was dead: the snapshot SELECT did not
    even carry game_mode (fixed 2026-08) and the literal never matched BL rows.
    """
    m = (mode or "").strip()
    return m[4:] if m.lower().startswith("solo") else m


def _lb_better(a: dict, b: dict) -> bool:
    """Whether a is better than b (ranked first > SoloStandard first > has stars first).

    The SoloStandard tiebreak matters when several characteristics share a
    difficulty name (90°/OneSaber/Lightshow "ExpertPlus"): without it an
    arbitrary row wins, and BeatLeader — which stores stars on unranked maps —
    could attach Lightshow stars to the Standard leaderboard.
    """

    def score(lb):
        s = 0
        if lb.get("ranked"):
            s += 100
        if _mode_name(lb.get("game_mode")) == "Standard":
            s += 10
        if (lb.get("stars") or 0) > 0:
            s += 1
        return s

    return score(a) > score(b)


def pick_leaderboard(rows: list[dict]) -> dict | None:
    """Pick the leaderboard row representing one (map_hash, difficulty) key.

    Same tie-break as the snapshot fold in _ensure_snapshot (ranked first >
    Standard first > has stars first); exposed so single-key lookups (e.g. the
    PP preview endpoint) cannot drift from the list enrichment semantics.
    """
    best: dict | None = None
    for lb in rows:
        if best is None or _lb_better(lb, best):
            best = lb
    return best


class EnrichmentService:
    """Map metadata snapshot + replay enrichment (platform-scoped cloud values).

    snapshot structure:
      key_map: {map_hash: {beatmap_key, nps: dict}}
      lb_map:  {(map_hash, difficulty_name): leaderboard row}   (active platform)
      rc:      {(map_hash, difficulty): ranked cache row}       (active platform)

    Platform (scoresaber | beatleader) is fixed per snapshot: switching the
    data source rebuilds the snapshot, so each platform's cached rows stay
    untouched and the UI just re-reads the active one.
    """

    def __init__(self, repo):
        self._repo = repo
        # (platform, snapshot) stored as ONE tuple: two separate fields had a
        # torn-read window during a platform switch, where a concurrent reader
        # could enrich one response with the other platform's stars/pp.
        self._snapshot: tuple[str, tuple[dict, dict, dict]] | None = None

    def invalidate(self) -> None:
        """Call after data changes (rescan / ranked sync / NPS update / cache clear / platform switch)."""
        self._snapshot = None

    def _ensure_snapshot(self, platform: str) -> tuple[dict, dict, dict]:
        entry = self._snapshot
        if entry is not None and entry[0] == platform:
            return entry[1]
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
        for lb in self._repo.list_ss_leaderboards(platform=platform):
            key = (lb["map_hash"], lb["difficulty_name"] or "")
            cur = lb_map.get(key)
            if cur is None or _lb_better(lb, cur):
                lb_map[key] = lb
        rc: dict[tuple, dict] = {}
        for r in self._repo.list_ranked_cache(platform=platform):
            rc[(r["map_hash"], r["difficulty"])] = r
        self._snapshot = (platform, (key_map, lb_map, rc))
        return self._snapshot[1]

    def enrich(self, days: list[dict], platform: str = "scoresaber") -> None:
        """Attach beatmap_key / stars / pp / nps to the per-day grouped replay lists."""
        if not days:
            return
        key_map, lb_map, rc = self._ensure_snapshot(platform)
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
                # PP is platform-specific:
                # - ScoreSaber: map_ranked_cache.pp is the player's CLOUD BEST
                #   for this difficulty, not this local replay. Derive each
                #   completed local play independently from leaderboard maxPP
                #   and its deterministic accuracy. This avoids painting one
                #   cloud best PP onto every local attempt (Cyaegha Expert).
                # - BeatLeader: its PP formula is intentionally not implemented
                #   yet; preserve the existing platform cache behavior.
                c = rc.get((mh, diff))
                if platform == "scoresaber":
                    max_pp = lb.get("max_pp") if lb else None
                    accuracy = ss_accuracy(r.get("accuracy"), r.get("score"),
                                           r.get("score_effective"))
                    pp = (predict_pp(float(max_pp), float(accuracy))
                          if lb and lb.get("ranked") and max_pp
                          and accuracy is not None else None)
                else:
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

    def enrich_flat(self, replays: list[dict], platform: str = "scoresaber") -> None:
        """Attach the same enrichment fields to a flat replay list (not grouped by day)."""
        if replays:
            self.enrich([{"replays": replays}], platform=platform)
