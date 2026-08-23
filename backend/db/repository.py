"""SQLite access layer. Each operation opens its own connection to avoid cross-thread issues (the overhead is negligible at local scale)."""
from __future__ import annotations

import json
import sqlite3
import pathlib
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .models import SCHEMA


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def _moving_average(values: list[float], window: int = 5) -> list[float]:
    """Centered moving average (shrinking window at the edges; no padding copy, to avoid edge bias).

    When there are not enough points in the window, the available range is used
    (the window tapers over the first/last window//2 points), which is more
    neutral than edge padding; an empty list returns empty.
    """
    if not values:
        return []
    w = max(1, int(window))
    half = w // 2
    n = len(values)
    out: list[float] = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        seg = values[lo:hi]
        out.append(round(sum(seg) / len(seg), 4))
    return out


class _ConnCtx:
    """A with-wrapper around sqlite3.Connection: commits/rolls back and truly closes on exit.

    (sqlite3's built-in __exit__ only handles the transaction, it does not close the connection.)
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()
        return False


class Repository:
    def __init__(self, db_path: pathlib.Path | str):
        self.db_path = str(db_path)
        pathlib.Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            self._migrate(c)

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        """Lightweight column migration: add new columns to existing tables with ALTER TABLE (IF NOT EXISTS only applies at CREATE TABLE time).

        Principle (developrules §23 backward compatibility): never drop data, only add columns/backfill.
        Historically the v2/v4/v5 schema changes were scattered in _tools/migrate_db*.py (deprecated and removed);
        this keeps the upgrade path for old databases so any older database completes migration on open.
        """
        # --- replays.analysis_status (formerly v6) ---
        cols = {row["name"] for row in c.execute("PRAGMA table_info(replays)")}
        if "analysis_status" not in cols:
            c.execute(
                "ALTER TABLE replays ADD COLUMN analysis_status TEXT DEFAULT 'pending'")
            # Backfill rows that were already fully analyzed to 'analyzed', to avoid spurious re-analysis after migration
            c.execute("UPDATE replays SET analysis_status='analyzed' "
                      "WHERE status='analyzed'")

        # --- maps.beatmap_key / nps_json (formerly v2/v5) ---
        cols = {row["name"] for row in c.execute("PRAGMA table_info(maps)")}
        if "beatmap_key" not in cols:
            c.execute("ALTER TABLE maps ADD COLUMN beatmap_key TEXT")
            # Consistent with the original v2 script: extract the key from the folder name "16633 (song - mapper)" and backfill
            rows = c.execute(
                "SELECT map_hash, folder_name FROM maps WHERE folder_name IS NOT NULL"
            ).fetchall()
            for row in rows:
                key = (row["folder_name"] or "").split(" ")[0]
                if key and key != row["folder_name"]:
                    c.execute("UPDATE maps SET beatmap_key=? WHERE map_hash=?",
                              (key, row["map_hash"]))
        if "nps_json" not in cols:
            c.execute("ALTER TABLE maps ADD COLUMN nps_json TEXT DEFAULT '{}'")

        # --- windows.t_ref (v1.4.1 plan E: window timeline anchor = median note event time within the window) ---
        cols = {row["name"] for row in c.execute("PRAGMA table_info(windows)")}
        if "t_ref" not in cols:
            c.execute("ALTER TABLE windows ADD COLUMN t_ref REAL")
        # The map_ranked_cache / scoresaber_leaderboards tables are automatically
        # created (with indexes) by SCHEMA's CREATE TABLE IF NOT EXISTS in executescript.

    def _conn(self) -> _ConnCtx:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return _ConnCtx(conn)

    # ---------- maps ----------
    def upsert_map(self, m: dict) -> None:
        # Default-fill optional columns: callers (resolver scan / NPS / ranked sync)
        # pass different key subsets, avoiding "did not supply a value for binding parameter".
        # nps_json defaults to None → COALESCE keeps the old value: a map_scan rescan must not
        # overwrite already-computed NPS (it was once wiped by scan due to the "{}" default — parallel one-click refresh scenario).
        for k, default in (("ranked_difficulty", None), ("stars", None),
                           ("scoresaber_updated", None), ("nps_json", None),
                           ("beatmap_key", "")):
            m.setdefault(k, default)
        with self._conn() as c:
            c.execute("""
                INSERT INTO maps(map_hash, folder_name, path, song_name, song_author,
                                 mapper, bpm, song_length, version, difficulties, info_json,
                                 ranked_difficulty, stars, scoresaber_updated,
                                 beatmap_key, nps_json, hash_source, last_scanned)
                VALUES(:map_hash,:folder_name,:path,:song_name,:song_author,:mapper,
                       :bpm,:song_length,:version,:difficulties,:info_json,
                       :ranked_difficulty,:stars,:scoresaber_updated,
                       :beatmap_key,:nps_json,:hash_source,:last_scanned)
                ON CONFLICT(map_hash) DO UPDATE SET
                    folder_name=excluded.folder_name, path=excluded.path,
                    song_name=excluded.song_name, song_author=excluded.song_author,
                    mapper=excluded.mapper, bpm=excluded.bpm,
                    song_length=COALESCE(excluded.song_length, maps.song_length),
                    version=excluded.version,
                    difficulties=excluded.difficulties, info_json=excluded.info_json,
                    ranked_difficulty=COALESCE(excluded.ranked_difficulty, maps.ranked_difficulty),
                    stars=COALESCE(excluded.stars, maps.stars),
                    scoresaber_updated=COALESCE(excluded.scoresaber_updated, maps.scoresaber_updated),
                    beatmap_key=COALESCE(NULLIF(excluded.beatmap_key, ''), maps.beatmap_key),
                    nps_json=COALESCE(excluded.nps_json, maps.nps_json),
                    hash_source=excluded.hash_source, last_scanned=excluded.last_scanned
            """, {**m, "last_scanned": _now()})

    def get_map(self, map_hash: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM maps WHERE map_hash=?",
                            (map_hash.upper(),)).fetchone()
            return _row_to_dict(row) if row else None

    def get_map_by_path(self, path: str) -> Optional[dict]:
        """Find a map exactly by its folder path (DB reuse fallback path for map scanning).

        Replaces the old "load the whole table then scan linearly" (P1-3.3): an index is unnecessary while the table is small (thousands of rows).
        """
        with self._conn() as c:
            row = c.execute("SELECT * FROM maps WHERE path=?",
                            (path,)).fetchone()
            return _row_to_dict(row) if row else None

    def list_maps(self, limit: int = 5000) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT map_hash, folder_name, beatmap_key, path, song_name, song_author,"
                " mapper, bpm, song_length, difficulties, nps_json, info_json,"
                " hash_source, last_scanned"
                " FROM maps ORDER BY song_name LIMIT ?", (limit,)).fetchall()
            return [_row_to_dict(r) for r in rows]

    def map_count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM maps").fetchone()[0]

    def clear_analysis_cache(self) -> dict:
        """Clear all analysis artifacts and online caches, keeping original .bsor files and the local map library.

        Tables deleted: replays / notes / metrics / windows / motion_series /
                        ai_reports / profiles / experiments / map_ranked_cache /
                        scoresaber_leaderboards / scoresaber_cache.
        Kept: maps / scan_state.
        The next scan will rediscover all .bsor files and re-analyze them; STARS/PP must be
        re-synced via "Update Stars/PP (online)" (map attributes are not computed locally).
        """
        with self._conn() as c:
            c.execute("PRAGMA foreign_keys=OFF")
            for t in ("replays", "notes", "metrics", "windows",
                      "motion_series", "ai_reports", "profiles",
                      "experiments", "map_ranked_cache",
                      "scoresaber_leaderboards", "scoresaber_cache"):
                c.execute(f"DELETE FROM {t}")
            c.execute("PRAGMA foreign_keys=ON")
        return {"cleared": True,
                "message": "缓存已清空，即刻生效。"}

    def reset_analysis_cache(self) -> dict:
        """Clear analysis artifacts and reset all replays to pending (v1.4.1).

        Use case: after analysis parameters (window/step/fatigue edges) change, all computed
        metrics are based on old parameters and must be recomputed. Kept: replays rows
        (list/history still visible), notes (judgement stats are independent of analysis
        parameters), maps and online caches (stars/pp are independent of local parameters).
        The detail page's lazy analysis (analyze_ingested) recomputes pending replays with the new parameters.
        """
        with self._conn() as c:
            for t in ("metrics", "windows", "motion_series"):
                c.execute(f"DELETE FROM {t}")
            c.execute(
                "UPDATE replays SET analysis_status='pending', status='parsed',"
                " analysis_version=NULL, analyzed_at=NULL")
        return {"cleared": True,
                "message": "分析参数已变更：已清空分析缓存，详情页将按新参数重新计算"}

    # ---------- replays ----------
    def upsert_replay(self, r: dict) -> None:
        cols = list(r.keys())
        placeholders = ",".join(f":{k}" for k in cols)
        updates = ",".join(f"{k}=excluded.{k}" for k in cols if k != "replay_id")
        sql = (f"INSERT INTO replays({','.join(cols)}) VALUES({placeholders}) "
               f"ON CONFLICT(replay_id) DO UPDATE SET {updates}")
        with self._conn() as c:
            c.execute(sql, r)

    def get_replay(self, replay_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM replays WHERE replay_id=?",
                            (replay_id,)).fetchone()
            return _row_to_dict(row) if row else None

    def list_replays(self, limit: int = 30, offset: int = 0,
                     map_hash: str | None = None,
                     days: int | None = None) -> list[dict]:
        sql = ("SELECT replay_id, file_path, file_name, timestamp, song_name, difficulty, mode,"
               " map_hash, score, score_effective, has_nf, accuracy, good_count, bad_count, miss_count,"
               " bomb_count, max_combo, full_combo, won, modifiers, status, analysis_status, profile_id,"
               " analysis_version, analyzed_at, duration, fps_median, player_name, player_id,"
               " completion_status"
               " FROM replays")
        where, params = [], []
        if map_hash:
            where.append("map_hash=?")
            params.append(map_hash.upper())
        if days:
            where.append("timestamp >= ?")
            params.append(int(datetime.now(timezone.utc).timestamp()) - days * 86400)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._conn() as c:
            rows = c.execute(sql, params).fetchall()
            return [_row_to_dict(r) for r in rows]

    def count_replays(self, map_hash: str | None = None,
                      days: int | None = None) -> int:
        """Return the total number of replays (for pagination)."""
        sql = "SELECT COUNT(*) FROM replays"
        where, params = [], []
        if map_hash:
            where.append("map_hash=?")
            params.append(map_hash.upper())
        if days:
            where.append("timestamp >= ?")
            params.append(int(datetime.now(timezone.utc).timestamp()) - days * 86400)
        if where:
            sql += " WHERE " + " AND ".join(where)
        with self._conn() as c:
            return c.execute(sql, params).fetchone()[0]

    def most_common_player_id(self) -> str:
        """Resolve the most common player ID from stored replays (provided by BSOR, includes ScoreSaber ID).

        Excludes "Noob" (the default fallback username when not logged into ScoreSaber).
        Returns "" when the library has no player data (the caller can then fall back to config).
        """
        with self._conn() as c:
            row = c.execute(
                "SELECT player_id, COUNT(*) AS n FROM replays "
                "WHERE player_id != '' AND LOWER(COALESCE(player_name,'')) != 'noob' "
                "GROUP BY player_id "
                "ORDER BY n DESC, player_id LIMIT 1").fetchone()
        return row[0] if row else ""

    def latest_player_id(self) -> str:
        """Return the player ID of the most recent play (excluding the Noob default username;
        the intuitive source for "current player" in a multi-player library)."""
        with self._conn() as c:
            row = c.execute(
                "SELECT player_id FROM replays "
                "WHERE player_id != '' AND LOWER(COALESCE(player_name,'')) != 'noob' "
                "ORDER BY timestamp DESC LIMIT 1").fetchone()
        return row[0] if row else ""

    def list_pending_replays(self, limit: int = 100000) -> list[dict]:
        """Return replays that are stored but not fully analyzed (analysis_status='pending').

        The target set of background precomputation (/api/analyze/all): they are already in the
        library, so scan() will not classify them as new/changed again.
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT replay_id, file_path FROM replays "
                "WHERE analysis_status='pending' "
                "ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
            return [_row_to_dict(r) for r in rows]

    def replay_exists(self, replay_id: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM replays WHERE replay_id=?",
                             (replay_id,)).fetchone() is not None

    # ---------- scoresaber leaderboards (rooted at the map) ----------
    def upsert_ss_leaderboard(self, lb: dict) -> None:
        """Insert/update a ScoreSaber leaderboard (keyed by leaderboard_id)."""
        with self._conn() as c:
            c.execute("""
                INSERT INTO scoresaber_leaderboards(
                    leaderboard_id, map_hash, difficulty_rank, difficulty_name,
                    game_mode, difficulty_raw, song_name, level_author,
                    stars, ranked, qualified, loved, max_pp, plays, last_synced)
                VALUES(:leaderboard_id,:map_hash,:difficulty_rank,:difficulty_name,
                       :game_mode,:difficulty_raw,:song_name,:level_author,
                       :stars,:ranked,:qualified,:loved,:max_pp,:plays,:last_synced)
                ON CONFLICT(leaderboard_id) DO UPDATE SET
                    map_hash=excluded.map_hash,
                    difficulty_rank=excluded.difficulty_rank,
                    difficulty_name=excluded.difficulty_name,
                    game_mode=excluded.game_mode,
                    difficulty_raw=excluded.difficulty_raw,
                    song_name=excluded.song_name,
                    level_author=excluded.level_author,
                    stars=excluded.stars,
                    ranked=excluded.ranked,
                    qualified=excluded.qualified,
                    loved=excluded.loved,
                    max_pp=excluded.max_pp,
                    plays=excluded.plays,
                    last_synced=excluded.last_synced
            """, lb)

    def get_ss_leaderboard(self, leaderboard_id: int) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM scoresaber_leaderboards WHERE leaderboard_id=?",
                (leaderboard_id,)).fetchone()
            return _row_to_dict(row) if row else None

    def get_ss_leaderboards_by_hash(self, map_hash: str) -> list[dict]:
        """Get all leaderboards for a map (map-rooted cache)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM scoresaber_leaderboards WHERE map_hash=?"
                " ORDER BY difficulty_rank", (map_hash.upper(),)).fetchall()
            return [_row_to_dict(r) for r in rows]

    def list_ss_leaderboards(self, limit: int = 100000) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT map_hash, difficulty_name, stars, ranked, qualified,"
                " max_pp, last_synced FROM scoresaber_leaderboards"
                " LIMIT ?", (limit,)).fetchall()
            return [_row_to_dict(r) for r in rows]

    def count_ss_leaderboards(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM scoresaber_leaderboards").fetchone()[0]

    # ---------- map ranked cache ----------
    def upsert_ranked_cache(self, map_hash: str, difficulty: str,
                            stars, pp, fetched_at: str) -> None:
        with self._conn() as c:
            c.execute("""
                INSERT INTO map_ranked_cache(map_hash, difficulty, stars, pp, fetched_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(map_hash, difficulty) DO UPDATE SET
                    stars=COALESCE(excluded.stars, map_ranked_cache.stars),
                    pp=COALESCE(excluded.pp, map_ranked_cache.pp),
                    fetched_at=excluded.fetched_at
            """, (map_hash.upper(), difficulty, stars, pp, fetched_at))

    def get_ranked_cache(self, map_hash: str, difficulty: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute(
                "SELECT stars, pp, fetched_at FROM map_ranked_cache"
                " WHERE map_hash=? AND difficulty=?",
                (map_hash.upper(), difficulty)).fetchone()
            return _row_to_dict(row) if row else None

    def list_ranked_cache(self, limit: int = 100000) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT map_hash, difficulty, stars, pp, fetched_at"
                " FROM map_ranked_cache LIMIT ?", (limit,)).fetchall()
            return [_row_to_dict(r) for r in rows]

    # ---------- replays by day ----------
    def list_replays_by_day(self, page: int = 1,
                            map_hash: str | None = None,
                            days: int | None = None) -> dict:
        """Return the replay list grouped by local date.

        Records completed on the same day are grouped into one page. Returns:
        {days: [{date: "YYYY-MM-DD", replays: [...]}], total_days, page, pages}
        """
        import collections
        all_rows = self.list_replays(limit=100000, map_hash=map_hash, days=days)
        groups: dict[str, list] = collections.OrderedDict()
        for r in all_rows:
            ts = r.get("timestamp") or 0
            if ts:
                date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            else:
                date_str = "未知日期"
            groups.setdefault(date_str, []).append(r)
        day_list = [{"date": d, "replays": rows}
                    for d, rows in groups.items()]  # already sorted by timestamp descending
        total_days = len(day_list)
        if not day_list:
            return {"days": [], "total_days": 0, "page": page, "pages": 0}
        page = max(1, min(page, total_days))
        return {
            "days": [day_list[page - 1]],
            "total_days": total_days,
            "page": page,
            "pages": total_days,
        }

    def known_file_states(self) -> dict[str, tuple]:
        """file_path -> (size, mtime), used for scan deduplication."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT file_path, file_size, file_mtime FROM replays").fetchall()
            return {r["file_path"]: (r["file_size"], r["file_mtime"]) for r in rows}

    def previous_attempts_on_map(self, map_hash: str, difficulty: str,
                                  before_ts: int, exclude_id: str | None = None,
                                  limit: int = 5) -> list[dict]:
        """Historical plays on the same map and difficulty (excluding exclude_id itself)."""
        if not map_hash or not difficulty:
            return []   # Guard: abnormal rows (missing hash/difficulty) skip same-map history lookup
        sql = ("SELECT replay_id, file_name, timestamp, song_name, difficulty, mode,"
               " map_hash, score, score_effective, has_nf, accuracy, good_count,"
               " bad_count, miss_count, bomb_count, max_combo, full_combo, won,"
               " modifiers, status, duration, player_name, completion_status"
               " FROM replays"
               " WHERE map_hash=? AND difficulty=? AND timestamp<? AND status!='error'")
        params: list = [map_hash.upper(), difficulty, before_ts]
        if exclude_id:
            sql += " AND replay_id != ?"
            params.append(exclude_id)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        with self._conn() as c:
            rows = c.execute(sql, params).fetchall()
            return [_row_to_dict(r) for r in rows]

    # ---------- notes ----------
    def insert_notes(self, replay_id: str, notes: list[dict]) -> None:
        with self._conn() as c:
            c.executemany("""
                INSERT OR REPLACE INTO notes(replay_id, idx, note_id, event_time,
                    spawn_time, event_type, saber, scoring_type, line_index, layer,
                    color_type, cut_direction, before_score, center_score, after_score,
                    note_score, cut_distance, saber_speed, time_deviation)
                VALUES(:replay_id,:idx,:note_id,:event_time,:spawn_time,:event_type,
                    :saber,:scoring_type,:line_index,:layer,:color_type,:cut_direction,
                    :before_score,:center_score,:after_score,:note_score,:cut_distance,
                    :saber_speed,:time_deviation)
            """, [{**n, "replay_id": replay_id} for n in notes])

    # ---------- metrics ----------
    def save_metrics(self, replay_id: str, metrics: list[tuple[str, str, float, str]]) -> None:
        """metrics: [(scope, name, value, detail_json), ...]"""
        with self._conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO metrics(replay_id, scope, name, value, detail)"
                " VALUES(?,?,?,?,?)",
                [(replay_id, s, n, v, d) for s, n, v, d in metrics])

    def get_metrics(self, replay_id: str) -> dict[str, dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT scope, name, value, detail FROM metrics"
                             " WHERE replay_id=?", (replay_id,)).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            scope = out.setdefault(r["scope"], {})
            v = r["value"]
            if r["detail"]:
                try:
                    extra = json.loads(r["detail"])
                    if isinstance(extra, dict):
                        v = {"value": v, **extra}
                    else:
                        v = {"value": v, "detail": extra}
                except json.JSONDecodeError:
                    pass
            scope[r["name"]] = v
        return out

    # ---------- windows / motion series ----------
    # [DEPRECATED] The windows table is kept (old-database data/structure compatibility); the engine no longer writes to it;
    # curves/slopes/AI summaries are all computed live from the notes table instead (see get_note_events).
    def save_windows(self, replay_id: str, windows: list[dict]) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM windows WHERE replay_id=?", (replay_id,))
            c.executemany(
                "INSERT INTO windows(replay_id, window_idx, t_start, t_end, t_ref,"
                " metrics_json) VALUES(?,?,?,?,?,?)",
                [(replay_id, w["window_idx"], w["t_start"], w["t_end"],
                  w.get("t_ref"), json.dumps(w["metrics"], ensure_ascii=False))
                 for w in windows])

    def get_windows(self, replay_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT window_idx, t_start, t_end, t_ref, metrics_json FROM windows"
                " WHERE replay_id=? ORDER BY window_idx", (replay_id,)).fetchall()
            out = []
            for r in rows:
                d = {"window_idx": r["window_idx"], "t_start": r["t_start"],
                     "t_end": r["t_end"], "t_ref": r["t_ref"], "metrics": {}}
                try:
                    m = json.loads(r["metrics_json"] or "{}")
                    if isinstance(m, dict):
                        d["metrics"] = m
                except json.JSONDecodeError:
                    pass
                out.append(d)
            return out

    def get_miss_bad_events(self, replay_id: str) -> dict:
        """Timestamps of miss/bad events (for the cumulative miss curve, v1.4.1).

        Why not accumulate from the windows' miss counts: time windows overlap with a 1s step
        (30s wide), so the same miss event falls into ~30 windows and gets counted repeatedly —
        GENTLEMAN's 4 misses were once accumulated to 120. Here we take each event's timestamp
        directly from the notes table (events are unique, timestamps are exact); the frontend
        draws a step line indexed by event ordinal.
        """
        from ..bsor.models import BAD, MISS
        with self._conn() as c:
            rows = c.execute(
                "SELECT event_time, event_type FROM notes"
                " WHERE replay_id=? AND event_type IN (?, ?)"
                " ORDER BY event_time", (replay_id, MISS, BAD)).fetchall()
        return {"miss": [r["event_time"] for r in rows if r["event_type"] == MISS],
                "bad": [r["event_time"] for r in rows if r["event_type"] == BAD]}

    def get_note_events(self, replay_id: str) -> list[dict]:
        """All note event rows (the time-series data source after the fixed windows were retired, 2026).

        Used for live computation of per-note curves (saber speed/density) and note grouping
        (slope/AI summary). Returns [{event_time, event_type, note_score, center_score,
        saber_speed, saber}], ordered by event_time ascending.
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT event_time, event_type, note_score, center_score,"
                " saber_speed, saber FROM notes WHERE replay_id=?"
                " ORDER BY event_time", (replay_id,)).fetchall()
            return [_row_to_dict(r) for r in rows]

    def get_note_time_range(self, replay_id: str) -> dict:
        """First/last note event times (timeline trim bounds, v1.4.1).

        Both time-series and hand-motion metrics depend on notes (acc/center/miss/bad); the timeline
        starts at the first note and ends at the last note, and redundant leading/trailing periods
        (pre-song waiting / trailing empty beats) are always trimmed; both cards share the same range.
        """
        with self._conn() as c:
            row = c.execute(
                "SELECT MIN(event_time), MAX(event_time) FROM notes"
                " WHERE replay_id=?", (replay_id,)).fetchone()
        first = row[0] if row and row[0] is not None else 0.0
        last = row[1] if row and row[1] is not None else 0.0
        return {"first_note": float(first), "last_note": float(last)}

    def get_note_series(self, replay_id: str) -> dict:
        """per-note time series (v1.4.1 plan A).

        acc/center/saber speed are note-event-level metrics; aggregating them with time windows
        misaligns them (30s window center vs the note's actual time inside the window deviates by
        ±15s; Hatatagami empirically showed -14.8s on sparse sections). Changed to one point per note:
          x = note event time (exact, unique)
          y = acc = note_score/115 (miss/bad = 0, the curve dips)
              center = center_score (0-15)
              speed = cut saber speed (only good cuts have a value; miss/bad are null)
        bombs (event_type=3) are excluded (not player mistakes). Returned compactly as columns.
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT event_time, note_score, center_score, saber_speed FROM notes"
                " WHERE replay_id=? AND event_type != 3 ORDER BY event_time",
                (replay_id,)).fetchall()
        t = [r["event_time"] for r in rows]
        acc = [round((r["note_score"] or 0) / 115.0, 4) for r in rows]
        center = [r["center_score"] or 0 for r in rows]
        speed = [round(r["saber_speed"], 3) if r["saber_speed"] is not None else None
                 for r in rows]
        return {"t": t, "acc": acc, "center": center, "speed": speed}

    def get_accuracy_curve(self, replay_id: str, ma_window: int = 5) -> dict:
        """per-note acc / Center curves (official convention, corrected 2026-08).

        **acc = official convention** (identical to the replay record / 3D replay / chro):
        accuracy = score / maxScore —— numerator = current actual score (bad=-2/miss=-3/
        bomb=-4/wall=-5 penalties + multiplier bonus), denominator = the theoretical max under
        the same multiplier curve, one point per **block note** (good/bad/miss, including penalty points).
        The old convention (good-only: cumulative score/(good×115)) excluded miss/bad from the
        denominator, so the curve endpoint ≠ the replay record — Hatatagami empirically showed 87.5% vs 81.68%.

        Data source: the accuracy_curve table (persisted during analysis from compute_score's
        running_accuracy — includes wall penalties; the notes table has no wall data so it cannot be
        rebuilt). Historical replays that were not recomputed fall back to the old good-only
        convention (displayable in the frontend; auto-aligned once recomputed).

        **center = good-only cumulative average** (0-15): bad/miss have no center measurement and
        are not fabricated — center_t is independent of acc's t (the frontend uses separate timelines).
        """
        from ..bsor.models import GOOD
        # 1) acc: official convention first (accuracy_curve table), fall back to good-only
        t: list[float] = []
        acc_raw: list[float] = []
        with self._conn() as c:
            row = c.execute("SELECT curve_json FROM accuracy_curve"
                            " WHERE replay_id=?", (replay_id,)).fetchone()
        if row:
            try:
                curve = json.loads(row["curve_json"] or "{}")
                t = [float(x) for x in curve.get("t", [])]
                acc_raw = [float(x) for x in curve.get("acc", [])]
            except (json.JSONDecodeError, TypeError, ValueError):
                t, acc_raw = [], []
        if not t:
            # Fallback: good-only cumulative (old convention, historical replay not recomputed)
            with self._conn() as c:
                rows = c.execute(
                    "SELECT event_time, note_score FROM notes"
                    " WHERE replay_id=? AND event_type=? ORDER BY event_time",
                    (replay_id, GOOD)).fetchall()
            cum_score = 0
            cum_n = 0
            for r in rows:
                cum_n += 1
                cum_score += r["note_score"] or 0
                t.append(r["event_time"])
                acc_raw.append(round(cum_score / (cum_n * 115), 4))
        # 2) center: good-only cumulative average (independent timeline, shared by both paths)
        with self._conn() as c:
            rows = c.execute(
                "SELECT event_time, center_score FROM notes"
                " WHERE replay_id=? AND event_type=? ORDER BY event_time",
                (replay_id, GOOD)).fetchall()
        center_t: list[float] = []
        center_raw: list[float] = []
        cum_center = 0
        cum_n = 0
        for r in rows:
            cum_n += 1
            cum_center += r["center_score"] or 0
            center_t.append(r["event_time"])
            center_raw.append(round(cum_center / cum_n, 3))
        return {"t": t,
                "acc": _moving_average(acc_raw, ma_window),
                "center_t": center_t,
                "center": _moving_average(center_raw, ma_window)}

    def save_motion_series(self, replay_id: str, series: dict) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO motion_series(replay_id, series_json)"
                      " VALUES(?,?)", (replay_id, json.dumps(series)))

    def save_accuracy_curve(self, replay_id: str,
                            block_accuracy: list[tuple]) -> None:
        """Persist the official-convention per-block accuracy curve (2026-08).

        block_accuracy: [(event_time, running_accuracy)], from analysis-time
        compute_score's block_accuracy (score/maxScore official convention, including
        bad/miss/bomb/wall penalties and multiplier). Stores raw; MA smoothing is done at read time.
        """
        curve = {"t": [round(float(t), 4) for t, _ in block_accuracy],
                 "acc": [round(float(a), 6) for _, a in block_accuracy]}
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO accuracy_curve(replay_id, curve_json)"
                      " VALUES(?,?)", (replay_id, json.dumps(curve)))

    def get_motion_series(self, replay_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT series_json FROM motion_series WHERE replay_id=?",
                            (replay_id,)).fetchone()
            return json.loads(row["series_json"]) if row else None

    # ---------- profiles ----------
    def create_profile(self, p: dict) -> str:
        pid = p.get("profile_id") or uuid.uuid4().hex[:12]
        with self._conn() as c:
            c.execute("""INSERT OR REPLACE INTO profiles(profile_id, name, created_at,
                position_x, position_y, position_z, rotation_x, rotation_y, rotation_z,
                source, notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, p.get("name", ""), p.get("created_at") or _now(),
                 p.get("position_x"), p.get("position_y"), p.get("position_z"),
                 p.get("rotation_x"), p.get("rotation_y"), p.get("rotation_z"),
                 p.get("source", "manual"), p.get("notes", "")))
        return pid

    def list_profiles(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM profiles ORDER BY created_at DESC").fetchall()
            return [_row_to_dict(r) for r in rows]

    def get_profile(self, profile_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM profiles WHERE profile_id=?",
                            (profile_id,)).fetchone()
            return _row_to_dict(row) if row else None

    # ---------- experiments ----------
    def create_experiment(self, e: dict) -> str:
        eid = e.get("experiment_id") or uuid.uuid4().hex[:12]
        with self._conn() as c:
            c.execute("""INSERT OR REPLACE INTO experiments(experiment_id, created_at,
                hypothesis, profile_id, baseline_replay_id, candidate_replay_id,
                status, conclusion) VALUES(?,?,?,?,?,?,?,?)""",
                (eid, e.get("created_at") or _now(), e.get("hypothesis", ""),
                 e.get("profile_id"), e.get("baseline_replay_id"),
                 e.get("candidate_replay_id"), e.get("status", "open"),
                 e.get("conclusion", "")))
        return eid

    def list_experiments(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM experiments ORDER BY created_at DESC").fetchall()
            return [_row_to_dict(r) for r in rows]

    # ---------- AI reports ----------
    def save_report(self, rep: dict) -> None:
        with self._conn() as c:
            c.execute("""INSERT OR REPLACE INTO ai_reports(report_id, replay_id,
                created_at, provider, model, status, context_json, report_md, error)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (rep["report_id"], rep.get("replay_id"), rep.get("created_at") or _now(),
                 rep.get("provider"), rep.get("model"), rep.get("status", "ok"),
                 rep.get("context_json"), rep.get("report_md"), rep.get("error")))

    def get_report(self, replay_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM ai_reports WHERE replay_id=? ORDER BY created_at DESC"
                " LIMIT 1", (replay_id,)).fetchone()
            return _row_to_dict(row) if row else None

    # ---------- ScoreSaber cache ----------
    def save_scoresaber(self, player_id: str, profile: dict, scores: list) -> None:
        with self._conn() as c:
            c.execute("""INSERT OR REPLACE INTO scoresaber_cache(player_id, fetched_at,
                profile_json, scores_json) VALUES(?,?,?,?)""",
                (player_id, _now(), json.dumps(profile, ensure_ascii=False),
                 json.dumps(scores, ensure_ascii=False)))

    def get_scoresaber(self, player_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM scoresaber_cache WHERE player_id=?",
                            (player_id,)).fetchone()
            if not row:
                return None
            return {"fetched_at": row["fetched_at"],
                    "profile": json.loads(row["profile_json"]),
                    "scores": json.loads(row["scores_json"])}

    # ---------- scan state ----------
    def set_scan_state(self, key: str, value: dict) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO scan_state(key, value) VALUES(?,?)",
                      (key, json.dumps(value, ensure_ascii=False)))

    def get_scan_state(self, key: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT value FROM scan_state WHERE key=?", (key,)).fetchone()
            return json.loads(row["value"]) if row else None
