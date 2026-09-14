"""SaberLab FastAPI entry point.

Start:  .venv\\Scripts\\python.exe backend\\main.py
Panel:  http://127.0.0.1:6980
"""
from __future__ import annotations

import base64
import json
import math
import os
import pathlib
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.config import Config, load_config, PROJECT_ROOT
from backend import APP_INSTANCE_ID
from backend.config.schema import STAR_PALETTES, get_star_palette
from backend.config.service import ConfigService, check_paths
from backend.analysis.player_palette import build_tiers
from backend.analysis import skill_model
import backend.beatleader as beatleader
from backend.db.repository import Repository
from backend.maps.resolver import MapResolver
from backend.services.enrichment import EnrichmentService
from backend.services import player_assets
from backend.watcher import ReplayPipeline
from backend.analysis.compare import compare_metrics
from backend.ai.provider import LLMClient
from backend.ai.report import run_ai_report
import backend.scoresaber as scoresaber

cfg: Config = load_config()
repo = Repository(cfg.db_path)
resolver = MapResolver(cfg.custom_levels_dir, repo, cfg.songcore_cache)
pipeline = ReplayPipeline(cfg, repo, resolver)
llm = LLMClient(cfg)
config_svc = ConfigService()
enrichment = EnrichmentService(repo)


def reload_runtime_config() -> None:
    """Hot-reload the runtime config (called after settings save): reload
    config.yaml and sync to resolver/pipeline/llm; path settings apply at once,
    AI settings (incl. the non-restart temperature/max_tokens) follow too."""
    global cfg
    cfg = load_config()
    resolver.update_paths(cfg.custom_levels_dir, cfg.songcore_cache)
    pipeline.update_config(cfg)
    llm.update_config(cfg)


def _scoresaber_id() -> str:
    """Resolve the ScoreSaber player ID (key finding: ScoreSaber ID = Steam ID,
    BSOR replays carry a 17-digit platform ID). The manual config setting is
    DEPRECATED (2026 decision): the ID is parsed from BSOR replays only —
    the most recent play's player ("current player" intuition in multi-player
    libraries, not the mode). Returns "" when no replay is ingested yet."""
    return repo.latest_player_id()


def _active_platform() -> str:
    """Current cloud data source (player.data_source): scoresaber | beatleader.
    Unknown values fall back to scoresaber (backward compatible)."""
    src = (cfg.data_source or "scoresaber").lower()
    return src if src in ("scoresaber", "beatleader") else "scoresaber"


def _path_ok(p: str) -> bool:
    try:
        return bool(p) and pathlib.Path(p).exists()
    except OSError:
        return False


def _attach_file_available(rows: list[dict]) -> None:
    """Attach file_available (server-side read-only presence check) to replay dicts.

    Ingest is add-only (2026-09 decision): DB rows can outlive their original
    .bsor files, so the UI must degrade explicitly instead of showing generic
    "no data" — persisted data (timeline) still renders, raw-file features
    (slice details / 3D replay / re-analysis) show an unavailable reason.
    The flag is computed here so the frontend never judges file existence
    from local paths.
    """
    for row in rows:
        fp = row.get("file_path")
        row["file_available"] = bool(fp) and pathlib.Path(fp).exists()


def _require_replay_dir() -> None:
    if not _path_ok(cfg.replay_dir):
        raise HTTPException(400, "Replay 目录不可用，请先在「设置 → 游戏路径」配置正确的游戏根目录")


def _require_maps_dir() -> None:
    if not _path_ok(cfg.custom_levels_dir):
        raise HTTPException(400, "谱面目录不可用，请先在「设置 → 游戏路径」配置正确的游戏根目录")


def _db_empty() -> bool:
    """Whether the database is empty (fresh first launch / after clearing the
    analysis cache).

    The replays table is authoritative: list/history/detail/batch analysis all
    build on replay rows; after clearing the cache the maps table remains (the
    map library), but most features are meaningless with no replays -> treated
    as an "empty database". A live COUNT (ms) avoids global-flag state-sync
    issues; it auto-clears once tasks ingest.
    """
    return repo.count_replays() == 0


def _require_db_populated() -> None:
    """Empty-DB guard: all background tasks are rejected on an empty database,
    except the Overview "⚡ One-click Refresh".

    After a fresh DB or cache clear, nearly every feature relies on scanned-in
    data; the only valid entry is One-click Refresh (ingest + map library +
    NPS + online stars in parallel).
    """
    if _db_empty():
        raise HTTPException(
            400, "数据库为空：请先点击总览「⚡ 一键刷新」完成首次扫描")

app = FastAPI(title="SaberLab", version="2.2.0",
              description="Beat Saber 本地 Replay 分析实验室")

FRONTEND_DIR = PROJECT_ROOT / "frontend"


@app.middleware("http")
async def _access_log(request, call_next):
    """Request access log (for diagnosing hung requests)."""
    import time as _time
    t0 = _time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        print(f"[req] {request.method} {request.url.path} -> EXC", flush=True)
        raise
    dt = (_time.perf_counter() - t0) * 1000
    print(f"[req] {request.method} {request.url.path} -> {response.status_code} ({dt:.0f}ms)",
          flush=True)
    return response


class NoCacheStaticFiles(StaticFiles):
    """Disable caching for static assets: frontend changes take effect at once.

    Browsers heuristically cache static files without Cache-Control, which once
    kept old JS/CSS stale for a long time (deployed but page behavior unchanged).
    """

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-store"
        return resp

# ---------- background task state (parallel tasks: one-click import runs 5 tasks at once) ----------
_task_lock = threading.Lock()
# kind -> task dict; each kind has its own slot: same-kind conflicts 409, different kinds run in parallel
_tasks: dict[str, dict] = {}

# timeline energy curve cache (2026-09): replay_id -> (ts, vals) or [] when
# unavailable. Bounded wholesale clear; see _energy_curve_for.
_energy_curve_cache: dict[str, tuple] = {}


def _set_task(kind: str, **kw):
    with _task_lock:
        t = _tasks.setdefault(kind, {"running": False, "kind": kind, "done": 0,
                                     "total": 0, "current": "", "results": [],
                                     "error": None})
        t.update(kw)
    # task finished (any background task may change the maps/leaderboards/ranked cache tables)
    # -> enrichment snapshot is invalidated, rebuilt on next request (P1-3.1 cache write-through invalidation point)
    if kw.get("running") is False:
        enrichment.invalidate()


def _task_running(kind: str | None = None) -> bool:
    """Read task running state under the lock: a specific kind or any task (P0-2.3 lock-free read fix)."""
    with _task_lock:
        if kind:
            t = _tasks.get(kind)
            return bool(t and t["running"])
        return any(t["running"] for t in _tasks.values())


def _start_task(kind: str, target, args=()) -> None:
    """Start a background task: same kind already running -> 409; different
    kinds run in parallel unaffected.

    Clean finished old tasks before starting a new one - `/api/status`'s tasks
    array only keeps the "currently active group": after One-click Refresh
    (5 tasks) finishes, a single task (online update) leaves 1 entry -> the
    frontend KPI correctly uses "task detail mode" not the count mode.
    """
    with _task_lock:
        if _tasks.get(kind, {}).get("running"):
            raise HTTPException(409, f"「{kind}」任务已在运行")
        for k in [k for k, t in _tasks.items() if not t.get("running")]:
            del _tasks[k]
        _tasks[kind] = {"running": True, "kind": kind, "done": 0, "total": 0,
                        "current": "准备中…", "results": [], "error": None}
    threading.Thread(target=target, args=args, daemon=True).start()


def _wait_ingest_done(kind: str):
    """Wait for the same group's ingest to finish (one-click refresh parallel
    scenario: before ingest, batch/ranked_update would miss just-scanned files
    - after clearing data, one-click refresh once synced 0 stars)."""
    while _task_running("ingest"):
        _set_task(kind, current="等待入库完成…")
        time.sleep(0.5)


def _run_batch(limit: int, force: bool = False):
    try:
        _wait_ingest_done("batch")
        def cb(i, n, name):
            _set_task("batch", done=i, total=n, current=name)
        # No reports (v2.1.0 decision): the batch used to call the LLM once per
        # replay (~20s each — hours after a cache clear). Reports are generated
        # on demand from the detail page via /api/ai/analyze/{id}.
        results = pipeline.analyze_all_new(progress_cb=cb, limit=limit, force=force)
        _set_task("batch", running=False, results=results, current="")
    except Exception as e:  # noqa: BLE001
        _set_task("batch", running=False, error=f"{e}\n{traceback.format_exc()}")


def _run_ranked_update(only_missing: bool = False,
                       refresh_player: bool = False):
    """Background sync: rooted at local maps, batch-fetch the ACTIVE platform's
    leaderboard metadata (scoresaber | beatleader, player.data_source).

    1. Collect hashes of maps with replay records (deduplicated)
    2. leaderboard sync -> scoresaber_leaderboards (stars cache, map attributes)
    3. player score index -> map_ranked_cache (pp cache, personal play history)

    Each platform keeps its own rows: switching data sources never touches the
    other platform's cache. only_missing (v1.4.1, one-click refresh): skip
    cached maps, only sync new ones.
    """
    platform = _active_platform()
    try:
        _wait_ingest_done("ranked_update")   # wait for ingest before collecting map hashes (post-clear one-click refresh scenario)
        replays = repo.list_replays(limit=100000)
        hashes = sorted({(r.get("map_hash") or "").upper() for r in replays
                         if r.get("map_hash")})

        def cb(i, n, name):
            _set_task("ranked_update", done=i, total=n, current=f"谱面同步:{name}")
        if platform == "beatleader":
            stats = beatleader.sync_maps_batch(cfg, repo, hashes, progress_cb=cb,
                                               only_missing=only_missing)
            build_index = beatleader.build_ranked_index
        else:
            stats = scoresaber.sync_maps_batch(cfg, repo, hashes, progress_cb=cb,
                                               only_missing=only_missing)
            build_index = scoresaber.build_ranked_index
        stats["leaderboards_total"] = repo.count_ss_leaderboards(platform=platform)

        # pp filling: player score index ((hash, difficulty) -> pp), ID auto-resolved from BSOR
        pid = _scoresaber_id()
        idx = build_index(cfg, pid) if pid else {}
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        for r in replays:
            mh = (r.get("map_hash") or "").upper()
            df = r.get("difficulty") or ""
            info = idx.get((mh, df))
            if info:
                repo.upsert_ranked_cache(mh, df, info.get("stars"),
                                         info.get("pp"), now, platform=platform)
        stats["pp_indexed"] = len(idx)

        # One-click refresh is the user's complete sync action: after map
        # leaderboards and the per-player score index, also refresh the active
        # platform's profile/recent scores and recompute the personal palette.
        # Keep this in the same task to avoid parallel duplicate API traffic.
        if refresh_player:
            _set_task("ranked_update", current="更新玩家数据与动态水平…")
            _cloud_page_refresh(platform)
            stats["player_refreshed"] = True

        _set_task("ranked_update", running=False, current="",
                  results=[{"kind": "ranked_update", **stats}])
    except Exception as e:  # noqa: BLE001
        _set_task("ranked_update", running=False, error=f"{e}\n{traceback.format_exc()}")


def _wait_map_scan_done(kind: str):
    """Wait for map_scan to finish (one-click refresh parallel scenario:
    nps_update depends on maps rows; before maps are scanned in, it idles -
    on a fresh DB nps_update once finished 0/0, all NPS missing)."""
    while _task_running("map_scan"):
        _set_task(kind, current="等待谱面扫描完成…")
        time.sleep(0.5)


def _run_nps_update():
    """Compute NPS for all maps in the background (block density: note count /
    the actual duration of that difficulty).

    Walk every map folder under CustomLevels, read each difficulty's .dat and
    write results to maps.nps_json ({"Standard|Expert": 4.2, ...}).

    Incremental (v1.4.1, one-click refresh): skip already-computed, unchanged
    maps (mtime <= last_scanned) - one-click refresh finds new data only.
    """
    try:
        _wait_map_scan_done("nps_update")
        from backend.maps.resolver import compute_level_nps, read_level_info
        maps = repo.list_maps(limit=100000)
        updated = 0
        skipped = 0

        def cb(i, n, name):
            _set_task("nps_update", done=i, total=n, current=f"NPS:{name}")

        def folder_changed(folder, last_scanned: str) -> bool:
            """Whether the map folder's mtime is newer than the last scan (decides whether NPS must be recomputed)."""
            try:
                latest = 0.0
                for p in folder.iterdir():
                    if p.is_file():
                        latest = max(latest, p.stat().st_mtime)
                from datetime import datetime, timezone as _tz
                ts = datetime.strptime(last_scanned, "%Y-%m-%d %H:%M:%SZ").replace(
                    tzinfo=_tz.utc).timestamp()
                return latest > ts
            except (OSError, ValueError):
                return True   # conservatively recompute on parse failure

        for i, m in enumerate(maps):
            cb(i + 1, len(maps), (m.get("song_name") or "")[:24] or m["map_hash"][:12])
            folder = pathlib.Path(m.get("path") or "")
            if not folder.exists():
                skipped += 1
                continue
            # incremental skip: NPS already present and folder unchanged (v1.4.1)
            try:
                has_nps = bool(json.loads(m.get("nps_json") or "{}"))
            except (json.JSONDecodeError, TypeError):
                has_nps = False
            if has_nps and not folder_changed(folder, m.get("last_scanned") or ""):
                skipped += 1
                continue
            info = read_level_info(folder)
            nps = compute_level_nps(folder, info) if info else {}
            if nps:
                repo.upsert_map({
                    "map_hash": m["map_hash"],
                    "folder_name": m.get("folder_name"),
                    "path": m.get("path"),
                    "song_name": m.get("song_name"),
                    "song_author": m.get("song_author"),
                    "mapper": m.get("mapper"),
                    "bpm": m.get("bpm"),
                    "song_length": m.get("song_length"),
                    "version": m.get("version"),
                    "difficulties": m.get("difficulties"),
                    "info_json": m.get("info_json"),
                    "hash_source": m.get("hash_source"),
                    "ranked_difficulty": m.get("ranked_difficulty"),
                    "stars": m.get("stars"),
                    "scoresaber_updated": m.get("scoresaber_updated"),
                    "nps_json": json.dumps(nps, ensure_ascii=False),
                })
                updated += 1
            else:
                skipped += 1
        _set_task("nps_update", running=False, current="",
                  results=[{"kind": "nps_update", "updated": updated,
                            "skipped": skipped, "total": len(maps)}])
    except Exception as e:  # noqa: BLE001
        _set_task("nps_update", running=False, error=f"{e}\n{traceback.format_exc()}")


# ---------- status ----------
@app.get("/api/status")
def api_status():
    scan = None
    try:
        replay_dir = pathlib.Path(cfg.replay_dir)
        scan = {"exists": replay_dir.exists(),
                "bsor_files": len(list(replay_dir.glob("*.bsor"))) if replay_dir.exists() else 0}
    except OSError:
        scan = {"exists": False}
    with _task_lock:
        tasks = [dict(t) for t in _tasks.values()]
    maps_dir = {"exists": _path_ok(cfg.custom_levels_dir)}
    platform = _active_platform()
    palette_cache = _palette_cache_for(platform)
    palette_entries = _palette_entries(palette_cache)
    return {
        "ok": True,
        "app_instance": APP_INSTANCE_ID,
        "pid": os.getpid(),
        "platform": platform,   # active cloud data source: scoresaber | beatleader
        "config": {
            "replay_dir": cfg.replay_dir,
            "custom_levels_dir": cfg.custom_levels_dir,
            "window_seconds": cfg.window_seconds,
            "window_step_seconds": cfg.window_step_seconds,
            "scoresaber_id": _scoresaber_id(),
        },
        "replay_dir": scan,
        "maps_dir": maps_dir,
        "tasks": tasks,
        "db": {
            "replays": repo.count_replays(),
            "maps": repo.map_count(),
        },
        "ai": {
            "provider": cfg.ai_provider,
            "model": cfg.ai_model,
            "configured": llm.configured,
            # 'env' (inherited environment variable) | 'env_file' (.env) | ''
            "api_key_source": cfg.ai_api_key_source(),
        },
        "chro": {"available": CHRO_AVAILABLE},
        # Star rating colour scheme: current selection + palette definitions (single
        # source for the frontend's starColor tiers). The selectable set is the skill
        # tracks whose rating exists on the ACTIVE platform (cached; absent offline ->
        # the active id falls back to "community").
        "ui": {
            "star_palette": _active_palette_id(palette_cache),
            "star_palettes": STAR_PALETTES + palette_entries,
            "star_palette_options": _palette_availability(palette_cache),
        },
    }


# ---------- scan & analyze ----------
# DEPRECATED (2026-08): not used by the frontend (scan detail is internal
# to the pipeline now); kept for API compatibility, intentional no-delete.
@app.post("/api/scan")
def api_scan():
    return pipeline.scan()


class AnalyzeBody(BaseModel):
    """Body of the deprecated analyze endpoints and /api/refresh/all.

    run_ai/lang are accepted for backward compatibility but no longer have any
    effect: analysis never generates reports (v2.1.0 decision) — reports come
    exclusively from /api/ai/analyze/{id}.
    """
    run_ai: bool = True
    lang: str = "zh-CN"


# DEPRECATED (2026-08): not used by the frontend (one-click refresh covers it);
# kept for API compatibility, intentional no-delete.
@app.post("/api/analyze/latest")
def api_analyze_latest(body: AnalyzeBody | None = None):
    # No reports (v2.1.0 decision): analysis never generates them; reports are
    # created on demand via /api/ai/analyze/{id}.
    _require_db_populated()   # empty-DB guard (all tasks rejected except one-click refresh)
    if _task_running():
        raise HTTPException(409, "已有批量分析任务在运行")
    res = pipeline.analyze_latest()
    return res


# DEPRECATED (2026-08): not used by the frontend (one-click refresh covers it);
# kept for API compatibility, intentional no-delete.
@app.post("/api/analyze/all")
def api_analyze_all(body: AnalyzeBody | None = None, limit: int = Query(0),
                    force: bool = Query(False)):
    """Batch analyze. force=true re-analyzes every ingested replay, not just
    new/changed/pending ones — needed after the analysis engine gains a metric
    (e.g. energy/fail time, 2026-09) without wiping the cache."""
    _require_db_populated()   # empty-DB guard (all tasks rejected except one-click refresh)
    _require_replay_dir()   # reject directly when path is unavailable (frontend already guards, backend fallback)
    _start_task("batch", _run_batch, (limit, force))
    return {"status": "started"}


# DEPRECATED (2026-08): not used by the frontend; kept for API compatibility,
# intentional no-delete.
@app.post("/api/analyze/by-path")
def api_analyze_by_path(path: str = Query(...), lang: str = Query("zh-CN")):
    return pipeline.process_file(path)


@app.post("/api/analyze/{replay_id}")
def api_reanalyze(replay_id: str, lang: str = Query("zh-CN")):
    row = repo.get_replay(replay_id)
    if not row:
        raise HTTPException(404, "replay 不存在")
    if not row.get("file_path") or not pathlib.Path(row["file_path"]).exists():
        raise HTTPException(410, "原始 .bsor 文件已不存在，无法重新分析")
    # force re-analysis (bypass content dedup); no reports — generate them
    # on demand via /api/ai/analyze/{id}
    res = pipeline.process_file(row["file_path"], force=True)
    return res


# DEPRECATED (2026-08): not used by the frontend (one-click refresh covers it);
# kept for API compatibility, intentional no-delete.
@app.post("/api/ingest/all")
def api_ingest_all(limit: int = Query(0)):
    """Lightweight ingest of all new/changed replays (metadata snapshot,
    ~5ms per file, finishes in seconds).

    Tiered analysis: list/search become visible immediately; full analysis is
    deferred to the detail page (POST /api/replays/{id}/analyze) or background
    precomputation (POST /api/analyze/all).
    """
    _require_db_populated()   # empty-DB guard (first ingest: use "One-click Refresh")
    _require_replay_dir()   # reject directly when path is unavailable (frontend already guards, backend fallback)
    _start_task("ingest", _run_ingest, (limit,))
    return {"status": "started"}


def _run_ingest(limit: int):
    """Background fast scan-and-ingest (progress via the multi-task state, same channel as batch analysis)."""
    try:
        def cb(i, n, name):
            _set_task("ingest", done=i, total=n, current=name)
        results = pipeline.ingest_all_new(progress_cb=cb, limit=limit)
        # Second replay source (LocalLeaderboard, 2026-09, zero-config):
        # ingests LL-only sessions and repairs rows whose original .bsor is
        # gone by pointing them at the surviving LL twin. Per-file results
        # join the common tally; counts ride along for the done toast.
        ll = pipeline.ingest_local_leaderboard()
        results.extend(ll.pop("results", []))
        counts: dict = {"parsed": 0, "duplicate": 0, "error": 0,
                        "unsupported": 0, "total": len(results)}
        for r in results:
            k = r.get("status", "error")
            counts[k] = counts.get(k, 0) + 1
        # take the config path directly (previously pipeline.scan() ran a full directory glob +
        # table-wide known_file_states(), just to echo a single string, P1-3.5)
        counts["replay_dir"] = cfg.replay_dir
        counts["local_leaderboard"] = ll
        _set_task("ingest", running=False, current="",
                  results=[{"kind": "ingest", **counts}])
    except Exception as e:  # noqa: BLE001
        _set_task("ingest", running=False, error=f"{e}\n{traceback.format_exc()}")


@app.post("/api/replays/{replay_id}/analyze")
def api_replay_lazy_analyze(replay_id: str):
    """Detail-page lazy load: full analysis for a pending replay (idempotent,
    instant return when already analyzed).

    Never generates reports (v2.1.0 decision); reports are generated on demand
    via /api/ai/analyze/{id}.
    """
    res = pipeline.analyze_ingested(replay_id)
    if res.get("status") == "error":
        raise HTTPException(422, res.get("error", "分析失败"))
    return res


# ---------- replays ----------
@app.get("/api/replays")
def api_replays(page: int = Query(1, ge=1),
                map_hash: Optional[str] = None, days: Optional[int] = None,
                flat: int = Query(0), limit: int = Query(200, le=2000),
                mode: str = Query("day")):
    """Paginated list.

    mode=day (default): grouped by day, same-day records share one page.
    mode=session: grouped into play sessions (a gap longer than
    ui.session_gap_minutes starts a new session) - solves the two cases
    "by day" gets wrong: a midnight run split across two days, and two separate
    blocks in the same day merged into one.
    mode=count: paginate by count (20 per page, flat list) - the Overview
    page's "by count" mode.
    flat=1 returns a flat list (for compare/history scenarios needing all data).
    """
    if flat:
        replays = repo.list_replays(limit=limit, map_hash=map_hash, days=days)
        enrichment.enrich_flat(replays, _active_platform())
        _attach_file_available(replays)
        return replays
    if mode == "count":
        page_size = 20
        replays = repo.list_replays(limit=100000, map_hash=map_hash, days=days)
        total = len(replays)
        pages = max(1, math.ceil(total / page_size)) if total else 0
        page = max(1, min(page, pages)) if pages else 1
        chunk = replays[(page - 1) * page_size: page * page_size]
        enrichment.enrich_flat(chunk, _active_platform())
        _attach_file_available(chunk)
        return {"replays": chunk, "total": total, "page": page,
                "pages": pages, "mode": "count"}
    if mode == "session":
        data = repo.list_replays_by_session(
            page=page, gap_seconds=max(60, int(cfg.session_gap_minutes) * 60),
            map_hash=map_hash, days=days)
        data["mode"] = "session"
        enrichment.enrich(data.get("days", []), _active_platform())
        for session in data.get("days", []):
            _attach_file_available(session.get("replays", []))
        return data
    data = repo.list_replays_by_day(page=page, map_hash=map_hash, days=days)
    # attach beatmap_key + ranked stars + pp (services/enrichment.py, cached)
    enrichment.enrich(data.get("days", []), _active_platform())
    for day in data.get("days", []):
        _attach_file_available(day.get("replays", []))
    return data


@app.get("/api/replays/{replay_id}")
def api_replay_detail(replay_id: str):
    row = repo.get_replay(replay_id)
    if not row:
        raise HTTPException(404, "replay 不存在")
    row["map"] = repo.get_map(row["map_hash"]) if row.get("map_hash") else None
    row["metrics"] = repo.get_metrics(replay_id)
    row["profile"] = (repo.get_profile(row["profile_id"])
                      if row.get("profile_id") else None)
    row["report"] = repo.get_report(replay_id)
    row["history_same_map"] = repo.previous_attempts_on_map(
        row["map_hash"], row["difficulty"], row["timestamp"] + 1,
        exclude_id=row["replay_id"], limit=10)
    # history entries also carry beatmap_key / stars / pp / nps
    hist = row["history_same_map"]
    if hist:
        enrichment.enrich_flat(hist, _active_platform())
    # the current replay itself carries nps / stars / pp
    enrichment.enrich_flat([row], _active_platform())
    _attach_file_available([row])
    _attach_file_available(row.get("history_same_map", []))
    return row


# Not used by the frontend (the detail response embeds metrics); kept for API
# completeness.
@app.get("/api/replays/{replay_id}/metrics")
def api_replay_metrics(replay_id: str):
    return repo.get_metrics(replay_id)


# ---------- OS integration for the replay's source file (2026-09, right-click menu) ----------
# Both endpoints act on the ORIGINAL .bsor only and never touch the database: ingest is
# add-only by design, so a removed file simply shows up as file_available=false
# (the existing "file missing" badge/notice) while analysis stays readable.

def _run_hidden(args: list[str], wait: bool = False, timeout: int = 30) -> int:
    """Run a Windows helper invisibly (no console flash) and return its exit code.

    Used for shell integration (Explorer / recycle bin). `pythonw` has no console,
    but spawning a console program from it would still flash a window, hence
    CREATE_NO_WINDOW.
    """
    CREATE_NO_WINDOW = 0x08000000
    if wait:
        proc = subprocess.run(args, capture_output=True, creationflags=CREATE_NO_WINDOW,
                              timeout=timeout)
        return proc.returncode
    subprocess.Popen(args, creationflags=CREATE_NO_WINDOW,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


@app.post("/api/replays/{replay_id}/reveal")
def api_replay_reveal(replay_id: str):
    """Open the OS file browser with the replay file selected.

    Falls back to the containing folder when the file no longer exists, so the
    action stays useful after a deletion.
    """
    row = repo.get_replay(replay_id)
    if not row:
        raise HTTPException(404, "replay 不存在")
    fp = row.get("file_path")
    if not fp:
        raise HTTPException(409, "该回放没有记录文件路径")
    target = pathlib.Path(fp)
    # NOTE (2026-09 bugfix): the switch and the path MUST be two separate argv
    # entries ("/select," then the path). Passing them as one argument
    # ("/select,C:\...") makes explorer fail to parse the switch and fall back to
    # opening the user's Documents folder — verified by enumerating Explorer
    # windows through Shell.Application. Do not "tidy" this into one string.
    if target.exists():
        _run_hidden(["explorer.exe", "/select,", str(target)])
        return {"status": "opened", "mode": "select", "path": str(target)}
    folder = target.parent
    if folder.exists():
        _run_hidden(["explorer.exe", str(folder)])
        return {"status": "opened", "mode": "folder", "path": str(folder)}
    raise HTTPException(410, "文件与所在文件夹都不存在")


@app.post("/api/replays/{replay_id}/recycle")
def api_replay_recycle(replay_id: str):
    """Delete a replay: move its .bsor to the OS recycle bin AND drop the record.

    Two explicit steps, in this order (2026-09, user decision):
      1. the file goes to the OS recycle bin — never a permanent delete, so the
         user can always pull it back out;
      2. SaberLab removes the replay record and all its derived data (the user
         deleted the replay; keeping the local analysis is not what they asked for).
         Cloud-side data is untouched.
    A missing file is tolerated (step 1 skipped) so a stale record can still be
    cleaned up; if step 1 fails, step 2 does NOT run, so we never end up with a
    record removed while the file is still sitting in the folder.
    """
    row = repo.get_replay(replay_id)
    if not row:
        raise HTTPException(404, "replay 不存在")
    fp = row.get("file_path")
    target = pathlib.Path(fp) if fp else None
    name = row.get("file_name") or (target.name if target else "")
    file_recycled = False

    if target is not None and target.exists():
        # Microsoft.VisualBasic.FileIO.FileSystem is the documented way to send a file
        # to the recycle bin; it needs no extra dependency and never hard-deletes.
        # The path is base64-encoded so any character (quotes, unicode, spaces)
        # survives the PowerShell command line intact.
        b64 = base64.b64encode(str(target).encode("utf-8")).decode("ascii")
        script = (
            "Add-Type -AssemblyName Microsoft.VisualBasic; "
            f"$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}')); "
            "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
            "$p,'OnlyErrorDialogs','SendToRecycleBin')"
        )
        try:
            rc = _run_hidden(["powershell.exe", "-NoProfile", "-NonInteractive",
                              "-Command", script], wait=True)
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "回收站操作超时")
        if rc != 0:
            raise HTTPException(500, f"移动到回收站失败（退出码 {rc}）")
        file_recycled = True

    counts = repo.delete_replay(replay_id)
    _energy_curve_cache.pop(replay_id, None)      # derived cache must not outlive the record
    return {"status": "deleted", "file_name": name, "path": str(target) if target else "",
            "file_recycled": file_recycled, "removed": counts}


@app.get("/api/replays/{replay_id}/timeline")
def api_replay_timeline(replay_id: str):
    """Chart data (note-anchored; the fixed time-window mode is retired, 2026):
    - notes: per-note cumulative acc/center curves (good cut, x=event time)
             + saber speed (±7 good-cut local mean, smoothed jumps)
             + local density (±5 note neighborhood + ±2 note rounding, natural
               valleys in map gaps - faithful to the data)
             + energy_t/energy: the simulated energy bar (white line, 2026-09)
    - events: miss/bad event timestamps (event step lines)
    - note_range: first/last note times (timeline trim bounds)
    - windows: reserved field (legacy history/empty arrays, backward compat;
               engine no longer writes it)
    """
    from backend.analysis.notes import moving_average, density_series
    from backend.bsor.models import GOOD, BOMB
    events = repo.get_note_events(replay_id)
    notes = repo.get_accuracy_curve(replay_id)
    # saber speed: ±7 local mean of good cuts (raw per-note values jump a lot;
    # 2026-08 user request for smoother viewing; the window is still "the mean
    # of the same batch of real good cuts" - traceable; miss/bad have no points,
    # never pad with 0 or interpolate to fake data)
    good_sp = [(e["event_time"], e["saber_speed"]) for e in events
               if e["event_type"] == GOOD and e["saber_speed"] is not None]
    notes["speed_t"] = [t for t, _ in good_sp]
    notes["speed"] = moving_average([s for _, s in good_sp], 15)
    # density: local density of all non-bomb notes (±5 neighborhood; long gaps
    # naturally dip - faithful to map design); then a ±2 note centered MA
    # smooths sharp jumps at pause edges (valleys kept, easier to read; 2026-08)
    ts_all = [e["event_time"] for e in events if e["event_type"] != BOMB]
    notes["density_t"] = ts_all
    notes["density"] = moving_average(density_series(ts_all, 5), 5)
    # energy bar (2026-09): rebuilt from the replay on demand rather than
    # persisted — the energy module is a pure function of the note/wall events,
    # so a lightweight parse (skip frames, the large section) + simulate costs
    # ~1 ms and keeps the DB free of a redundant derived series. Failures are
    # non-fatal: a missing/broken file simply yields no energy series.
    en = _energy_curve_for(replay_id)
    if en:
        notes["energy_t"], notes["energy"] = en
    return {"windows": repo.get_windows(replay_id),
            "events": repo.get_miss_bad_events(replay_id),
            "notes": notes,
            "note_range": repo.get_note_time_range(replay_id)}


def _energy_curve_for(replay_id: str):
    """[(t, energy)] step-line samples for the timeline, or None when unavailable.

    Cached per replay id (bounded, cleared wholesale when it grows past a small
    cap) — the detail page redraws the chart on every toggle, and re-parsing the
    file each time would be waste.
    """
    global _energy_curve_cache
    cached = _energy_curve_cache.get(replay_id)
    if cached is not None:
        return cached or None
    row = repo.get_replay(replay_id)
    path = (row or {}).get("file_path")
    if not path or not pathlib.Path(path).exists():
        _energy_curve_cache[replay_id] = []
        return None
    try:
        from backend.analysis.energy import EnergyConfig, simulate_energy
        from backend.bsor import parser as bsor_parser
        rep = bsor_parser.parse_file_light(path)
        res = simulate_energy(rep.notes, rep.walls,
                              EnergyConfig.from_modifiers(rep.info.modifiers))
    except Exception as e:                             # noqa: BLE001 — never break the chart
        print(f"[timeline] energy curve unavailable for {replay_id[:12]}: {e}", flush=True)
        _energy_curve_cache[replay_id] = []
        return None
    # Step line: emit (t, value_before) then (t, value_after) per change so the
    # bar reads as flat-then-jump, exactly like the miss/bad event lines.
    ts: list[float] = [0.0]
    vals: list[float] = [res.start_energy]
    for ev in res.events:
        if ev.reason == "fail":
            ts.append(ev.t)
            vals.append(0.0)
            continue
        ts.append(ev.t)
        vals.append(round(ev.energy - ev.delta, 6))
        ts.append(ev.t)
        vals.append(round(ev.energy, 6))
    if len(ts) > 20000:                                # safety cap (chart readability)
        step = len(ts) // 20000 + 1
        ts, vals = ts[::step], vals[::step]
    result = (ts, vals)
    if len(_energy_curve_cache) > 256:                 # bounded: detail view is user-driven
        _energy_curve_cache.clear()
    _energy_curve_cache[replay_id] = result
    return result


@app.get("/api/replays/{replay_id}/series")
def api_replay_series(replay_id: str):
    """Time series for charts: hand speed/angular velocity + accuracy curve."""
    row = repo.get_replay(replay_id)
    if not row:
        raise HTTPException(404, "replay 不存在")
    series = repo.get_motion_series(replay_id) or {}
    return {"motion": series}


@app.get("/api/replays/{replay_id}/slice-details")
def api_replay_slice_details(replay_id: str):
    """SliceDetails (v1.6.0, ported from the SliceDetails mod, qqrz997/ckosmic):
    per-grid-position average cut scores and angles — 12 tiles (4x3 note grid) x
    18 cells (2 colors x 9 directions).

    Computed live from the original .bsor: the notes table has no cut normal,
    which the cut-angle average requires. Parsing is deterministic and cheap
    (pure function over the read-only replay file).
    """
    from backend.bsor.parser import parse_file, BsorError
    from backend.analysis.slicedetails import analyze_slice_details
    row = repo.get_replay(replay_id)
    if not row:
        raise HTTPException(404, "replay 不存在")
    path = row.get("file_path")
    if not path or not pathlib.Path(path).exists():
        raise HTTPException(404, "replay 文件不存在（已移动或删除）")
    try:
        replay = parse_file(path)
    except BsorError as e:
        raise HTTPException(422, f"replay 解析失败: {e}")
    except Exception as e:  # unexpected parse failure must not fake success
        raise HTTPException(500, f"slice-details 计算失败: {e}")
    return analyze_slice_details(replay.notes, height=replay.info.height,
                                 left_handed=replay.info.left_handed)


@app.get("/api/replays/{replay_id}/pp-preview")
def api_replay_pp_preview(replay_id: str):
    """Accuracy-preview for one replay (v2.1.0): the map difficulty's PP as a
    function of accuracy, replicated from ScoreSaber's formula
    pp = maxPP * curve(acc) (see analysis/pp_predict.py; deterministic, no
    network at request time — the curve is embedded).

    Platform-scoped: ScoreSaber only (BeatLeader uses a different, open-source
    formula — not replicated here). The ranked-cache pp is informational: the
    prediction itself only needs the leaderboard's maxPP, so it works for every
    ranked map, including ones whose pp is outside the player's top-100 sync.
    """
    from backend.analysis import pp_predict
    from backend.services.enrichment import pick_leaderboard
    if _active_platform() != "scoresaber":
        raise HTTPException(400, "PP 预测目前仅支持 ScoreSaber 数据源（BeatLeader 公式不同，暂未支持）")
    row = repo.get_replay(replay_id)
    if not row:
        raise HTTPException(404, "replay 不存在")
    mh = (row.get("map_hash") or "").upper()
    diff = row.get("difficulty") or ""
    lbs = [lb for lb in repo.get_ss_leaderboards_by_hash(mh)
           if (lb.get("difficulty_name") or "") == diff]
    lb = pick_leaderboard(lbs)
    if not lb or not lb.get("ranked") or not lb.get("max_pp"):
        raise HTTPException(404, "该谱面在 ScoreSaber 上不是 ranked 谱面（或星级数据未同步，请先联网更新）")
    max_pp = float(lb["max_pp"])
    # The preview explores PP at the accuracy shown on this replay. NF's
    # effective-score penalty affects its awarded/list PP, but must not move
    # the preview slider away from the visible replay accuracy.
    acc = row.get("accuracy")
    cached = repo.get_ranked_cache(mh, diff, platform="scoresaber")
    payload = pp_predict.preview_payload(max_pp, default_acc=acc)
    payload.update({
        "song_name": row.get("song_name"),
        "difficulty": diff,
        "stars": lb.get("stars"),
        "replay_pp": cached.get("pp") if cached else None,
    })
    return payload


@app.get("/api/history")
def api_history(map_hash: Optional[str] = None, days: Optional[int] = None,
                limit: int = Query(2000, le=50000)):
    """Flat replay list for the history page.

    `limit` upper bound is 50000 (2026-09): the history page fetches the whole
    library when a search term is present, because searching only the newest N
    rows silently hides older matches (measured: with a 2000 cap on a 6000-replay
    library, 67% of matches were invisible). The client filters and pages locally —
    measured 2 ms to filter+sort 6000 rows and 13 ms to render a 300-row page,
    so the fetch size is the only real constraint (~1083 bytes/row).
    """
    replays = repo.list_replays(limit=limit, map_hash=map_hash, days=days)
    # attach beatmap_key / nps / stars / pp (the history list's highlight search needs the key)
    enrichment.enrich_flat(replays, _active_platform())
    _attach_file_available(replays)
    return replays


# ---------- compare ----------
@app.get("/api/compare")
def api_compare(a: str = Query(...), b: str = Query(...)):
    ma = repo.get_metrics(a)
    mb = repo.get_metrics(b)
    if not ma or not mb:
        raise HTTPException(404, "其中一个 replay 没有指标数据")
    ra, rb = repo.get_replay(a), repo.get_replay(b)
    return {"a": ra, "b": rb, "rows": compare_metrics(ma, mb)}


# ---------- 3D replay data channel (ChroViewer port) ----------
@app.get("/api/replays/{replay_id}/raw")
def api_replay_raw(replay_id: str):
    """Return the raw .bsor byte stream (for the 3D replay frontend to parse)."""
    row = repo.get_replay(replay_id)
    if not row:
        raise HTTPException(404, "replay 不存在")
    path = row.get("file_path")
    if not path or not pathlib.Path(path).exists():
        raise HTTPException(410, "原始 .bsor 文件已不存在")
    return FileResponse(path, media_type="application/octet-stream",
                        filename=row.get("file_name") or "replay.bsor")


@app.get("/api/maps/{map_hash}/package")
def api_map_package(map_hash: str):
    """Zip the map folder (the 3D replay frontend unpacks it with fflate).

    Contains info.dat / each difficulty's .dat / cover / audio (.egg as-is;
    phase 3 handles backend decryption). Uses ZIP_STORED (no compression):
    map files are mostly already-compressed (audio/images); DEFLATE degrades
    badly on high-entropy data (encrypted .egg) and can hang the request.
    """
    m = repo.get_map(map_hash.strip().upper())
    if not m:
        raise HTTPException(404, "谱面不存在")
    folder = pathlib.Path(m.get("path") or "")
    if not folder.exists():
        raise HTTPException(410, "谱面文件夹已不存在")
    import io
    import zipfile
    files = [f for f in folder.rglob("*") if f.is_file()]
    total_size = sum(f.stat().st_size for f in files)
    # guard: reject oversized maps (>500MB) to avoid overloading the server
    if total_size > 500 * 1024 * 1024:
        raise HTTPException(413, f"谱面文件夹过大 ({total_size // 1048576}MB)")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for f in sorted(files):
            zf.write(f, f.relative_to(folder).as_posix())
    # note: do not hand BytesIO to StreamingResponse - sync file objects iterate
    # by "lines" (\n splits); a 14MB zip becomes 120k+ tiny chunks (each chunk
    # costs a threadpool dispatch + an HTTP chunk); even locally it takes 5+
    # minutes to transfer, so the frontend's 60s timeout will cut it off.
    # The whole package is already in memory, return it in one shot (with Content-Length).
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=map.zip"},
    )


# ---------- maps ----------
# Not used by the frontend; kept for API completeness.
@app.get("/api/maps")
def api_maps():
    return repo.list_maps()


def _run_map_scan():
    """Background map-library scan (progress via the multi-task state, polled by the frontend progress bar)."""
    try:
        def cb(i, n, name):
            _set_task("map_scan", done=i, total=n, current=f"谱面扫描:{name[:40]}")
        stats = resolver.scan(progress_cb=cb)
        _set_task("map_scan", running=False, current="",
                  results=[{"kind": "map_scan", **stats}])
    except Exception as e:  # noqa: BLE001
        _set_task("map_scan", running=False, error=f"{e}\n{traceback.format_exc()}")


# DEPRECATED (2026-08): not used by the frontend (one-click refresh covers it);
# kept for API compatibility, intentional no-delete.
@app.post("/api/maps/rescan")
def api_maps_rescan():
    _require_db_populated()   # empty-DB guard (map scan: use "One-click Refresh")
    _require_maps_dir()   # reject directly when the maps dir is unavailable (frontend already guards, backend fallback)
    _start_task("map_scan", _run_map_scan)
    return {"status": "started"}


# Not used by the frontend; kept for API completeness.
@app.get("/api/maps/{map_hash}")
def api_map_detail(map_hash: str):
    m = repo.get_map(map_hash)
    if not m:
        raise HTTPException(404, "谱面不存在")
    return m


@app.get("/api/maps/{map_hash}/cover")
def api_map_cover(map_hash: str):
    p = resolver.cover_path(map_hash)
    if p is None:
        # hardening: when the DB row lacks a path / the maps dir was just configured,
        # lazily run one targeted scan first (fixes all-default covers after first ingest until restart)
        try:
            resolver.ensure_map_path(map_hash)
            p = resolver.cover_path(map_hash)
        except Exception:  # noqa: BLE001
            p = None
    if p is None:
        # map missing / original file deleted: return the default cover, no 404
        default = FRONTEND_DIR / "default.png"
        if default.exists():
            return FileResponse(default)
        raise HTTPException(404, "无封面")
    return FileResponse(p)


# ---------- profiles & experiments ----------
class ProfileBody(BaseModel):
    name: str
    position_x: float = 0.0
    position_y: float = 0.0
    position_z: float = 0.0
    rotation_x: float = 0.0
    rotation_y: float = 0.0
    rotation_z: float = 0.0
    notes: str = ""


# API-only by design (README: A/B experiment records); no UI surface.
@app.get("/api/profiles")
def api_profiles():
    return repo.list_profiles()


@app.post("/api/profiles")
def api_create_profile(body: ProfileBody):
    pid = repo.create_profile({**body.model_dump(), "source": "manual"})
    return {"profile_id": pid}


class ExperimentBody(BaseModel):
    hypothesis: str
    profile_id: Optional[str] = None
    baseline_replay_id: Optional[str] = None
    candidate_replay_id: Optional[str] = None


# API-only by design (README: A/B experiment records); no UI surface.
@app.get("/api/experiments")
def api_experiments():
    return repo.list_experiments()


@app.post("/api/experiments")
def api_create_experiment(body: ExperimentBody):
    eid = repo.create_experiment(body.model_dump())
    return {"experiment_id": eid}


# ---------- AI ----------
@app.post("/api/ai/analyze/{replay_id}")
def api_ai_analyze(replay_id: str, lang: str = Query("zh-CN")):
    """Generate an AI report. `lang` = the frontend UI language code (zh-CN /
    en-US / ...), injected into the system prompt as the output-language directive (see ai/prompts.py build_system_prompt)."""
    if not repo.get_replay(replay_id):
        raise HTTPException(404, "replay 不存在")
    rep = run_ai_report(repo, cfg, replay_id, llm, None, lang=lang)
    return rep


@app.get("/api/reports/{replay_id}")
def api_report(replay_id: str):
    rep = repo.get_report(replay_id)
    if not rep:
        raise HTTPException(404, "暂无报告")
    return rep


# ---------- ACC-weighted skill ratings -> star palettes (2026-09) ----------
# Each track (80% / 94% / 96% accuracy) becomes its own selectable palette, but only
# when the model could actually produce a rating for it. A track with insufficient
# direct evidence stays out of the selectable set entirely (the settings UI shows
# "数据不足" and disables it) -- the model never guesses a number to fill the gap.
PALETTE_TRACK_KEYS = ("personal96", "personal94", "personal80")     # best track first
# The public ids are `personal<target>` (config enum, /api payloads, i18n). The cache
# columns kept short names (r80/r94/r96) -- they are a storage detail and the live
# database already has them that way -- so this map is the single translation point.
TRACK_CACHE_COLUMNS = {"personal96": "r96", "personal94": "r94", "personal80": "r80"}


def _track_column(key: str) -> str:
    return TRACK_CACHE_COLUMNS[key]


def _track_label(key: str) -> str:
    return {"personal80": "80% 基准", "personal94": "94% 基准", "personal96": "96% 基准"}[key]


def _palette_cache_for(platform: str | None = None) -> dict | None:
    platform = platform or _active_platform()
    pid = _scoresaber_id()
    if not pid:
        return None
    cached = repo.get_player_palette(platform, pid)
    if not cached:
        return None
    if not any(cached.get(_track_column(k)) is not None for k in PALETTE_TRACK_KEYS):
        return None
    return cached


def _palette_meta(key: str, cached: dict) -> dict:
    column = _track_column(key)
    return {
        "stars": cached.get(column),
        "direct_count": cached.get(f"{column}_direct"),
        "lower_bound": cached.get(f"{column}_lower_bound"),
        "confidence": cached.get(f"{column}_confidence"),
        "computed_at": cached.get("computed_at"),
    }


def _palette_entries(cached: dict | None) -> list[dict]:
    """Palette definitions for every track that produced a rating."""
    entries: list[dict] = []
    for key in PALETTE_TRACK_KEYS:
        stars = cached.get(_track_column(key)) if cached else None
        if stars is None:
            continue
        entries.append({
            "id": key,
            "label": _track_label(key),
            "tiers": build_tiers(stars),
            "meta": _palette_meta(key, cached),
        })
    return entries


def _available_palette_keys(cached: dict | None) -> list[str]:
    return [e["id"] for e in _palette_entries(cached)]


def _palette_availability(cached: dict | None) -> dict:
    """Per-option availability for the settings UI.

    A track without a rating is reported as unavailable with a short factual reason,
    so the dropdown can grey it out instead of offering a choice that cannot work.
    """
    available = _available_palette_keys(cached)
    options: dict = {"community": {"available": True}}
    for key in PALETTE_TRACK_KEYS:
        if key in available:
            options[key] = {"available": True, "meta": _palette_meta(key, cached or {})}
        else:
            options[key] = {"available": False, "reason": skill_model.INSUFFICIENT_LABEL}
    return options


def _active_palette_id(cached: dict | None) -> str:
    """Selected palette id with safe fallback.

    Historic configs stored ``personal`` (the replaced classifier's single palette);
    it now means "whichever track is available", best first, so an existing choice
    keeps working instead of silently reverting to the community palette.
    """
    want = cfg.star_palette
    available = _available_palette_keys(cached)
    if want == "personal":
        want = available[0] if available else "community"
    if want in PALETTE_TRACK_KEYS:
        return want if want in available else (available[0] if available else "community")
    return want if get_star_palette(want) else "community"


def _palette_result(platform: str, pid: str) -> dict | None:
    """/api payload of the cached palette for a player/platform (public ids)."""
    cached = repo.get_player_palette(platform, pid)
    if not cached:
        return None
    if not any(cached.get(_track_column(k)) is not None for k in PALETTE_TRACK_KEYS):
        return None
    return _palette_public_payload(cached)


def _score_record_from_payload(s: dict) -> skill_model.ScoreRecord | None:
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


def _palette_public_payload(cached: dict) -> dict:
    """Cache row -> API payload, translating cache columns to public ids.

    Cache stores ratings in short columns (r80/r94/r96); every API consumer sees them
    as `personal80/personal94/personal96` (plus `_direct` / `_lower_bound` /
    `_confidence` suffixes). ``method`` carries the selected track in public form.
    """
    payload: dict = {
        "status": "known" if cached.get("yellow_stars") is not None else "unknown",
        "yellow_stars": cached.get("yellow_stars"),
        "computed_at": cached.get("computed_at"),
        "skill_params": cached.get("skill_params"),
    }
    for key in PALETTE_TRACK_KEYS:
        column = _track_column(key)
        payload[key] = cached.get(column)
        payload[f"{key}_direct"] = cached.get(f"{column}_direct")
        payload[f"{key}_lower_bound"] = cached.get(f"{column}_lower_bound")
        payload[f"{key}_confidence"] = cached.get(f"{column}_confidence")
    method = cached.get("method")
    if method not in PALETTE_TRACK_KEYS:
        # a cache row written before the ids were renamed still names the track "r80";
        # map it so the UI can still tell which baseline is in effect
        method = next((k for k in PALETTE_TRACK_KEYS if _track_column(k) == method), None)
    payload["method"] = method
    return payload


def _skill_palette_payload(platform: str, pid: str, scores: list) -> dict:
    """Run the ACC-weighted skill model over freshly fetched scores and cache it.

    ``yellow_stars`` stays the colour anchor: the best track that produced a rating
    (personal96 > personal94 > personal80). Tracks without enough direct evidence stay
    NULL, which the settings UI turns into "数据不足" and a disabled option -- the model
    is not allowed to guess a number there.
    """
    records = [r for r in (_score_record_from_payload(s) for s in scores) if r is not None]
    ratings = skill_model.rate_player(records, time.time())
    available = skill_model.available_track_keys(ratings)      # personal96, personal94, personal80 order

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
        column = _track_column(track.key)          # cache columns are short (r80...)
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
    return _palette_public_payload(payload)


def _compute_and_cache_palette(platform: str, pid: str, scores: list) -> dict:
    """Skill-model palette over freshly fetched scores, persisted, returned."""
    return _skill_palette_payload(platform, pid, scores)


# ---------- cloud data page (platform-scoped: /api/scoresaber | /api/beatleader) ----------
# How much score history the cloud sync pulls. The skill model rates a player at three
# target accuracies, so it needs plays spread over the accuracy range, not just the most
# recent page (the API caps one page at 100 rows).
CLOUD_SCORE_LIMIT = 300
CLOUD_SCORE_PAGES = 3


def _cloud_page_get(platform: str):
    """Shared GET logic: serve the cached profile/scores (+ personal palette)
    for a platform, or fetch on first visit."""
    pid = _scoresaber_id()   # auto-resolved from BSOR (= Steam ID); the config setting is deprecated
    cached = repo.get_player_cache(platform, pid)
    if cached:
        return {**cached, "palette": _palette_result(platform, pid)}
    return _cloud_page_refresh(platform)


def _cloud_page_refresh(platform: str):
    """Shared POST logic: fetch profile + scores for the platform, compute and
    cache the personal palette in one step ("拉取数据并计算动态水平").

    The score fetch is deliberately wider than one page: the skill model needs plays
    near each target accuracy (80% / 94% / 96%), and a player's recent page alone can
    miss whole bands. For ScoreSaber the recent and top lists are merged (both are
    capped by the API at 100 rows per page); BeatLeader is paged by date.
    """
    pid = _scoresaber_id()
    if not pid:
        raise HTTPException(400, "库中无 Replay 数据，无法解析玩家 ID")
    if platform == "beatleader":
        try:
            profile = beatleader.fetch_profile(cfg, pid)
            scores = beatleader.fetch_scores(cfg, pid, limit=CLOUD_SCORE_LIMIT,
                                             max_pages=CLOUD_SCORE_PAGES)
        except beatleader.BeatLeaderError as e:
            raise HTTPException(502, str(e))
    else:
        try:
            profile = scoresaber.fetch_profile(cfg, pid)
            merged: dict = {}
            for sort in ("recent", "top"):
                for row in scoresaber.fetch_scores(cfg, pid, limit=CLOUD_SCORE_LIMIT,
                                                   sort=sort, max_pages=CLOUD_SCORE_PAGES):
                    key = row.get("score_id") or (row.get("song_hash"), row.get("difficulty"))
                    merged.setdefault(key, row)
            scores = list(merged.values())
            scores.sort(key=lambda r: r.get("time_set") or "", reverse=True)
        except scoresaber.ScoreSaberError as e:
            raise HTTPException(502, str(e))
    repo.save_player_cache(platform, pid, profile, scores)
    # Sidebar player card images (avatar + country flag): downloaded here so the
    # card works offline afterwards. Best effort on purpose - a CDN failure must
    # not fail the sync (the cached snapshot and any previously cached image stay
    # valid); it only means the card keeps the old image or falls back to the
    # name initial / country code. Keys whose source URL is missing are skipped
    # (no attempt, no log).
    for kind, attempted in player_assets.sync_player_assets(
            cfg, platform, pid, profile).items():
        if attempted and not attempted.stored:
            print(f"[player] {kind} download failed ({platform})"
                  " — keeping cached image", flush=True)
    palette = _compute_and_cache_palette(platform, pid, scores)
    return {"fetched_at": datetime.now(timezone.utc).isoformat(),
            "profile": profile, "scores": scores, "palette": palette}


@app.get("/api/scoresaber")
def api_scoresaber():
    return _cloud_page_get("scoresaber")


@app.post("/api/scoresaber/refresh")
def refresh_scoresaber():
    return _cloud_page_refresh("scoresaber")


@app.get("/api/beatleader")
def api_beatleader():
    return _cloud_page_get("beatleader")


# ---------- sidebar player card (2026-09) ----------
# Both endpoints are read-only and serve local data only: the sync
# (_cloud_page_refresh) is what talks to the network, so opening the app
# offline still renders the card from the last snapshot.

@app.get("/api/player/card")
def api_player_card():
    """Card fields (name / country / global rank / country rank / image URLs).

    `profile` is None when that platform has never been synced - a normal state
    (fresh install, or the other data source selected), not an error: the
    frontend keeps the block hidden instead of showing a failure."""
    pid = _scoresaber_id()
    platform = _active_platform()
    cached = repo.get_player_cache(platform, pid) if pid else None
    profile = (cached or {}).get("profile")
    return {"platform": platform,
            "profile": player_assets.card_payload(cfg, platform, pid, profile)}


def _cached_image(payload: tuple[bytes, str, float] | None, missing: str) -> Response:
    """Serve one locally cached player image (no network access).

    404 when it was never downloaded - the frontend then falls back to the
    player's name initial / country code instead of showing a broken image."""
    if payload is None:
        raise HTTPException(404, missing)
    data, mime, _mtime = payload
    # The URL carries ?v=<file mtime>, so the bytes for a given URL never change:
    # let the WebView cache them hard instead of re-fetching on every page load.
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "public, max-age=604800"})


@app.get("/api/player/avatar")
def api_player_avatar():
    """Cached avatar of the current player on the active platform."""
    return _cached_image(
        player_assets.read_avatar(cfg, _active_platform(), _scoresaber_id()),
        "未缓存玩家头像")


@app.get("/api/player/flag")
def api_player_flag():
    """Cached country flag of the current player (shared by both platforms)."""
    platform = _active_platform()
    pid = _scoresaber_id()
    cached = repo.get_player_cache(platform, pid) if pid else None
    country = str(((cached or {}).get("profile") or {}).get("country") or "")
    return _cached_image(player_assets.read_flag(cfg, country), "未缓存国家旗帜")


@app.post("/api/beatleader/refresh")
def refresh_beatleader():
    return _cloud_page_refresh("beatleader")


@app.get("/api/scoresaber/validate")
def api_scoresaber_validate():
    """Cross-validation (ScoreSaber-only): locally parsed scores vs ScoreSaber
    recorded scores."""
    local = repo.list_replays(limit=500)
    result = scoresaber.cross_validate(cfg, _scoresaber_id(), local)
    return result


# DEPRECATED (2026-08): not used by the frontend (one-click refresh covers it);
# kept for API compatibility, intentional no-delete.
@app.post("/api/scoresaber/update-ranked")
def api_scoresaber_update_ranked():
    """Background fill of the (map_hash, difficulty) -> stars/pp cache for the
    ACTIVE platform (cloud data, local files untouched)."""
    _require_db_populated()   # empty-DB guard (stars sync: use "One-click Refresh")
    _require_maps_dir()   # sync rooted at local maps; reject if the maps dir is unavailable
    _start_task("ranked_update", _run_ranked_update)
    return {"status": "started"}


# DEPRECATED (2026-08): not used by the frontend (one-click refresh covers it);
# kept for API compatibility, intentional no-delete.
@app.post("/api/maps/update-nps")
def api_maps_update_nps():
    """Compute NPS (block density) for all maps in the background."""
    _require_db_populated()   # empty-DB guard (NPS computation: use "One-click Refresh")
    _require_maps_dir()   # reject directly when the maps dir is unavailable
    _start_task("nps_update", _run_nps_update)
    return {"status": "started"}


# ---------- one-click refresh / online update ----------
@app.post("/api/refresh/all")
def api_refresh_all(body: AnalyzeBody | None = None):
    """One-click refresh: trigger all 5 task groups in parallel (ingest /
    batch analysis / map scan / NPS / online data).

    Incremental semantics (v1.4.1): only new/changed data is processed -
    ingest/batch dedup by sha256+mtime, map_scan reuses the DB by folder mtime,
    nps_update skips already-computed unchanged maps, ranked_update syncs only
    new uncached maps. The online task also refreshes the active platform's
    player profile/recent scores and recomputes the dynamic level.
    """
    _require_replay_dir()
    _require_maps_dir()
    # No reports (v2.1.0 decision): batch analysis never generates them; the
    # detail page generates reports on demand via /api/ai/analyze/{id}
    # (the settings toggle ai.ai_report_enabled decides LLM vs rule there).
    started = []
    for kind, fn, args in (("ingest", _run_ingest, (0,)),
                           ("batch", _run_batch, (0,)),
                           ("map_scan", _run_map_scan, ()),
                           ("nps_update", _run_nps_update, ()),
                           ("ranked_update", _run_ranked_update, (True, True))):
        try:
            _start_task(kind, fn, args)
            started.append(kind)
        except HTTPException:
            pass   # skip if the same kind is already running
    return {"status": "started", "tasks": started}


@app.post("/api/refresh/online")
def api_refresh_online():
    """Re-update data online: only force-refresh cloud values (stars/pp); local
    analysis data stays untouched.

    Use case: ScoreSaber adjusted cloud data such as map stars (local hard
    metrics need no recomputation).
    """
    _require_db_populated()   # empty-DB guard (cloud sync is based on ingested map hashes)
    _require_maps_dir()
    _start_task("ranked_update", _run_ranked_update)
    return {"status": "started"}


@app.get("/api/network/check")
def api_network_check():
    """Check whether this machine can reach ScoreSaber (internet connectivity).

    The frontend calls this before "Re-update data online"; offline blocks and
    toasts. HTTPError (404 etc.) means the server is reachable -> online;
    URLError/timeout -> offline.
    """
    try:
        with urllib.request.urlopen(
                "https://scoresaber.com/api/", timeout=4):
            return {"online": True}
    except urllib.error.HTTPError:
        return {"online": True}    # server reachable (may be a 404)
    except Exception:  # noqa: BLE001 network down / timeout / DNS failure
        return {"online": False}


# ---------- settings ----------
@app.get("/api/i18n/langs")
def api_i18n_langs():
    """Discover available UI languages by scanning frontend/i18n/*.json.

    Each language file provides its own display name via the "lang.name"
    key (e.g. zh-CN.json -> "简体中文"). Adding a new file is enough to
    enable the language — the frontend renders the switch buttons
    dynamically from this response (2026-08).
    """
    import re
    d = FRONTEND_DIR / "i18n"
    langs = []
    if d.exists():
        for p in sorted(d.glob("*.json")):
            code = p.stem
            if not re.fullmatch(r"[a-z]{2}(-[A-Z]{2})?", code):
                continue
            name = None
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                name = data.get("lang.name")
            except (json.JSONDecodeError, OSError):
                pass
            langs.append({"code": code, "name": name or code})
    # zh-CN (baseline language) first, others in alphabetical order
    langs.sort(key=lambda x: (x["code"] != "zh-CN", x["code"]))
    return {"langs": langs}


class SettingsBody(BaseModel):
    instance_root: str = ""


class SettingsSaveBody(BaseModel):
    values: dict = {}


# DEPRECATED (2026-08): not used by the frontend (it uses /api/settings/schema);
# kept for API compatibility, intentional no-delete.
@app.get("/api/settings")
def api_settings():
    """Return the current config view (without secrets)."""
    return config_svc.view().to_dict()


@app.get("/api/settings/schema")
def api_settings_schema():
    """Return the schema + current values (secrets masked) so the frontend can dynamically generate the settings UI.

    The star-palette option list is enriched with per-option availability: tracks the
    skill model could not rate are marked unavailable (with a factual reason) and the
    settings dropdown disables them rather than offering an unusable choice.
    """
    from backend.config.schema import get_schema
    schema = get_schema()
    palette_cache = _palette_cache_for()
    availability = _palette_availability(palette_cache)
    for item in schema:
        if item.get("key") == "player.star_palette":
            item["option_meta"] = availability
            break
    values = config_svc.get_all_values()
    # Historic configs stored "personal" for the replaced classifier's single palette.
    # Resolve it to the track that is actually available so the settings form shows a
    # real selection instead of an option that no longer exists in the enum.
    if values.get("player.star_palette") == "personal":
        values["player.star_palette"] = _active_palette_id(palette_cache)
    return {"schema": schema, "values": values}


@app.post("/api/settings/validate")
def api_settings_validate(body: SettingsBody | None = None):
    """Validate the game root directory and its derived paths.

    valid = root AND maps directories exist (core check, shown as the badge
    next to the title); results lists each path's details (root / Replay /
    maps / SongCore / local replay retention).

    The last item is not a path: it reports the BeatLeader mod's
    "keep latest only" replay setting (which deletes older local .bsor files,
    see HANDOFF §4.25). It is advisory only — it never affects `valid`.
    """
    root = (body.instance_root if body else "").strip() or cfg.instance_root
    results = []
    for s in check_paths(root):
        results.append({
            "key": s.key, "label": s.label, "path": s.path,
            "exists": s.exists, "ok": s.ok, "note": s.note,
            "status": s.status,
        })
    by_key = {r["key"]: r for r in results}
    valid = bool(by_key.get("instance_root", {}).get("ok") and
                 by_key.get("custom_levels_dir", {}).get("ok"))
    return {"instance_root": root, "valid": valid, "results": results}


@app.post("/api/settings/folder-dialog")
def api_settings_folder_dialog():
    """Open the native folder-picker dialog (pywebview window mode; browser mode
    returns unavailable and the frontend falls back to manual path input)."""
    from backend.dialog import request_folder_dialog  # lazy import to avoid circular dependencies
    return request_folder_dialog()


@app.post("/api/settings")
def api_settings_save(body: SettingsSaveBody | None = None):
    """Batch-save config (atomic write-back to config.yaml / .env).

    Hot-reloads the runtime config on success (path settings apply immediately,
    no restart needed). When analysis parameters (analysis.*) actually change
    (v1.4.1): metrics were computed with the old parameters, so the analysis
    cache is cleared and replays reset to pending - the detail-page lazy
    analysis recomputes with the new parameters. The settings form submits
    every visible field on each save, so the reset must key on
    save_values' actually-changed list, not on mere submission (2026-08 fix:
    saving an untouched form used to wipe the analysis cache every time).
    """
    updates = body.values if body and body.values else {}
    if not updates:
        return {"saved": False, "error": "没有要保存的配置"}
    res = config_svc.save_values(updates)
    if res.get("saved"):
        reload_runtime_config()
        changed = res.get("changed") or []
        if any(k.startswith("analysis.") for k in changed):
            cache_res = repo.reset_analysis_cache()
            enrichment.invalidate()
            res["message"] = cache_res["message"]
    return res


@app.post("/api/settings/root")
def api_settings_save_root(body: SettingsBody):
    """Save the game root directory (compat entry; hot-reloads on success, applies immediately)."""
    res = config_svc.save_instance_root(body.instance_root)
    if res.get("saved"):
        reload_runtime_config()
    return res


@app.post("/api/restart")
def api_restart():
    """In-app restart (the settings page's "Restart SABER LAB" button).

    Schedules host.py's restart callback via the dialog bridge: a background
    thread launches the new process after a delay, then the current process
    exits gracefully (window/browser modes; restarts the exe when frozen).
    """
    from backend.dialog import request_restart  # lazy import to avoid circular dependencies
    return request_restart()


@app.post("/api/settings/clear-cache")
def api_settings_clear_cache():
    """Clear the SABER LAB analysis cache (the double confirmation happens in the frontend)."""
    result = repo.clear_analysis_cache()
    # map_ranked_cache / scoresaber_leaderboards were cleared -> enrichment snapshot invalidated
    enrichment.invalidate()
    return result


# ---------- desktop integration (frosted-glass plan C: wallpaper push, see others/毛玻璃方案探索.md) ----------
@app.post("/api/desktop/backdrop-ready")
def api_desktop_backdrop_ready():
    """Frontend acrylic-layer ready notification (page load / language-switch
    reload). The host wallpaper service thread consumes this flag and
    re-pushes the backdrop payload — without it, a reload permanently loses
    the wallpaper background because the service thread only pushes on
    wallpaper/monitor changes (2026-08 fix).
    """
    from backend.dialog import set_backdrop_ready
    set_backdrop_ready()
    return {"ok": True}


@app.get("/api/desktop/backdrop")
def api_desktop_backdrop(hwnd: int = Query(0)):
    """Data for the window's frosted-glass layer: window/monitor geometry +
    the wallpaper URL.

    hwnd is passed by the host (backend/host.py); unavailable when no hwnd is
    given. Loopback-only (local tooling), not called in browser mode.
    """
    from backend import desktop
    if not hwnd:
        return {"available": False}
    wallpaper = desktop.get_wallpaper_path()
    wallpaper_url = "/api/desktop/wallpaper" if wallpaper else ""
    payload = desktop.backdrop_payload(hwnd, wallpaper_url)
    if wallpaper is None:
        payload["background_color"] = desktop.get_desktop_background_color()
    return payload


@app.get("/api/desktop/wallpaper")
def api_desktop_wallpaper():
    """Return the original desktop wallpaper image (used as the frosted-glass layer background)."""
    from backend import desktop
    wallpaper = desktop.get_wallpaper_path()
    if not wallpaper:
        raise HTTPException(404, "未找到桌面壁纸文件（可能是纯色桌面）")
    return FileResponse(wallpaper, media_type="image/jpeg")


# ---------- frontend ----------
@app.get("/", response_class=HTMLResponse)
def index():
    resp = FileResponse(FRONTEND_DIR / "index.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


app.mount("/static", NoCacheStaticFiles(directory=FRONTEND_DIR), name="static")

# 3D replay viewer: external GPL-2.0 plugin (Local-ChroViewer, independent
# project). SaberLab does not ship the viewer source; the plugin's build output
# must be present under the first-party plugin directory plugins/chro/ (which
# also covers the packaged layout, since frozen PROJECT_ROOT == <exe dir>).
# When present it is mounted at /chro/ and reported in /api/status (the UI
# shows an install hint otherwise). No fallback paths: removing the plugin
# directory must disable the viewer.
_CHRO_PLUGIN_DIR = PROJECT_ROOT / "plugins" / "chro"
_CHRO_DIST = _CHRO_PLUGIN_DIR if (_CHRO_PLUGIN_DIR / "index.html").exists() else None
if _CHRO_DIST is not None:
    app.mount("/chro", NoCacheStaticFiles(directory=_CHRO_DIST, html=True), name="chro")
CHRO_AVAILABLE = _CHRO_DIST is not None


def main():
    import uvicorn
    print(f"SaberLab starting on http://{cfg.host}:{cfg.port}")
    print(f"  replay dir : {cfg.replay_dir}")
    print(f"  levels dir : {cfg.custom_levels_dir}")
    print(f"  database   : {cfg.db_path}")
    print(f"  AI provider: {cfg.ai_provider} ({'configured' if llm.configured else 'NOT configured -> rule-based fallback'})")
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
