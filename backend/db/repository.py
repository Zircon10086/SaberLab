"""SQLite 访问层。每个操作独立连接，避免跨线程问题（本地规模下开销可忽略）。"""
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


class _ConnCtx:
    """sqlite3.Connection 的 with 包装：退出时提交/回滚并真正 close。

    （sqlite3 自带的 __exit__ 只处理事务，不关闭连接。）
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
        """轻量列迁移：新列对已存在表用 ALTER TABLE 补齐（IF NOT EXISTS 只作用于建表）。

        原则（developrules §23 向后兼容）：绝不丢数据，只加列/回填。
        历史上 v2/v4/v5 的表结构变更曾散落在 _tools/migrate_db*.py（已废弃删除），
        此处保留对旧库的升级路径，保证任意旧版本数据库打开即完成迁移。
        """
        # --- replays.analysis_status（原 v6） ---
        cols = {row["name"] for row in c.execute("PRAGMA table_info(replays)")}
        if "analysis_status" not in cols:
            c.execute(
                "ALTER TABLE replays ADD COLUMN analysis_status TEXT DEFAULT 'pending'")
            # 存量已完整分析过的行回填为 analyzed，避免迁移后误触发重复分析
            c.execute("UPDATE replays SET analysis_status='analyzed' "
                      "WHERE status='analyzed'")

        # --- maps.beatmap_key / nps_json（原 v2/v5） ---
        cols = {row["name"] for row in c.execute("PRAGMA table_info(maps)")}
        if "beatmap_key" not in cols:
            c.execute("ALTER TABLE maps ADD COLUMN beatmap_key TEXT")
            # 与原 v2 脚本一致：从文件夹名 "16633 (song - mapper)" 提取 key 回填
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
        # map_ranked_cache / scoresaber_leaderboards 两张表由 SCHEMA 的
        # CREATE TABLE IF NOT EXISTS 在 executescript 时自动补建（含索引）。

    def _conn(self) -> _ConnCtx:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return _ConnCtx(conn)

    # ---------- maps ----------
    def upsert_map(self, m: dict) -> None:
        # 可选列缺省补齐：调用方（resolver 扫描 / NPS / ranked 同步）传入的
        # 键子集不同，避免 "did not supply a value for binding parameter"。
        # nps_json 缺省 None → COALESCE 保留旧值：map_scan 重扫不得覆盖
        # 已计算的 NPS（曾因默认 "{}" 被 scan 冲空——一键刷新并行场景）。
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
        """按谱面文件夹路径精确查找（map 扫描的 DB 复用回退路径）。

        替代旧的“全表加载后线性扫”（P1-3.3）：表小（千级）时索引无必要。
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
        """清空全部分析产物与联网缓存，保留原始 .bsor 文件与本地谱面库。

        删除的表：replays / notes / metrics / windows / motion_series /
                 ai_reports / profiles / experiments / map_ranked_cache /
                 scoresaber_leaderboards / scoresaber_cache。
        保留：maps / scan_state。
        下次扫描会重新发现全部 .bsor 并重新分析；STARS/PP 需重新点
        「更新星级/PP（联网）」同步（谱面属性非本地计算取得）。
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
        """返回 replay 总数（用于分页）。"""
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
        """从已入库 Replay 解析最常用的玩家 ID（BSOR 自带，含 ScoreSaber ID）。

        排除 "Noob"（未登录 ScoreSaber 时的默认回落用户名）。
        返回 "" 表示库中无玩家数据（此时调用方可用配置兜底）。
        """
        with self._conn() as c:
            row = c.execute(
                "SELECT player_id, COUNT(*) AS n FROM replays "
                "WHERE player_id != '' AND LOWER(COALESCE(player_name,'')) != 'noob' "
                "GROUP BY player_id "
                "ORDER BY n DESC, player_id LIMIT 1").fetchone()
        return row[0] if row else ""

    def latest_player_id(self) -> str:
        """返回最近一次游戏记录的玩家 ID（排除 Noob 默认用户名；
        多玩家库中取"当前玩家"的直觉来源）。"""
        with self._conn() as c:
            row = c.execute(
                "SELECT player_id FROM replays "
                "WHERE player_id != '' AND LOWER(COALESCE(player_name,'')) != 'noob' "
                "ORDER BY timestamp DESC LIMIT 1").fetchone()
        return row[0] if row else ""

    def list_pending_replays(self, limit: int = 100000) -> list[dict]:
        """返回已入库但未完整分析（analysis_status='pending'）的 Replay。

        后台预计算（/api/analyze/all）的目标集合：它们已在库中，
        scan() 不会再把它们判为 new/changed。
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

    # ---------- scoresaber leaderboards（以谱面为根） ----------
    def upsert_ss_leaderboard(self, lb: dict) -> None:
        """插入/更新一个 ScoreSaber leaderboard（以 leaderboard_id 为主键）。"""
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
        """获取某谱面的全部 leaderboard（以谱面为根缓存）。"""
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
        """按本地日期分组返回 replay 列表。

        同一天完成的记录归为一页。返回:
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
                    for d, rows in groups.items()]  # 已按 timestamp 降序
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
        """file_path -> (size, mtime)，用于扫描去重。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT file_path, file_size, file_mtime FROM replays").fetchall()
            return {r["file_path"]: (r["file_size"], r["file_mtime"]) for r in rows}

    def previous_attempts_on_map(self, map_hash: str, difficulty: str,
                                  before_ts: int, exclude_id: str | None = None,
                                  limit: int = 5) -> list[dict]:
        """同谱同难度的历史游玩记录（排除 exclude_id 自身）。"""
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
    def save_windows(self, replay_id: str, windows: list[dict]) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM windows WHERE replay_id=?", (replay_id,))
            c.executemany(
                "INSERT INTO windows(replay_id, window_idx, t_start, t_end, metrics_json)"
                " VALUES(?,?,?,?,?)",
                [(replay_id, w["window_idx"], w["t_start"], w["t_end"],
                  json.dumps(w["metrics"], ensure_ascii=False)) for w in windows])

    def get_windows(self, replay_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT window_idx, t_start, t_end, metrics_json FROM windows"
                " WHERE replay_id=? ORDER BY window_idx", (replay_id,)).fetchall()
        return [{"window_idx": r["window_idx"], "t_start": r["t_start"],
                 "t_end": r["t_end"], "metrics": json.loads(r["metrics_json"])}
                for r in rows]

    def save_motion_series(self, replay_id: str, series: dict) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO motion_series(replay_id, series_json)"
                      " VALUES(?,?)", (replay_id, json.dumps(series)))

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
