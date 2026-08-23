"""Replay discovery and analysis pipeline (design doc §7).

MVP strategy: when "Start Analysis" is clicked, scan the Replay directory,
deduplicate by size/mtime/sha256, and parse only after the file write has
settled. No resident high-frequency watcher (watchdog is a later option).
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
    """File-write stability check (§7.3): two consecutive size/mtime readings unchanged."""
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
    """Existing files (mtime older than max_age seconds) are treated as stable
    with zero waiting.

    Layered-analysis scenario: when bulk-ingesting 300+ existing files,
    wait_stable costs at least 0.5s each and would slow the batch down to
    minutes; only freshly written files need waiting, to avoid reading a
    half-written file.
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
        """Hot config update (called after saving settings; path-type settings take effect immediately, no restart needed)."""
        self.cfg = cfg

    # ---------- scan ----------
    def scan(self) -> dict:
        """Scan the Replay directory and return the list of new/changed files (no parsing)."""
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
                     force: bool = False, lang: str = "zh-CN") -> dict:
        """Parse + match map + analyze + persist. With force=True, skip the already-analyzed dedup."""
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

        # Map matching
        map_row = None
        map_status = "not_found"
        if replay.info.map_hash:
            map_row = self.resolver.resolve(replay.info.map_hash)
            if map_row:
                map_status = "matched"
                # Ranked metadata is handled centrally by the "map sync task"
                # (scoresaber_leaderboards table); no network call here so
                # replay analysis is not slowed down (by-id request ~44s).

        # Profile binding (controller offset comes from Replay metadata, source of truth §14)
        profile_id = None
        if replay.controller_offsets is not None:
            profile_id = self._ensure_profile(replay)

        # Filename exit marker: BeatLeader names files <player_id>-exit-<song>-<diff>-...
        # Authoritative game-side info; an early quit is explicitly marked.
        # Used for completion judgment (highest priority).
        filename_exit = "-exit-" in p.name or (
            p.name.split("-")[1] == "exit" if len(p.name.split("-")) > 1 else False)

        # Analysis
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

        # AI report (optional)
        if run_ai and ai_client is not None and build_context is not None:
            try:
                from .ai import run_ai_report
                rep = run_ai_report(self.repo, self.cfg, rid, ai_client,
                                    build_context, lang=lang)
                out["ai_report"] = {"status": rep.get("status"),
                                    "report_id": rep.get("report_id")}
            except Exception as e:  # noqa: BLE001
                out["ai_report"] = {"status": "error", "error": str(e)}
        return out

    # ---------- layered ingest (layered analysis strategy §analysis-strategy) ----------
    def ingest_file(self, path: str, force: bool = False) -> dict:
        """Lightweight ingest (metadata snapshot): only parse the info section (~5ms/file).

        Lists/search/history become available immediately; full analysis
        (motion/windows/fatigue ~0.5s) is deferred to the detail-page lazy
        trigger (analyze_ingested) or background precompute (analyze_all_new).
        State machine: analysis_status pending -> analyzed; status parsed -> analyzed.
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

        # Map matching (pure local DB query; does not trigger a full scan —
        # that is a heavy operation left to "rescan map library" or full
        # analysis. Layering principle: ingest is a second-scale fast path.)
        map_row = None
        map_status = "not_found"
        if replay.info.map_hash:
            map_row = self.repo.get_map(replay.info.map_hash.upper())
            if map_row:
                map_status = "matched"

        # Filename exit marker: completion decidable from metadata alone
        # (highest priority).
        # Three-state completion: replays only carry win/exit/fail tags — when
        # neither exit nor fail is present it counts as completed; during
        # analyze, a duration <98% is corrected to incomplete.
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
            "analysis_version": None,   # not fully analyzed, keep NULL
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
                         ai_client=None, build_context=None,
                         lang: str = "zh-CN") -> dict:
        """Full analysis of an already-ingested (pending) Replay (lazy trigger from the detail page). Idempotent."""
        row = self.repo.get_replay(replay_id)
        if not row:
            return {"status": "error", "error": f"Replay 不在库中: {replay_id}",
                    "replay_id": replay_id}
        path = row.get("file_path")
        if not path or not pathlib.Path(path).exists():
            return {"status": "error", "error": "原始 .bsor 文件已不存在",
                    "replay_id": replay_id}
        # No force: a pending snapshot is overwritten by the analysis; when
        # already analyzed, process_file returns early via content dedup
        # (idempotent — repeatedly opening the detail page never recomputes).
        return self.process_file(path, run_ai=run_ai, ai_client=ai_client,
                                 build_context=build_context, force=False,
                                 lang=lang)

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
                       build_context=None, lang: str = "zh-CN") -> dict:
        """Design doc §18: scan -> pick newest unanalyzed -> parse -> analyze -> report."""
        scan = self.scan()
        if not scan["exists"]:
            return {"status": "error", "error": f"Replay 目录不存在: {scan['replay_dir']}"}
        candidates = scan["new"] + scan["changed"]
        # Already-ingested but pending entries also count as candidates (the
        # scenario of ingesting after a play, then clicking "Analyze Latest")
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
                                ai_client=ai_client, build_context=build_context,
                                lang=lang)
        res["pending_remaining"] = len(candidates) - 1
        res["total_files"] = scan["total_files"]
        return res

    def analyze_all_new(self, progress_cb=None, limit: int = 0,
                        run_ai: bool = False, ai_client=None,
                        build_context=None, lang: str = "zh-CN") -> list[dict]:
        """Background precompute: analyze files newly found by scan + all ingested-but-pending replays."""
        scan = self.scan()
        candidates = scan["new"] + scan["changed"]
        # Also add ingested-but-unanalyzed (pending) entries — the core target
        # of background precompute
        for r in self.repo.list_pending_replays():
            if r.get("file_path"):
                candidates.append({"path": r["file_path"]})
        # Dedup (the same file may be both changed and pending)
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
                                             build_context=build_context,
                                             lang=lang))
        return results

    def ingest_all_new(self, progress_cb=None, limit: int = 0) -> list[dict]:
        """Batch lightweight ingest (scenario 1: first use / after clearing the DB).

        scan -> metadata snapshot of all new/changed files (second-scale);
        full analysis is left to the detail-page lazy trigger or the
        /api/analyze/all background precompute.
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
