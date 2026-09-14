"""Replay discovery and analysis pipeline (design doc §7).

MVP strategy: when "Start Analysis" is clicked, scan the Replay directory,
deduplicate by size/mtime/sha256, and parse only after the file write has
settled. No resident high-frequency watcher (watchdog is a later option).
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import pathlib
import re
import time
from datetime import datetime, timezone
from typing import Optional

from .bsor.parser import (parse_file, parse_metadata_only,
                          BsorError, UnsupportedFormatError)
from .config import Config
from .db.repository import Repository
from .maps.resolver import MapResolver
from .analysis.engine import analyze_replay

# ---- batch parallelism (2026-09) ----
# Analysis threads do not help (GIL-bound hot loops measured at 1.04-1.24x), so a
# large batch is spread over PROCESSES. 4 is the deliberate design point: SaberLab
# targets a 6-core machine where 4 workers reach ~2x while leaving room for the
# UI, and on very high-core machines the extra processes buy little (I/O bound).
BATCH_WORKERS = 4
# Below this many candidates the pool's spawn/import cost outweighs the gain.
BATCH_PARALLEL_MIN = 24


def _analyze_worker(path: str, force: bool) -> dict:
    """Worker entry point: one full parse+match+analyze+persist unit.

    A fresh ReplayPipeline per spawned process keeps every worker's sqlite handle,
    resolver cache and progress state independent; the connection is closed on the
    way out so the process does not linger holding the database file. The wiring
    mirrors main.py's module-level construction (same three collaborators).
    """
    from .config import load_config
    cfg = load_config()
    repo = Repository(cfg.db_path)
    resolver = MapResolver(cfg.custom_levels_dir, repo, cfg.songcore_cache)
    pipeline = ReplayPipeline(cfg, repo, resolver, map_scan=False)
    try:
        return pipeline.process_file(path, force=force)
    finally:
        try:
            repo.close()
        except Exception:                                # noqa: BLE001 — best effort
            pass

# LocalLeaderboard stores the same session as BeatLeader but with a `_<tick>`
# suffix: `<player>-<song>-<diff>-<mode>-<hash>-<ts>_<tick>.bsor` (the tick is
# a high-resolution timestamp). Normalizing = dropping the tick (2026-09,
# second replay source, HANDOFF §4.25 待办 2).
_LL_NAME_RE = re.compile(r"^(\d+)-(.+)-(\d{10})(_\d+)?\.bsor$")


def normalize_ll_replay_name(name: str) -> str | None:
    """Drop a LocalLeaderboard `_<tick>` suffix (BL-style replay name).

    Returns None when the name is not a standard LL/BL replay name (the
    caller then skips the file — no name to match by).
    """
    m = _LL_NAME_RE.match(name)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}.bsor"


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
    def __init__(self, cfg: Config, repo: Repository, resolver: MapResolver,
                 map_scan: bool = True):
        self.cfg = cfg
        self.repo = repo
        self.resolver = resolver
        # map_scan=False: never trigger a full CustomLevels rescan from resolve()
        # (batch workers set this; the batch does one scan up front instead).
        self.map_scan = map_scan

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
    def process_file(self, path: str, force: bool = False) -> dict:
        """Parse + match map + analyze + persist. With force=True, skip the already-analyzed dedup.

        Never generates reports (v2.1.0 decision) — reports are created on
        demand via /api/ai/analyze/{id} (ai/report.run_ai_report).
        """
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
                    "error": "该 Replay 已分析过"}

        # Map matching. `self.map_scan` is off for batch workers (see __init__):
        # a full CustomLevels rescan costs ~14 s on a 1000-map library, and a batch
        # of 400 replays each missing from the DB used to trigger one per debounce
        # window PER WORKER PROCESS — that is what turned a ~30 s batch into
        # ~4 minutes (2026-09 perf work). Interactive single-file analysis keeps
        # scanning, so a freshly downloaded map still gets picked up.
        map_row = None
        map_status = "not_found"
        if replay.info.map_hash:
            map_row = self.resolver.resolve(replay.info.map_hash,
                                            trigger_scan=self.map_scan)
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

        # No reports here (v2.1.0 decision): the analysis pipeline never
        # generates reports. Historically batch runs called the LLM per replay,
        # which made a post-clear full batch take hours (345 plays x ~20s).
        # Reports are generated ONLY on demand via /api/ai/analyze/{id}
        # (ai/report.run_ai_report — the settings toggle decides LLM vs rule).
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
                    "error": "该 Replay 已分析过"}

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

    def analyze_ingested(self, replay_id: str) -> dict:
        """Full analysis of an already-ingested (pending) Replay (lazy trigger from the detail page). Idempotent.

        Never generates reports — see process_file.
        """
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
        return self.process_file(path, force=False)

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
    def analyze_latest(self) -> dict:
        """Design doc §18: scan -> pick newest unanalyzed -> parse -> analyze. No reports."""
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
        res = self.process_file(target["path"])
        res["pending_remaining"] = len(candidates) - 1
        res["total_files"] = scan["total_files"]
        return res

    def analyze_all_new(self, progress_cb=None, limit: int = 0, force: bool = False) -> list[dict]:
        """Background precompute: analyze files newly found by scan + all ingested-but-pending replays.

        Never generates reports (v2.1.0 decision): a post-clear full batch used
        to call the LLM once per replay (~20s each, hours for 300+ plays).
        Reports are generated on demand from the detail page instead.

        force=True re-analyses every ingested replay regardless of its current
        analysis_status (2026-09). It exists because analysis output changes when
        the engine gains new metrics (e.g. the energy/fail-time module) while the
        normal batch only touches new/changed/pending rows — without it, adding a
        metric would require wiping the whole analysis cache just to recompute
        numbers that are deterministic anyway.
        """
        scan = self.scan()
        candidates = scan["new"] + scan["changed"]
        # Also add ingested-but-unanalyzed (pending) entries — the core target
        # of background precompute
        for r in self.repo.list_pending_replays():
            if r.get("file_path"):
                candidates.append({"path": r["file_path"]})
        if force:
            # Re-analyze everything that still has its source file (raw data is
            # read-only, derived rows are rewritten in place).
            for r in self.repo.list_replays(limit=100000):
                if r.get("file_path"):
                    candidates.append({"path": r["file_path"]})
        # Dedup (the same file may be both changed and pending) and drop rows whose
        # source file is gone: a deleted/renamed .bsor leaves a DB row behind
        # (ingest is add-only by design), and re-trying it every batch only
        # produced "文件不存在" noise (2026-09).
        seen: set[str] = set()
        uniq = []
        for c in candidates:
            path = c.get("path")
            if not path or path in seen:
                continue
            if not pathlib.Path(path).exists():
                continue
            seen.add(path)
            uniq.append(c)
        candidates = uniq
        candidates.sort(key=lambda c: c.get("mtime") or 0)
        if limit > 0:
            candidates = candidates[:limit]
        if len(candidates) >= BATCH_PARALLEL_MIN:
            return self._analyze_batch_parallel(candidates, progress_cb, force)
        results = []
        for i, c in enumerate(candidates):
            if progress_cb:
                progress_cb(i + 1, len(candidates), pathlib.Path(c["path"]).name)
            results.append(self.process_file(c["path"], force=force))
        return results

    def _analyze_batch_parallel(self, candidates: list[dict], progress_cb, force: bool):
        """Analyze a large batch across worker PROCESSES (2026-09).

        Measured on the 2026-09 pipeline (407 replays, whole library):
        serial batch 13.4 s of work vs 6.9 s with 4 workers (~1.95x) — threads are
        useless here (1.04-1.24x, GIL-bound hot loops), processes are not.
        Persistence stays inside each worker running the same `process_file` unit,
        so nothing large is pickled back to the parent.

        One map scan runs HERE, up front: a full CustomLevels walk costs ~14 s and
        every worker resolving a missing hash would otherwise trigger its own copy
        (4x 14 s, fighting over the disk — that alone made a batch 3x SLOWER than
        serial before the 2026-09 fix).
        """
        paths = [c["path"] for c in candidates]
        total = len(paths)
        try:
            self.resolver.scan()          # once, in the parent (workers never rescan)
        except Exception as e:                             # noqa: BLE001
            print(f"[batch] 前置地图扫描失败（继续，未匹配的谱面将记为 not_found）: {e}", flush=True)
        results: list[dict] = []
        workers = min(BATCH_WORKERS, total)
        try:
            with cf.ProcessPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(_analyze_worker, p, force): p for p in paths}
                for i, fut in enumerate(cf.as_completed(futures), start=1):
                    path = futures[fut]
                    try:
                        results.append(fut.result())
                    except Exception as e:                 # noqa: BLE001 — one file must not kill the batch
                        results.append({"status": "error", "path": path, "error": repr(e)})
                    if progress_cb:
                        progress_cb(i, total, pathlib.Path(path).name)
        except Exception as e:                             # noqa: BLE001
            # Pool creation itself can fail (e.g. a frozen/restricted environment):
            # fall back to the serial path rather than reporting a failed batch.
            print(f"[batch] 并行分析不可用，回退串行: {e}", flush=True)
            results = []
            for i, p in enumerate(paths):
                if progress_cb:
                    progress_cb(i + 1, total, pathlib.Path(p).name)
                results.append(self.process_file(p, force=force))
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

    # ---------- second replay source: LocalLeaderboard (2026-09) ----------
    def ingest_local_leaderboard(self) -> dict:
        """Ingest the optional LocalLeaderboard replay directory (second
        read-only source; zero-config — enabled when the derived
        `UserData/LocalLeaderboard/Replays` dir exists).

        The two mods store one copy per session (LL keeps no exit replays),
        so the LL dir doubles as a safety copy:
        - row exists + its own file is gone → repair the row to point at the
          LL twin (HANDOFF §4.25 恢复场景; analysis data untouched — same
          content, the mod_VERSION shows the twins are byte-identical today,
          and matching is by session key anyway);
        - session not in the DB + BL twin exists → skip (the normal BL scan
          owns the session);
        - session not in the DB + no BL twin → LL-only replay, ingest as a
          normal row.

        Dedupe is by session (player + map_hash + play timestamp), not by
        content hash: content-hash dedupe would double-count a session if the
        two mods ever start writing different payloads.
        Returns per-file results (for the ingest task tally) + counts.
        """
        ll_dir = pathlib.Path(self.cfg.local_leaderboard_dir or "")
        empty = {"dir": str(ll_dir), "exists": False, "files": 0,
                 "ingested": 0, "duplicate": 0, "repaired": 0, "skipped": 0,
                 "results": []}
        if not ll_dir.exists():
            return empty
        bl_dir = pathlib.Path(self.cfg.replay_dir)
        bl_names = ({p.name for p in bl_dir.glob("*.bsor")}
                    if bl_dir.exists() else set())
        try:
            files = sorted(ll_dir.glob("*.bsor"),
                           key=lambda p: p.stat().st_mtime)
        except OSError:
            return empty
        out = {"dir": str(ll_dir), "exists": True,
               "files": len(files), "ingested": 0, "duplicate": 0,
               "repaired": 0, "skipped": 0, "results": []}
        for f in files:
            norm = normalize_ll_replay_name(f.name)
            if not norm:
                out["skipped"] += 1
                continue
            if (bl_dir / norm).exists():
                # The BL twin is present — the normal BL scan owns the
                # session; do not create a second row or touch paths here.
                out["duplicate"] += 1
                continue
            try:
                meta = parse_metadata_only(f)
            except (BsorError, UnsupportedFormatError, ValueError):
                out["skipped"] += 1
                continue
            row = self.repo.get_replay_by_session(
                meta.info.player_id, meta.info.map_hash.upper(),
                meta.info.timestamp_int)
            if row:
                old = row.get("file_path")
                if old and pathlib.Path(old).exists():
                    out["duplicate"] += 1     # row file present; LL copy redundant
                else:
                    # Repair: the row's original .bsor is gone, the LL twin
                    # survives — point the row at it (ingest is add-only, so
                    # the row itself must be kept).
                    self.repo.refresh_replay_file(row["replay_id"], str(f))
                    out["repaired"] += 1
                continue
            # LL-only session (no BL twin): ingest as a normal row
            res = self.ingest_file(str(f))
            out["results"].append(res)
            status = res.get("status")
            if status in ("parsed", "analyzed"):
                out["ingested"] += 1
            elif status == "duplicate":
                out["duplicate"] += 1
            else:
                out["skipped"] += 1
        return out
