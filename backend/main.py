"""SaberLab FastAPI entry point.

Start:  .venv\\Scripts\\python.exe backend\\main.py
Panel:  http://127.0.0.1:8787
"""
from __future__ import annotations

import json
import math
import pathlib
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
from backend.config.service import ConfigService, check_paths
from backend.db.repository import Repository
from backend.maps.resolver import MapResolver
from backend.services.enrichment import EnrichmentService
from backend.watcher import ReplayPipeline
from backend.analysis.compare import compare_metrics
from backend.ai.provider import LLMClient
from backend.ai.context import build_context
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
    config.yaml and sync to resolver/pipeline; path settings apply at once."""
    global cfg
    cfg = load_config()
    resolver.update_paths(cfg.custom_levels_dir, cfg.songcore_cache)
    pipeline.update_config(cfg)


def _scoresaber_id() -> str:
    """Resolve the ScoreSaber player ID (key finding: ScoreSaber ID = Steam ID,
    BSOR replays carry a 17-digit platform ID): prefer the historical manual
    config (config fallback); otherwise take the most recent play's player
    ("current player" intuition in multi-player libraries, not the mode)."""
    cfg_id = (cfg.scoresaber_id or "").strip()
    if cfg_id:
        return cfg_id
    return repo.latest_player_id()


def _path_ok(p: str) -> bool:
    try:
        return bool(p) and pathlib.Path(p).exists()
    except OSError:
        return False


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
            400, "数据库为空：请先点击总览「⚡ 一键刷新」完成首次扫描"
                 "（入库 + 谱面库 + NPS + 联网星级同步）")

app = FastAPI(title="SaberLab", version="1.5.0",
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


def _run_batch(limit: int, run_ai: bool, lang: str = "zh-CN"):
    try:
        _wait_ingest_done("batch")
        def cb(i, n, name):
            _set_task("batch", done=i, total=n, current=name)
        results = pipeline.analyze_all_new(progress_cb=cb, limit=limit,
                                           run_ai=run_ai, ai_client=llm,
                                           build_context=build_context, lang=lang)
        _set_task("batch", running=False, results=results, current="")
    except Exception as e:  # noqa: BLE001
        _set_task("batch", running=False, error=f"{e}\n{traceback.format_exc()}")


def _run_ranked_update(only_missing: bool = False):
    """Background sync: rooted at local maps, batch-fetch ScoreSaber leaderboard
    metadata.

    1. Collect hashes of maps with replay records (deduplicated)
    2. get-difficulties + by-id info -> scoresaber_leaderboards (stars cache, map attributes)
    3. player score index -> map_ranked_cache (pp cache, personal play history)

    only_missing (v1.4.1, one-click refresh): skip cached maps, only sync new
    ones - one-click refresh finds new data, cloud old values are not
    re-fetched; only "Re-update data online" (force) refreshes cloud values.
    """
    try:
        _wait_ingest_done("ranked_update")   # wait for ingest before collecting map hashes (post-clear one-click refresh scenario)
        replays = repo.list_replays(limit=100000)
        hashes = sorted({(r.get("map_hash") or "").upper() for r in replays
                         if r.get("map_hash")})

        def cb(i, n, name):
            _set_task("ranked_update", done=i, total=n, current=f"谱面同步:{name}")
        stats = scoresaber.sync_maps_batch(cfg, repo, hashes, progress_cb=cb,
                                           only_missing=only_missing)
        stats["leaderboards_total"] = repo.count_ss_leaderboards()

        # pp filling: player score index ((hash, difficulty) -> pp), ID auto-resolved from BSOR;
        # skip pp when there is no Replay in the DB and no config fallback (avoid ScoreSaber empty-ID 404)
        pid = _scoresaber_id()
        idx = scoresaber.build_ranked_index(cfg, pid) if pid else {}
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        for r in replays:
            mh = (r.get("map_hash") or "").upper()
            df = r.get("difficulty") or ""
            info = idx.get((mh, df))
            if info:
                repo.upsert_ranked_cache(mh, df, None, info.get("pp"), now)
        stats["pp_indexed"] = len(idx)

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
    return {
        "ok": True,
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
        },
    }


# ---------- scan & analyze ----------
@app.post("/api/scan")
def api_scan():
    return pipeline.scan()


class AnalyzeBody(BaseModel):
    run_ai: bool = True
    lang: str = "zh-CN"   # UI language code: output language for batch-analysis AI reports


@app.post("/api/analyze/latest")
def api_analyze_latest(body: AnalyzeBody | None = None):
    # Reports are always generated; whether the LLM is called is decided by
    # the settings toggle ai.ai_report_enabled inside run_ai_report (2026-08).
    _require_db_populated()   # empty-DB guard (all tasks rejected except one-click refresh)
    if _task_running():
        raise HTTPException(409, "已有批量分析任务在运行")
    res = pipeline.analyze_latest(run_ai=True, ai_client=llm,
                                  build_context=build_context,
                                  lang=(body.lang if body else "zh-CN"))
    return res


@app.post("/api/analyze/all")
def api_analyze_all(body: AnalyzeBody | None = None, limit: int = Query(0)):
    _require_db_populated()   # empty-DB guard (all tasks rejected except one-click refresh)
    _require_replay_dir()   # reject directly when path is unavailable (frontend already guards, backend fallback)
    _start_task("batch", _run_batch, (limit, True, body.lang if body else "zh-CN"))
    return {"status": "started"}


@app.post("/api/analyze/by-path")
def api_analyze_by_path(path: str = Query(...)):
    return pipeline.process_file(path, run_ai=True, ai_client=llm,
                                 build_context=build_context)


@app.post("/api/analyze/{replay_id}")
def api_reanalyze(replay_id: str):
    row = repo.get_replay(replay_id)
    if not row:
        raise HTTPException(404, "replay 不存在")
    if not row.get("file_path") or not pathlib.Path(row["file_path"]).exists():
        raise HTTPException(410, "原始 .bsor 文件已不存在，无法重新分析")
    # force re-analysis (bypass content dedup)
    res = pipeline.process_file(row["file_path"], run_ai=True, ai_client=llm,
                                build_context=build_context, force=True)
    return res


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
        counts: dict = {"parsed": 0, "duplicate": 0, "error": 0,
                        "unsupported": 0, "total": len(results)}
        for r in results:
            k = r.get("status", "error")
            counts[k] = counts.get(k, 0) + 1
        # take the config path directly (previously pipeline.scan() ran a full directory glob +
        # table-wide known_file_states(), just to echo a single string, P1-3.5)
        counts["replay_dir"] = cfg.replay_dir
        _set_task("ingest", running=False, current="",
                  results=[{"kind": "ingest", **counts}])
    except Exception as e:  # noqa: BLE001
        _set_task("ingest", running=False, error=f"{e}\n{traceback.format_exc()}")


@app.post("/api/replays/{replay_id}/analyze")
def api_replay_lazy_analyze(replay_id: str):
    """Detail-page lazy load: full analysis for a pending replay (idempotent,
    instant return when already analyzed).

    No AI report (run_ai=False); AI reports generated on demand via /api/ai/analyze/{id}.
    """
    res = pipeline.analyze_ingested(replay_id, run_ai=False,
                                    ai_client=llm, build_context=build_context)
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
    mode=count: paginate by count (20 per page, flat list) - the Overview
    page's "by count" mode.
    flat=1 returns a flat list (for compare/history scenarios needing all data).
    """
    if flat:
        replays = repo.list_replays(limit=limit, map_hash=map_hash, days=days)
        enrichment.enrich_flat(replays)
        return replays
    if mode == "count":
        page_size = 20
        replays = repo.list_replays(limit=100000, map_hash=map_hash, days=days)
        total = len(replays)
        pages = max(1, math.ceil(total / page_size)) if total else 0
        page = max(1, min(page, pages)) if pages else 1
        chunk = replays[(page - 1) * page_size: page * page_size]
        enrichment.enrich_flat(chunk)
        return {"replays": chunk, "total": total, "page": page,
                "pages": pages, "mode": "count"}
    data = repo.list_replays_by_day(page=page, map_hash=map_hash, days=days)
    # attach beatmap_key + ranked stars + pp (services/enrichment.py, cached)
    enrichment.enrich(data.get("days", []))
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
        enrichment.enrich_flat(hist)
    # the current replay itself carries nps / stars / pp
    enrichment.enrich_flat([row])
    return row


@app.get("/api/replays/{replay_id}/metrics")
def api_replay_metrics(replay_id: str):
    return repo.get_metrics(replay_id)


@app.get("/api/replays/{replay_id}/timeline")
def api_replay_timeline(replay_id: str):
    """Chart data (note-anchored; the fixed time-window mode is retired, 2026):
    - notes: per-note cumulative acc/center curves (good cut, x=event time)
             + saber speed (±7 good-cut local mean, smoothed jumps)
             + local density (±5 note neighborhood + ±2 note rounding, natural
               valleys in map gaps - faithful to the data)
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
    return {"windows": repo.get_windows(replay_id),
            "events": repo.get_miss_bad_events(replay_id),
            "notes": notes,
            "note_range": repo.get_note_time_range(replay_id)}


@app.get("/api/replays/{replay_id}/series")
def api_replay_series(replay_id: str):
    """Time series for charts: hand speed/angular velocity + accuracy curve."""
    row = repo.get_replay(replay_id)
    if not row:
        raise HTTPException(404, "replay 不存在")
    series = repo.get_motion_series(replay_id) or {}
    return {"motion": series}


@app.get("/api/history")
def api_history(map_hash: Optional[str] = None, days: Optional[int] = None,
                limit: int = Query(200, le=2000)):
    replays = repo.list_replays(limit=limit, map_hash=map_hash, days=days)
    # attach beatmap_key / nps / stars / pp (the history list's highlight search needs the key)
    enrichment.enrich_flat(replays)
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


@app.post("/api/maps/rescan")
def api_maps_rescan():
    _require_db_populated()   # empty-DB guard (map scan: use "One-click Refresh")
    _require_maps_dir()   # reject directly when the maps dir is unavailable (frontend already guards, backend fallback)
    _start_task("map_scan", _run_map_scan)
    return {"status": "started"}


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


# ---------- ScoreSaber ----------
@app.get("/api/scoresaber")
def api_scoresaber():
    pid = _scoresaber_id()   # auto-resolved from BSOR (= Steam ID), config is only a fallback
    cached = repo.get_scoresaber(pid)
    if cached:
        return cached
    return refresh_scoresaber()


@app.post("/api/scoresaber/refresh")
def refresh_scoresaber():
    pid = _scoresaber_id()
    if not pid:
        raise HTTPException(400, "库中无 Replay 数据，无法解析玩家 ID")
    try:
        profile = scoresaber.fetch_profile(cfg, pid)
        scores = scoresaber.fetch_scores(cfg, pid, limit=100,
                                         sort="recent", max_pages=2)
    except scoresaber.ScoreSaberError as e:
        raise HTTPException(502, str(e))
    repo.save_scoresaber(pid, profile, scores)
    return {"fetched_at": datetime.now(timezone.utc).isoformat(),
            "profile": profile, "scores": scores}


@app.get("/api/scoresaber/validate")
def api_scoresaber_validate():
    """Cross-validation: locally parsed scores vs ScoreSaber recorded scores."""
    local = repo.list_replays(limit=500)
    result = scoresaber.cross_validate(cfg, _scoresaber_id(), local)
    return result


@app.post("/api/scoresaber/update-ranked")
def api_scoresaber_update_ranked():
    """Background fill of the (map_hash, difficulty) -> stars/pp cache (cloud data, local files untouched)."""
    _require_db_populated()   # empty-DB guard (stars sync: use "One-click Refresh")
    _require_maps_dir()   # sync rooted at local maps; reject if the maps dir is unavailable
    _start_task("ranked_update", _run_ranked_update)
    return {"status": "started"}


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
    """One-click refresh: trigger all 5 tasks in parallel (ingest / batch
    analysis / map scan / NPS / online stars).

    Incremental semantics (v1.4.1): only new/changed data is processed -
    ingest/batch dedup by sha256+mtime, map_scan reuses the DB by folder mtime,
    nps_update skips already-computed unchanged maps, ranked_update syncs only
    new uncached maps. Ingested data and stale cloud values are never
    recomputed or re-fetched.
    """
    _require_replay_dir()
    _require_maps_dir()
    # Reports are always generated; whether the LLM is called is decided by
    # the settings toggle ai.ai_report_enabled inside run_ai_report (2026-08).
    lang = (body.lang if body else "zh-CN")
    started = []
    for kind, fn, args in (("ingest", _run_ingest, (0,)),
                           ("batch", _run_batch, (0, True, lang)),
                           ("map_scan", _run_map_scan, ()),
                           ("nps_update", _run_nps_update, ()),
                           ("ranked_update", _run_ranked_update, (True,))):
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


@app.get("/api/settings")
def api_settings():
    """Return the current config view (without secrets)."""
    return config_svc.view().to_dict()


@app.get("/api/settings/schema")
def api_settings_schema():
    """Return the schema + current values (secrets masked) so the frontend can dynamically generate the settings UI."""
    from backend.config.schema import get_schema
    values = config_svc.get_all_values()
    return {"schema": get_schema(), "values": values}


@app.post("/api/settings/validate")
def api_settings_validate(body: SettingsBody | None = None):
    """Validate the game root directory and its derived paths.

    valid = root AND maps directories exist (core check, shown as the badge
    next to the title); results lists each path's details (root / Replay /
    maps / SongCore).
    """
    root = (body.instance_root if body else "").strip() or cfg.instance_root
    results = []
    for s in check_paths(root):
        results.append({
            "key": s.key, "label": s.label, "path": s.path,
            "exists": s.exists, "ok": s.ok, "note": s.note,
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
    no restart needed). When analysis parameters (analysis.*) change (v1.4.1):
    metrics were computed with the old parameters, so the analysis cache is
    cleared and replays reset to pending - the detail-page lazy analysis
    recomputes with the new parameters.
    """
    updates = body.values if body and body.values else {}
    if not updates:
        return {"saved": False, "error": "没有要保存的配置"}
    analysis_changed = any(k.startswith("analysis.") for k in updates)
    res = config_svc.save_values(updates)
    if res.get("saved"):
        reload_runtime_config()
        if analysis_changed:
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

# 3D replay viewer (ChroViewer port, phase 2): build output mounted at /chro/
_CHRO_DIST = FRONTEND_DIR / "chro" / "dist"
if _CHRO_DIST.exists():
    app.mount("/chro", NoCacheStaticFiles(directory=_CHRO_DIST, html=True), name="chro")


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
