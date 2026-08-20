"""Replay 发现与分析管线（设计文档 §7）。

MVP 策略：点击“开始分析”时扫描 Replay 目录，按 size/mtime/sha256 去重，
等待文件写入稳定后解析。不常驻高频监听（watchdog 为后续可选项）。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import time
from datetime import datetime, timezone
from typing import Optional

from .bsor.parser import (parse_file, parse_metadata_only,
                          BsorError, UnsupportedFormatError)
from .config import Config
from .db.repository import Repository
from .maps.resolver import MapResolver
from .analysis.engine import analyze_replay


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def wait_stable(path: pathlib.Path, checks: int = 2, interval: float = 0.5) -> bool:
    """文件写入稳定判断（§7.3）：连续两次 size/mtime 不变。"""
    last = None
    for _ in range(checks + 1):
        try:
            st = path.stat()
        except OSError:
            return False
        cur = (st.st_size, st.st_mtime)
        if last is not None and cur == last:
            return True
        last = cur
        time.sleep(interval)
    return False


def _wait_stable_if_fresh(p: pathlib.Path, max_age: float = 5.0) -> bool:
    """存量文件（mtime 距今 > max_age 秒）直接视为稳定，零等待。

    分层分析场景：批量入库 300+ 存量文件时 wait_stable 每次至少 0.5s，
    会拖慢到分钟级；只有刚写入的文件才需要等待防读半截。
    """
    try:
        age = time.time() - p.stat().st_mtime
    except OSError:
        return False
    if age > max_age:
        return True
    return wait_stable(p)


class ReplayPipeline:
    def __init__(self, cfg: Config, repo: Repository, resolver: MapResolver):
        self.cfg = cfg
        self.repo = repo
        self.resolver = resolver

    def update_config(self, cfg: Config) -> None:
        """配置热更新（设置页保存后调用，路径类配置即时生效，无需重启）。"""
        self.cfg = cfg

    # ---------- scan ----------
    def scan(self) -> dict:
        """扫描 Replay 目录，返回新/变更文件列表（不解析）。"""
        replay_dir = pathlib.Path(self.cfg.replay_dir)
        out = {"replay_dir": str(replay_dir), "exists": replay_dir.exists(),
               "total_files": 0, "new": [], "changed": []}
        if not replay_dir.exists():
            return out
        known = self.repo.known_file_states()
        files = sorted(replay_dir.glob("*.bsor"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        out["total_files"] = len(files)
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            key = str(f)
            if key not in known:
                out["new"].append({"path": key, "size": st.st_size,
                                   "mtime": st.st_mtime})
            else:
                size, mtime = known[key]
                if size != st.st_size or abs(mtime - st.st_mtime) > 1.0:
                    out["changed"].append({"path": key, "size": st.st_size,
                                           "mtime": st.st_mtime})
        return out

    # ---------- single file ----------
    def process_file(self, path: str, run_ai: bool = False,
                     ai_client=None, build_context=None,
                     force: bool = False) -> dict:
        """解析 + 匹配谱面 + 分析 + 落库。force=True 时忽略已分析去重。"""
        p = pathlib.Path(path)
        if not p.exists():
            return {"status": "error", "error": f"文件不存在: {path}"}
        if not _wait_stable_if_fresh(p):
            return {"status": "error", "error": "文件仍在写入，未稳定"}

        try:
            replay = parse_file(p)
        except UnsupportedFormatError as e:
            return {"status": "unsupported", "error": str(e), "path": path}
        except BsorError as e:
            return {"status": "error", "error": f"解析失败: {e}", "path": path}

        rid = replay.file_sha256
        existing = self.repo.get_replay(rid)
        st = p.stat()
        if existing and existing.get("analysis_status") == "analyzed" and not force:
            return {"status": "duplicate", "replay_id": rid,
                    "song_name": existing.get("song_name"),
                    "error": "该 Replay 已分析过（按内容 sha256 去重）"}

        # 谱面匹配
        map_row = None
        map_status = "not_found"
        if replay.info.map_hash:
            map_row = self.resolver.resolve(replay.info.map_hash)
            if map_row:
                map_status = "matched"
                # Ranked 元数据统一由「谱面同步任务」负责（scoresaber_leaderboards 表），
                # 此处不联网，避免拖慢 replay 分析（by-id 请求 ~44s）。

        # Profile 绑定（controller offset 来自 Replay metadata，source of truth §14）
        profile_id = None
        if replay.controller_offsets is not None:
            profile_id = self._ensure_profile(replay)

        # 文件名 exit 标记：BeatLeader 命名 <player_id>-exit-<song>-<diff>-...
        # 游戏侧权威信息，中途退出明确标记；用于完成度判定（优先级最高）
        filename_exit = "-exit-" in p.name or (
            p.name.split("-")[1] == "exit" if len(p.name.split("-")) > 1 else False)

        # 分析
        result = analyze_replay(replay, self.cfg, self.repo, save=True,
                                filename_exit=filename_exit)
        summary = result["summary"]

        info = replay.info
        self.repo.upsert_replay({
            "replay_id": rid,
            "file_path": str(p),
            "file_name": p.name,
            "file_size": st.st_size,
            "file_mtime": st.st_mtime,
            "timestamp": info.timestamp_int,
            "player_id": info.player_id,
            "player_name": info.player_name,
            "platform": info.platform,
            "tracking_system": info.tracking_system,
            "hmd": info.hmd,
            "controller": info.controller,
            "game_version": info.game_version,
            "mod_version": info.version,
            "map_hash": info.map_hash.upper(),
            "song_name": info.song_name,
            "mapper": (map_row or {}).get("mapper") or info.mapper,
            "difficulty": info.difficulty,
            "mode": info.mode,
            "environment": info.environment,
            "modifiers": info.modifiers,
            "score": info.score,
            "score_recomputed": summary["score_recomputed"],
            "score_effective": summary["score_effective"],
            "has_nf": 1 if summary["has_nf"] else 0,
            "jump_distance": info.jump_distance,
            "left_handed": 1 if info.left_handed else 0,
            "height": info.height,
            "start_time": info.start_time,
            "fail_time": info.fail_time,
            "speed": info.speed,
            "won": 1 if info.won else 0,
            "frame_count": summary["frame_count"],
            "fps_median": summary["fps_median"],
            "duration": summary["duration"],
            "note_count": summary["note_count"],
            "good_count": summary["good_count"],
            "bad_count": summary["bad_count"],
            "miss_count": summary["miss_count"],
            "bomb_count": summary["bomb_count"],
            "accuracy": summary["accuracy"],
            "max_combo": summary["max_combo"],
            "full_combo": 1 if summary["full_combo"] else 0,
            "completion_status": summary["completion_status"],
            "profile_id": profile_id,
            "analysis_version": ((existing.get("analysis_version") or 0) + 1
                                 if existing else 1),
            "status": "analyzed",
            "analysis_status": "analyzed",
            "error_message": None,
            "parsed_at": (existing or {}).get("parsed_at") or _now(),
            "analyzed_at": _now(),
        })

        out = {
            "status": "analyzed",
            "analysis_status": "analyzed",
            "replay_id": rid,
            "song_name": info.song_name,
            "difficulty": info.difficulty,
            "map_status": map_status,
            "score": info.score,
            "accuracy": summary["accuracy"],
            "good": summary["good_count"],
            "bad": summary["bad_count"],
            "miss": summary["miss_count"],
            "completion_status": summary["completion_status"],
            "profile_id": profile_id,
        }

        # AI 报告（可选）
        if run_ai and ai_client is not None and build_context is not None:
            try:
                from .ai import run_ai_report
                rep = run_ai_report(self.repo, self.cfg, rid, ai_client, build_context)
                out["ai_report"] = {"status": rep.get("status"),
                                    "report_id": rep.get("report_id")}
            except Exception as e:  # noqa: BLE001
                out["ai_report"] = {"status": "error", "error": str(e)}
        return out

    # ---------- layered ingest（分层分析策略 §分析策略）----------
    def ingest_file(self, path: str, force: bool = False) -> dict:
        """轻量入库（元数据快照）：只解析 info section（~5ms/文件）。

        列表/搜索/历史立即可用；完整分析（motion/windows/fatigue ~0.5s）
        延迟到详情页懒触发（analyze_ingested）或后台预计算（analyze_all_new）。
        状态机：analysis_status pending -> analyzed；status parsed -> analyzed。
        """
        p = pathlib.Path(path)
        if not p.exists():
            return {"status": "error", "error": f"文件不存在: {path}"}
        if not _wait_stable_if_fresh(p):
            return {"status": "error", "error": "文件仍在写入，未稳定"}

        try:
            replay = parse_metadata_only(p)
        except UnsupportedFormatError as e:
            return {"status": "unsupported", "error": str(e), "path": path}
        except BsorError as e:
            return {"status": "error", "error": f"解析失败: {e}", "path": path}

        rid = replay.file_sha256
        existing = self.repo.get_replay(rid)
        if existing and existing.get("analysis_status") == "analyzed" and not force:
            return {"status": "duplicate", "replay_id": rid,
                    "song_name": existing.get("song_name"),
                    "error": "该 Replay 已分析过（按内容 sha256 去重）"}

        # 谱面匹配（纯本地 DB 查询，不触发全量 scan——那是重型操作，留给
        # 「重扫谱面库」或完整分析。分层原则：ingest 是秒级快速路径）
        map_row = None
        map_status = "not_found"
        if replay.info.map_hash:
            map_row = self.repo.get_map(replay.info.map_hash.upper())
            if map_row:
                map_status = "matched"

        # 文件名 exit 标记：元数据即可判定的完成度（优先级最高）。
        # 三态补全：Replay 只有通关/exit/fail 三种标签——exit 与 fail 都不存在
        # 即视为顺利通关（completed）；analyze 时若时长 <98% 会修正为 incomplete。
        filename_exit = "-exit-" in p.name or (
            p.name.split("-")[1] == "exit" if len(p.name.split("-")) > 1 else False)
        info = replay.info
        nf = "NF" in (info.modifiers or "")
        if filename_exit:
            completion = "incomplete"
        elif nf or (info.fail_time or 0) > 0:
            completion = "failed"
        else:
            completion = "completed"

        st = p.stat()
        self.repo.upsert_replay({
            "replay_id": rid,
            "file_path": str(p),
            "file_name": p.name,
            "file_size": st.st_size,
            "file_mtime": st.st_mtime,
            "timestamp": info.timestamp_int,
            "player_id": info.player_id,
            "player_name": info.player_name,
            "platform": info.platform,
            "tracking_system": info.tracking_system,
            "hmd": info.hmd,
            "controller": info.controller,
            "game_version": info.game_version,
            "mod_version": info.version,
            "map_hash": info.map_hash.upper(),
            "song_name": info.song_name,
            "mapper": (map_row or {}).get("mapper") or info.mapper,
            "difficulty": info.difficulty,
            "mode": info.mode,
            "environment": info.environment,
            "modifiers": info.modifiers,
            "score": info.score,
            "jump_distance": info.jump_distance,
            "left_handed": 1 if info.left_handed else 0,
            "height": info.height,
            "start_time": info.start_time,
            "fail_time": info.fail_time,
            "speed": info.speed,
            "won": 1 if info.won else 0,
            "completion_status": completion,
            "analysis_version": None,   # 未完整分析，保持 NULL
            "status": "parsed",
            "analysis_status": "pending",
            "error_message": None,
            "parsed_at": _now(),
            "analyzed_at": None,
        })
        return {"status": "parsed", "analysis_status": "pending",
                "replay_id": rid, "song_name": info.song_name,
                "difficulty": info.difficulty, "map_status": map_status,
                "completion_status": completion, "score": info.score}

    def analyze_ingested(self, replay_id: str, run_ai: bool = False,
                         ai_client=None, build_context=None) -> dict:
        """对已入库（pending）的 Replay 做完整分析（详情页懒触发）。幂等。"""
        row = self.repo.get_replay(replay_id)
        if not row:
            return {"status": "error", "error": f"Replay 不在库中: {replay_id}",
                    "replay_id": replay_id}
        path = row.get("file_path")
        if not path or not pathlib.Path(path).exists():
            return {"status": "error", "error": "原始 .bsor 文件已不存在",
                    "replay_id": replay_id}
        # 不传 force：pending 快照会被分析覆盖；已 analyzed 时 process_file 内部
        # 按内容去重直接返回（幂等，详情页反复打开不重复计算）
        return self.process_file(path, run_ai=run_ai, ai_client=ai_client,
                                 build_context=build_context, force=False)

    def _ensure_profile(self, replay) -> Optional[str]:
        co = replay.controller_offsets
        r = co.right
        key_src = json.dumps({
            "lp": [round(x, 5) for x in co.left.position],
            "lr": [round(x, 5) for x in co.left.rotation],
            "rp": [round(x, 5) for x in r.position],
            "rr": [round(x, 5) for x in r.rotation],
        })
        pid = "off_" + hashlib.sha1(key_src.encode()).hexdigest()[:10]
        if self.repo.get_profile(pid) is None:
            n = len(self.repo.list_profiles()) + 1
            self.repo.create_profile({
                "profile_id": pid,
                "name": f"自动记录 #{n}",
                "position_x": r.position[0], "position_y": r.position[1],
                "position_z": r.position[2],
                "rotation_x": r.rotation[0], "rotation_y": r.rotation[1],
                "rotation_z": r.rotation[2],
                "source": "replay_metadata",
                "notes": json.dumps({
                    "left": {"position": list(co.left.position),
                             "rotation": list(co.left.rotation)},
                    "right": {"position": list(r.position),
                              "rotation": list(r.rotation)},
                }, ensure_ascii=False),
            })
        return pid

    # ---------- batch ----------
    def analyze_latest(self, run_ai: bool = False, ai_client=None,
                       build_context=None) -> dict:
        """设计文档 §18：扫描 -> 挑最新未分析 -> 解析 -> 分析 -> 报告。"""
        scan = self.scan()
        if not scan["exists"]:
            return {"status": "error", "error": f"Replay 目录不存在: {scan['replay_dir']}"}
        candidates = scan["new"] + scan["changed"]
        # 已入库但 pending 的也算候选（打歌后先 ingest 再点「分析最新」的场景）
        for r in self.repo.list_pending_replays(limit=50):
            p = r.get("file_path")
            if p:
                try:
                    mt = pathlib.Path(p).stat().st_mtime
                except OSError:
                    mt = 0.0
                candidates.append({"path": p, "mtime": mt})
        if not candidates:
            return {"status": "idle", "message": "没有发现新的 Replay",
                    "total_files": scan["total_files"]}
        candidates.sort(key=lambda c: c["mtime"], reverse=True)
        target = candidates[0]
        res = self.process_file(target["path"], run_ai=run_ai,
                                ai_client=ai_client, build_context=build_context)
        res["pending_remaining"] = len(candidates) - 1
        res["total_files"] = scan["total_files"]
        return res

    def analyze_all_new(self, progress_cb=None, limit: int = 0,
                        run_ai: bool = False, ai_client=None,
                        build_context=None) -> list[dict]:
        """后台预计算：分析 scan 新发现文件 + 已入库但 pending 的全部 Replay。"""
        scan = self.scan()
        candidates = scan["new"] + scan["changed"]
        # 补充已入库未分析（pending）的条目——后台预计算的核心目标
        for r in self.repo.list_pending_replays():
            if r.get("file_path"):
                candidates.append({"path": r["file_path"]})
        # 去重（同一文件可能既 changed 又 pending）
        seen: set[str] = set()
        uniq = []
        for c in candidates:
            if c["path"] not in seen:
                seen.add(c["path"])
                uniq.append(c)
        candidates = uniq
        candidates.sort(key=lambda c: c.get("mtime") or 0)
        if limit > 0:
            candidates = candidates[:limit]
        results = []
        for i, c in enumerate(candidates):
            if progress_cb:
                progress_cb(i + 1, len(candidates), pathlib.Path(c["path"]).name)
            results.append(self.process_file(c["path"], run_ai=run_ai,
                                             ai_client=ai_client,
                                             build_context=build_context))
        return results

    def ingest_all_new(self, progress_cb=None, limit: int = 0) -> list[dict]:
        """批量轻量入库（场景一：首次使用/清库后）。

        scan -> 对全部新/变更文件做元数据快照（秒级），
        完整分析留给详情懒触发或 /api/analyze/all 后台预计算。
        """
        scan = self.scan()
        candidates = scan["new"] + scan["changed"]
        candidates.sort(key=lambda c: c["mtime"])
        if limit > 0:
            candidates = candidates[:limit]
        results = []
        for i, c in enumerate(candidates):
            if progress_cb:
                progress_cb(i + 1, len(candidates), pathlib.Path(c["path"]).name)
            results.append(self.ingest_file(c["path"]))
        return results
