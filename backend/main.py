"""SaberLab FastAPI 入口。

启动:  .venv\\Scripts\\python.exe backend\\main.py
面板:  http://127.0.0.1:8787
"""
from __future__ import annotations

import json
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
    """配置热更新（设置页保存后调用）：重载 config.yaml 并同步到
    resolver/pipeline，路径类配置即时生效，无需重启。"""
    global cfg
    cfg = load_config()
    resolver.update_paths(cfg.custom_levels_dir, cfg.songcore_cache)
    pipeline.update_config(cfg)


def _scoresaber_id() -> str:
    """ScoreSaber 玩家 ID 解析（重大发现：ScoreSaber ID = Steam ID，
    BSOR Replay 自带 17 位平台 ID）：
    优先尊重历史手动配置（config 兜底值）；未配置时取最近一次游戏记录的玩家
    （多玩家库中"当前玩家"的直觉来源，避免众数选中他人）。"""
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

app = FastAPI(title="SaberLab", version="1.0.0",
              description="Beat Saber 本地 Replay 分析实验室")

FRONTEND_DIR = PROJECT_ROOT / "frontend"


@app.middleware("http")
async def _access_log(request, call_next):
    """请求访问日志（定位卡死请求用）。"""
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
    """静态资源禁止缓存：前端改动即时生效。

    浏览器对无 Cache-Control 的静态文件做启发式缓存，曾导致旧 JS/CSS
    长期不更新（修改已部署但页面行为不变）。
    """

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-store"
        return resp

# ---------- 后台任务状态（多任务并行：一键导入同时跑 5 个任务） ----------
_task_lock = threading.Lock()
# kind -> task dict；每个 kind 独立槽位：同 kind 冲突 409，不同 kind 可并行
_tasks: dict[str, dict] = {}


def _set_task(kind: str, **kw):
    with _task_lock:
        t = _tasks.setdefault(kind, {"running": False, "kind": kind, "done": 0,
                                     "total": 0, "current": "", "results": [],
                                     "error": None})
        t.update(kw)
    # 任务结束（任意后台任务都可能改 maps/leaderboards/ranked 缓存表）
    # → 富化快照失效，下次请求重建（P1-3.1 缓存写穿失效点）
    if kw.get("running") is False:
        enrichment.invalidate()


def _task_running(kind: str | None = None) -> bool:
    """持锁读取任务运行状态：指定 kind 或任一任务（P0-2.3 无锁读修复）。"""
    with _task_lock:
        if kind:
            t = _tasks.get(kind)
            return bool(t and t["running"])
        return any(t["running"] for t in _tasks.values())


def _start_task(kind: str, target, args=()) -> None:
    """启动后台任务：同 kind 已在运行 → 409；不同 kind 并行不受影响。

    启动新任务前清理已完成的旧任务——`/api/status` 的 tasks 数组只保留
    "当前活跃组"：一键刷新(5 任务)完成后，再触发单任务(联网更新)时
    数组只有 1 个 → 前端 KPI 正确走"任务详情模式"而非完成数模式。
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
    """等同组 ingest 完成（一键刷新并行场景：新数据未入库前，
    batch/ranked_update 取 replays 会漏掉刚扫描的文件——
    清空数据后一键刷新曾导致星级同步 0 条、批量分析漏项）。"""
    while _task_running("ingest"):
        _set_task(kind, current="等待入库完成…")
        time.sleep(0.5)


def _run_batch(limit: int, run_ai: bool):
    try:
        _wait_ingest_done("batch")
        def cb(i, n, name):
            _set_task("batch", done=i, total=n, current=name)
        results = pipeline.analyze_all_new(progress_cb=cb, limit=limit,
                                           run_ai=run_ai, ai_client=llm,
                                           build_context=build_context)
        _set_task("batch", running=False, results=results, current="")
    except Exception as e:  # noqa: BLE001
        _set_task("batch", running=False, error=f"{e}\n{traceback.format_exc()}")


def _run_ranked_update():
    """后台同步：以本地谱面为根，批量拉取 ScoreSaber leaderboard 元数据。

    1. 收集有 replay 记录的谱面 hash（去重）
    2. get-difficulties + by-id info -> scoresaber_leaderboards（星级缓存，谱面属性）
    3. 玩家成绩索引 -> map_ranked_cache（pp 缓存，个人游玩记录）
    """
    try:
        _wait_ingest_done("ranked_update")   # 等入库完成再收集谱面 hash（清空后一键刷新场景）
        replays = repo.list_replays(limit=100000)
        hashes = sorted({(r.get("map_hash") or "").upper() for r in replays
                         if r.get("map_hash")})

        def cb(i, n, name):
            _set_task("ranked_update", done=i, total=n, current=f"谱面同步:{name}")
        stats = scoresaber.sync_maps_batch(cfg, repo, hashes, progress_cb=cb)
        stats["leaderboards_total"] = repo.count_ss_leaderboards()

        # pp 填充：玩家成绩索引（(hash, difficulty) -> pp），ID 从 BSOR 自动解析；
        # 库中无 Replay 且无配置兜底时跳过 pp（不触发 ScoreSaber 空 ID 404）
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
    """等 map_scan 完成（一键刷新并行场景：nps_update 依赖 maps 行，
    谱面未扫描入库前计算会空转——全新库下 nps_update 曾 0/0 完成，NPS 全缺失）。"""
    while _task_running("map_scan"):
        _set_task(kind, current="等待谱面扫描完成…")
        time.sleep(0.5)


def _run_nps_update():
    """后台计算全部谱面的 NPS（方块密度：notes 数 / 该难度实际时长）。

    遍历 CustomLevels 每个谱面文件夹，读取各难度 .dat 计算，
    结果写入 maps.nps_json（{"Standard|Expert": 4.2, ...}）。
    """
    try:
        _wait_map_scan_done("nps_update")
        from backend.maps.resolver import compute_level_nps, read_level_info
        maps = repo.list_maps(limit=100000)
        updated = 0
        skipped = 0

        def cb(i, n, name):
            _set_task("nps_update", done=i, total=n, current=f"NPS:{name}")
        for i, m in enumerate(maps):
            cb(i + 1, len(maps), (m.get("song_name") or "")[:24] or m["map_hash"][:12])
            folder = pathlib.Path(m.get("path") or "")
            if not folder.exists():
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


@app.post("/api/analyze/latest")
def api_analyze_latest(body: AnalyzeBody | None = None):
    run_ai = body.run_ai if body else True
    if _task_running():
        raise HTTPException(409, "已有批量分析任务在运行")
    res = pipeline.analyze_latest(run_ai=run_ai, ai_client=llm,
                                  build_context=build_context)
    return res


@app.post("/api/analyze/all")
def api_analyze_all(body: AnalyzeBody | None = None, limit: int = Query(0)):
    _require_replay_dir()   # 路径不可用时直接拒绝（前端已拦截，后端兜底）
    run_ai = body.run_ai if body else False
    _start_task("batch", _run_batch, (limit, run_ai))
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
    # 强制重新分析（绕过内容去重）
    res = pipeline.process_file(row["file_path"], run_ai=True, ai_client=llm,
                                build_context=build_context, force=True)
    return res


@app.post("/api/ingest/all")
def api_ingest_all(limit: int = Query(0)):
    """轻量入库全部新/变更 Replay（元数据快照，~5ms/文件，秒级完成）。

    分层分析策略：列表/搜索立即可见；完整分析延迟到详情页
    （POST /api/replays/{id}/analyze）或后台预计算（POST /api/analyze/all）。
    """
    _require_replay_dir()   # 路径不可用时直接拒绝（前端已拦截，后端兜底）
    _start_task("ingest", _run_ingest, (limit,))
    return {"status": "started"}


def _run_ingest(limit: int):
    """后台快速扫描入库（进度走多任务状态，与批量分析同一通道）。"""
    try:
        def cb(i, n, name):
            _set_task("ingest", done=i, total=n, current=name)
        results = pipeline.ingest_all_new(progress_cb=cb, limit=limit)
        counts: dict = {"parsed": 0, "duplicate": 0, "error": 0,
                        "unsupported": 0, "total": len(results)}
        for r in results:
            k = r.get("status", "error")
            counts[k] = counts.get(k, 0) + 1
        # 直接取配置路径（原先调 pipeline.scan() 触发完整目录 glob +
        # 全表 known_file_states()，只为回显一个字符串，P1-3.5）
        counts["replay_dir"] = cfg.replay_dir
        _set_task("ingest", running=False, current="",
                  results=[{"kind": "ingest", **counts}])
    except Exception as e:  # noqa: BLE001
        _set_task("ingest", running=False, error=f"{e}\n{traceback.format_exc()}")


@app.post("/api/replays/{replay_id}/analyze")
def api_replay_lazy_analyze(replay_id: str):
    """详情页懒加载：对 pending Replay 做完整分析（幂等，已分析则秒回）。

    不带 AI 报告（run_ai=False）；AI 报告由 /api/ai/analyze/{id} 按需生成。
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
                flat: int = Query(0), limit: int = Query(200, le=2000)):
    """按天分页：同一天完成的记录归为一页。
    flat=1 时返回平铺列表（供对比/历史等需要全量的场景）。
    """
    if flat:
        replays = repo.list_replays(limit=limit, map_hash=map_hash, days=days)
        enrichment.enrich_flat(replays)
        return replays
    data = repo.list_replays_by_day(page=page, map_hash=map_hash, days=days)
    # 附加 beatmap_key + ranked 星级 + pp（services/enrichment.py，带缓存）
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
    # 历史条目同样附带 beatmap_key / stars / pp / nps
    hist = row["history_same_map"]
    if hist:
        enrichment.enrich_flat(hist)
    # 当前 replay 本身附带 nps / stars / pp
    enrichment.enrich_flat([row])
    return row


@app.get("/api/replays/{replay_id}/metrics")
def api_replay_metrics(replay_id: str):
    return repo.get_metrics(replay_id)


@app.get("/api/replays/{replay_id}/timeline")
def api_replay_timeline(replay_id: str):
    return {"windows": repo.get_windows(replay_id)}


@app.get("/api/replays/{replay_id}/series")
def api_replay_series(replay_id: str):
    """图表用时间序列：手部速度/角速度 + accuracy 曲线。"""
    row = repo.get_replay(replay_id)
    if not row:
        raise HTTPException(404, "replay 不存在")
    series = repo.get_motion_series(replay_id) or {}
    return {"motion": series}


@app.get("/api/history")
def api_history(map_hash: Optional[str] = None, days: Optional[int] = None,
                limit: int = Query(200, le=2000)):
    replays = repo.list_replays(limit=limit, map_hash=map_hash, days=days)
    # 附加 beatmap_key / nps / stars / pp（历史列表高亮搜索需要 key）
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


# ---------- 3D 回放数据通道（ChroViewer 移植） ----------
@app.get("/api/replays/{replay_id}/raw")
def api_replay_raw(replay_id: str):
    """返回 .bsor 原始字节流（3D 回放前端解析用）。"""
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
    """谱面文件夹 zip 打包（3D 回放前端 fflate 解包加载）。

    含 info.dat / 各难度 .dat / 封面 / 音频（.egg 原样，阶段 3 处理后端解密）。
    使用 ZIP_STORED（不压缩）：谱面文件多为已压缩格式（音频/图片），
    DEFLATE 对高熵数据（加密 .egg）存在严重性能退化，可导致请求卡死。
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
    # 防御：超大谱面（>500MB）拒绝，避免拖垮服务器
    if total_size > 500 * 1024 * 1024:
        raise HTTPException(413, f"谱面文件夹过大 ({total_size // 1048576}MB)")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for f in sorted(files):
            zf.write(f, f.relative_to(folder).as_posix())
    # 注意：不能把 BytesIO 直接交给 StreamingResponse——同步文件对象按“行”迭代
    # （\n 分片），14MB 的 zip 会被拆成 12 万+ 个微小 chunk（每 chunk 一次线程池
    # 调度 + 一次 HTTP chunk），本地也要传 5 分钟以上，前端 60s 超时必然中断。
    # 整包已在内存中，直接一次性返回（带 Content-Length）。
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
    """后台扫描谱面库（进度走多任务状态，供前端进度条轮询）。"""
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
    _require_maps_dir()   # 谱面目录不可用时直接拒绝（前端已拦截，后端兜底）
    _start_task("map_scan", _run_map_scan)
    return {"status": "started"}
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
        # 加固：DB 行缺路径/谱面目录刚配置时，懒触发一次针对性扫描再取
        # （修复打包版首次 ingest 后封面全默认、重启才恢复的问题）
        try:
            resolver.ensure_map_path(map_hash)
            p = resolver.cover_path(map_hash)
        except Exception:  # noqa: BLE001
            p = None
    if p is None:
        # 谱面不存在/原文件被删除：返回默认封面，不 404
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
def api_ai_analyze(replay_id: str):
    if not repo.get_replay(replay_id):
        raise HTTPException(404, "replay 不存在")
    rep = run_ai_report(repo, cfg, replay_id, llm, None)
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
    pid = _scoresaber_id()   # 从 BSOR 自动解析（= Steam ID），config 仅兜底
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
    """交叉验证：本地解析分数 vs ScoreSaber 记录分数。"""
    local = repo.list_replays(limit=500)
    result = scoresaber.cross_validate(cfg, _scoresaber_id(), local)
    return result


@app.post("/api/scoresaber/update-ranked")
def api_scoresaber_update_ranked():
    """后台填充 (map_hash, difficulty) -> stars/pp 缓存（云端数据，本地不动）。"""
    _require_maps_dir()   # 以本地谱面为根同步，谱面目录不可用则拒绝
    _start_task("ranked_update", _run_ranked_update)
    return {"status": "started"}


@app.post("/api/maps/update-nps")
def api_maps_update_nps():
    """后台计算全部谱面的 NPS（方块密度）。"""
    _require_maps_dir()   # 谱面目录不可用时直接拒绝
    _start_task("nps_update", _run_nps_update)
    return {"status": "started"}


# ---------- 一键刷新 / 联网更新 ----------
@app.post("/api/refresh/all")
def api_refresh_all(body: AnalyzeBody | None = None):
    """一键刷新：并行触发全部 5 个任务（入库/批量分析/谱面扫描/NPS/联网星级）。

    本地分析（ingest/analyze/map_scan/nps）只处理新增/变更数据（sha256 去重 +
    pending 过滤，已有计算数据自动跳过）；ranked_update 为新数据一次性联网
    获取云端数值。前端 KPI 卡片按"任务完成数/总数"显示进度。
    """
    _require_replay_dir()
    _require_maps_dir()
    run_ai = body.run_ai if body else False
    started = []
    for kind, fn, args in (("ingest", _run_ingest, (0,)),
                           ("batch", _run_batch, (0, run_ai)),
                           ("map_scan", _run_map_scan, ()),
                           ("nps_update", _run_nps_update, ()),
                           ("ranked_update", _run_ranked_update, ())):
        try:
            _start_task(kind, fn, args)
            started.append(kind)
        except HTTPException:
            pass   # 同 kind 已在运行则跳过
    return {"status": "started", "tasks": started}


@app.post("/api/refresh/online")
def api_refresh_online():
    """联网重新更新数据：仅强制刷新云端数值（星级/pp），本地分析数据不动。

    适用场景：ScoreSaber 调整了谱面星级等云端数据（replay 本地硬指标无需重算）。
    """
    _require_maps_dir()
    _start_task("ranked_update", _run_ranked_update)
    return {"status": "started"}


@app.get("/api/network/check")
def api_network_check():
    """检测本机能否访问 ScoreSaber（互联网连通性）。

    前端在点「联网重新更新数据」前调用；离线时拦截并 toast 提示。
    HTTPError（404 等）说明服务器可达 → online；URLError/超时 → offline。
    """
    try:
        with urllib.request.urlopen(
                "https://scoresaber.com/api/", timeout=4):
            return {"online": True}
    except urllib.error.HTTPError:
        return {"online": True}    # 服务器可达（可能 404）
    except Exception:  # noqa: BLE001 网络不通/超时/DNS 失败
        return {"online": False}


# ---------- settings ----------
class SettingsBody(BaseModel):
    instance_root: str = ""


class SettingsSaveBody(BaseModel):
    values: dict = {}


@app.get("/api/settings")
def api_settings():
    """返回当前配置视图（不含 secret）。"""
    return config_svc.view().to_dict()


@app.get("/api/settings/schema")
def api_settings_schema():
    """返回 schema + 当前值（secret 脱敏），供前端动态生成设置 UI。"""
    from backend.config.schema import get_schema
    values = config_svc.get_all_values()
    return {"schema": get_schema(), "values": values}


@app.post("/api/settings/validate")
def api_settings_validate(body: SettingsBody | None = None):
    """校验游戏根目录及其派生路径。

    valid = 根目录存在 且 谱面目录存在（核心判定，供标题旁徽章显示）；
    results 为各路径明细（根/Replay/谱面/SongCore）。
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
    """弹出原生文件夹选择对话框（pywebview 窗口模式；浏览器模式返回
    unavailable，前端回退手动输入路径）。"""
    from backend.dialog import request_folder_dialog  # 延迟导入防循环依赖
    return request_folder_dialog()


@app.post("/api/settings")
def api_settings_save(body: SettingsSaveBody | None = None):
    """批量保存配置（原子写回 config.yaml / .env）。

    保存成功后热重载运行时配置（路径类即时生效，无需重启）。
    """
    updates = body.values if body and body.values else {}
    if not updates:
        return {"saved": False, "error": "没有要保存的配置"}
    res = config_svc.save_values(updates)
    if res.get("saved"):
        reload_runtime_config()
    return res


@app.post("/api/settings/root")
def api_settings_save_root(body: SettingsBody):
    """保存游戏根目录（兼容旧入口；保存成功后热重载，即时生效）。"""
    res = config_svc.save_instance_root(body.instance_root)
    if res.get("saved"):
        reload_runtime_config()
    return res


@app.post("/api/restart")
def api_restart():
    """应用内重启（设置页「重启 SABER LAB」按钮）。

    通过 dialog 桥调度 host.py 的重启回调：后台线程延迟拉起新进程后
    优雅退出当前进程（窗口模式/浏览器模式均适用；frozen 下重启 exe）。
    """
    from backend.dialog import request_restart  # 延迟导入防循环依赖
    return request_restart()


@app.post("/api/settings/clear-cache")
def api_settings_clear_cache():
    """清空 SABER LAB 分析缓存（需二次确认在前端完成）。"""
    result = repo.clear_analysis_cache()
    # 清掉了 map_ranked_cache / scoresaber_leaderboards → 富化快照失效
    enrichment.invalidate()
    return result


# ---------- 桌面集成（毛玻璃方案 C：壁纸推送，见 others/毛玻璃方案探索.md） ----------
@app.get("/api/desktop/backdrop")
def api_desktop_backdrop(hwnd: int = Query(0)):
    """窗口毛玻璃层所需数据：窗口/显示器几何 + 壁纸地址。

    hwnd 由宿主（backend/host.py）传入；无 hwnd 时返回不可用。
    仅回环使用（本机工具），浏览器模式下无人调用。
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
    """返回桌面壁纸原图（前端毛玻璃层背景用）。"""
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

# 3D 回放查看器（ChroViewer 移植，阶段 2）：构建产物挂载 /chro/
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
