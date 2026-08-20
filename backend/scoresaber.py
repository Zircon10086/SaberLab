"""ScoreSaber API 客户端（非官方公开 API，仅用于验证与展示）。

端点（实测可用）:
- GET https://scoresaber.com/api/player/{id}/full    -> 玩家资料
- GET https://scoresaber.com/api/player/{id}/scores?limit=N&sort=recent|top&page=N
"""
from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .config import Config

BASE = "https://scoresaber.com/api"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 SaberLab/1.0")

# ScoreSaber 难度名 -> Replay/BSOR 难度名
_DIFF_NORM = {
    "easy": "Easy", "normal": "Normal", "hard": "Hard",
    "expert": "Expert", "expert+": "ExpertPlus", "expertplus": "ExpertPlus",
}


def norm_difficulty(name) -> str:
    if not name:
        return ""
    return _DIFF_NORM.get(str(name).strip().lower(), str(name).strip())


class ScoreSaberError(Exception):
    """ScoreSaber 请求失败。status：HTTP 状态码（int），None = 网络/解析错误。

    区分二者是缓存正确性的关键（P0-2.4）：
    - status=404 → 谱面确实不在 ScoreSaber，可以安全写“未找到”缓存
    - status=None（超时/断网/5xx）→ 绝不能写“未找到”，否则缓存被投毒，
      断网一次整批谱面星级丢失，只能手动清缓存恢复
    """

    def __init__(self, msg: str, status: Optional[int] = None):
        super().__init__(msg)
        self.status = status


# 每线程一个持久 HTTPS 连接（http.client 显式复用 keep-alive）。
# 背景（实测）：ScoreSaber 对新 TCP 连接的首个请求有 ~43s 固定延迟，
# 同连接后续请求 ~0.2s。urllib.request 在 Python 3.12+ 已无 keep-alive
# 连接池（每次 urlopen 都新建连接），所以这里直接管理 http.client 连接：
# 首请求吃 43s 后，本线程所有后续请求都是毫秒级。
_conn_local = threading.local()
_REQUEST_TIMEOUT = 120.0  # socket 超时（首请求 43s 是常态，30s 会误杀）
# 429（限速）退避：连接复用时毫秒级连发会触发 ScoreSaber 限速
# （实测 8 并发下约 1/3 by-id 请求 429）。退避后重试，避免每次同步
# 都有一批固定失败且永不写缓存。
_429_RETRY_DELAYS = (1.0, 2.0, 4.0)


def _conn(cfg: Config) -> http.client.HTTPSConnection:
    cached = getattr(_conn_local, "conn", None)
    if cached is not None:
        return cached
    conn = http.client.HTTPSConnection("scoresaber.com",
                                       timeout=_REQUEST_TIMEOUT)
    if cfg.proxy:
        # 代理：CONNECT 隧道（保持原有代理支持）
        proxy = urllib.parse.urlsplit(cfg.proxy if "://" in cfg.proxy
                                      else f"http://{cfg.proxy}")
        conn.set_tunnel(proxy.hostname, proxy.port or 443)
    _conn_local.conn = conn
    return conn


def _drop_conn():
    conn = getattr(_conn_local, "conn", None)
    _conn_local.conn = None
    if conn is not None:
        try:
            conn.close()
        except OSError:
            pass


def _get(cfg: Config, url: str):
    parsed = urllib.parse.urlsplit(url)
    target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    retries = 0
    while True:
        conn = _conn(cfg)
        try:
            conn.request("GET", target, headers={"User-Agent": UA,
                                                 "Accept": "application/json"})
            resp = conn.getresponse()
            body = resp.read()
            if resp.status == 429 and retries < len(_429_RETRY_DELAYS):
                # 限速：退避后重试（连接本身可继续复用）
                time.sleep(_429_RETRY_DELAYS[retries])
                retries += 1
                continue
            if resp.status >= 400:
                raise ScoreSaberError(
                    f"ScoreSaber API HTTP {resp.status}: {url}",
                    status=resp.status)
            return json.loads(body.decode("utf-8"))
        except ScoreSaberError:
            raise
        except (http.client.HTTPException, OSError, ValueError) as e:
            # 连接失效/超时/解析失败：丢弃本线程连接，下次重建
            # （新连接首请求会再吃一次 43s，但只发生在真正断连时）
            _drop_conn()
            raise ScoreSaberError(f"ScoreSaber API 请求失败: {url}: {e}") from e


def fetch_profile(cfg: Config, player_id: str) -> dict:
    return _get(cfg, f"{BASE}/player/{player_id}/full")


def fetch_scores(cfg: Config, player_id: str, limit: int = 100,
                 sort: str = "recent", max_pages: int = 3) -> list[dict]:
    """拉取最近/最高分成绩列表（自动翻页到 limit）。"""
    out: list[dict] = []
    page = 1
    per_page = min(max(1, limit), 100)
    while len(out) < limit and page <= max_pages:
        q = urllib.parse.urlencode({"limit": per_page, "sort": sort, "page": page})
        data = _get(cfg, f"{BASE}/player/{player_id}/scores?{q}")
        batch = data.get("playerScores") or []
        if not batch:
            break
        for item in batch:
            sc = item.get("score") or {}
            lb = item.get("leaderboard") or {}
            diff = lb.get("difficulty") or {}
            # difficultyRaw 格式: "_Expert_SoloStandard" -> "Expert"
            diff_raw = diff.get("difficultyRaw") or ""
            diff_name = diff_raw.split("_")[1] if "_" in diff_raw else diff_raw
            out.append({
                "score_id": sc.get("id"),
                "time_set": sc.get("timeSet"),
                "score": sc.get("modifiedScore") or sc.get("baseScore"),
                "pp": sc.get("pp"),
                "weight": sc.get("weight"),
                "rank": sc.get("rank"),
                "bad_cuts": sc.get("badCuts"),
                "missed_notes": sc.get("missedNotes"),
                "max_combo": sc.get("maxCombo"),
                "full_combo": sc.get("fullCombo"),
                "modifiers": sc.get("modifiers"),
                "has_replay": sc.get("hasReplay"),
                "device_hmd": sc.get("deviceHmd"),
                "leaderboard_id": lb.get("id"),
                "song_hash": lb.get("songHash"),
                "song_name": lb.get("songName"),
                "song_author": lb.get("songAuthorName"),
                "level_author": lb.get("levelAuthorName"),
                "difficulty": diff_name,
                "difficulty_rank": diff.get("difficulty"),
                "stars": lb.get("stars"),
                "ranked": lb.get("ranked"),
                "max_pp": lb.get("maxPP"),
            })
        if len(batch) < per_page:
            break
        page += 1
    return out[:limit]


def cross_validate(cfg: Config, player_id: str,
                   local_replays: list[dict]) -> dict:
    """用 ScoreSaber 成绩验证本地解析结果。

    对每张 (song_hash, difficulty)：比较 ScoreSaber 分数 vs 本地 replay 分数。
    返回 {matched: [...], unmatched_local: n, score_diffs: [...]}
    """
    try:
        ss_scores = fetch_scores(cfg, player_id, limit=100, sort="recent")
    except ScoreSaberError as e:
        return {"error": str(e)}
    ss_index: dict[tuple, dict] = {}
    for s in ss_scores:
        if s.get("song_hash"):
            key = (s["song_hash"].upper(), norm_difficulty(s.get("difficulty")))
            ss_index.setdefault(key, s)
    matched, diffs = [], []
    for r in local_replays:
        key = ((r.get("map_hash") or "").upper(),
               norm_difficulty(r.get("difficulty")))
        ss = ss_index.get(key)
        if ss is None:
            continue
        row = {
            "song_name": r.get("song_name"),
            "difficulty": r.get("difficulty"),
            "local_score": r.get("score"),
            "scoresaber_score": ss.get("score"),
            "local_accuracy": r.get("accuracy"),
            "scoresaber_pp": ss.get("pp"),
            "stars": ss.get("stars"),
            "time_set": ss.get("time_set"),
        }
        if isinstance(row["local_score"], int) and isinstance(row["scoresaber_score"], int):
            row["score_diff"] = row["local_score"] - row["scoresaber_score"]
            diffs.append(row["score_diff"])
        matched.append(row)
    return {"matched": matched, "fetched_scores": len(ss_scores),
            "matched_count": len(matched)}


def fetch_map_difficulties(cfg: Config, map_hash: str) -> list[dict]:
    """GET /api/leaderboard/get-difficulties/{hash}

    按谱面 hash 获取该 Map 的全部难度列表（含 leaderboardId）。
    实测：该端点速度快、结果正确；字段不含 stars（需 by-id 补齐）。
    返回 [{"leaderboardId", "difficulty", "gameMode", "difficultyRaw"}, ...]
    """
    if not map_hash:
        return []
    data = _get(cfg, f"{BASE}/leaderboard/get-difficulties/{map_hash.upper()}")
    return data if isinstance(data, list) else []


def fetch_leaderboard_info(cfg: Config, leaderboard_id: int) -> Optional[dict]:
    """GET /api/leaderboard/by-id/{id}/info

    返回单个 leaderboard 的完整信息（stars/ranked/qualified/loved/maxPP）。
    """
    data = _get(cfg, f"{BASE}/leaderboard/by-id/{leaderboard_id}/info")
    if not isinstance(data, dict):
        return None
    diff = data.get("difficulty") or {}
    diff_raw = diff.get("difficultyRaw") or ""
    diff_name = diff_raw.split("_")[1] if "_" in diff_raw else ""
    return {
        "leaderboard_id": data.get("id"),
        "map_hash": (data.get("songHash") or "").upper(),
        "difficulty_rank": diff.get("difficulty"),
        "difficulty_name": diff_name,
        "game_mode": diff.get("gameMode") or "",
        "difficulty_raw": diff_raw,
        "song_name": data.get("songName"),
        "level_author": data.get("levelAuthorName"),
        "stars": data.get("stars"),
        "ranked": 1 if data.get("ranked") else 0,
        "qualified": 1 if data.get("qualified") else 0,
        "loved": 1 if data.get("loved") else 0,
        "max_pp": data.get("maxPP"),
        "plays": data.get("plays"),
    }


_DIFF_RANK_NAME = {1: "Easy", 3: "Normal", 5: "Hard", 7: "Expert",
                   9: "ExpertPlus", 11: "ExpertPlus"}


def diff_rank_to_name(rank) -> str:
    if rank is None:
        return ""
    return _DIFF_RANK_NAME.get(int(rank), f"Rank{rank}")


def sync_map_leaderboards(cfg: Config, repo, map_hash: str,
                          force: bool = False) -> dict:
    """以本地谱面为根，同步该谱面的全部 ScoreSaber leaderboard 元数据。

    流程（实测验证的正确路线）：
      1. get-difficulties/{hash} -> 全部难度的 leaderboardId（快）
      2. 对每个 leaderboardId：缓存命中且未过期则跳过；
         否则 by-id/{id}/info 获取 stars/ranked/maxPP（慢，~44s/请求）

    缓存到 scoresaber_leaderboards 表（leaderboard_id 主键）。
    返回 {total, cached, fetched, failed}
    """
    from datetime import datetime, timezone

    try:
        diffs = fetch_map_difficulties(cfg, map_hash)
    except ScoreSaberError as e:
        if e.status == 404:
            # 谱面确实不在 ScoreSaber（自定义未上传图）：合法的“未找到”
            return {"total": 0, "cached": 0, "fetched": 0, "failed": 0,
                    "map_hash": map_hash, "not_on_scoresaber": True}
        raise  # 网络/超时/5xx：交给上层计 failed，下次同步重试
    stats = {"total": len(diffs), "cached": 0, "fetched": 0, "failed": 0,
             "map_hash": map_hash}
    if not diffs:
        stats["not_on_scoresaber"] = True
        return stats

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    for d in diffs:
        lbid = d.get("leaderboardId")
        if not lbid:
            stats["failed"] += 1
            continue
        cached = repo.get_ss_leaderboard(lbid)
        if cached and not force and cached.get("last_synced"):
            # TTL 内直接命中缓存（星级变化缓慢，默认 30 天）
            stats["cached"] += 1
            continue
        try:
            info = fetch_leaderboard_info(cfg, lbid)
        except ScoreSaberError as e:
            if e.status == 404:
                # leaderboard 确实不存在：保存基础条目，避免反复查询
                info = None
            else:
                # 网络失败：不写缓存（下次同步重试），也不算“未找到”
                stats["failed"] += 1
                continue
        if info is None:
            # 无详情：至少保存 get-difficulties 给出的基础条目
            diff_raw = d.get("difficultyRaw") or ""
            rank = d.get("difficulty")
            repo.upsert_ss_leaderboard({
                "leaderboard_id": lbid,
                "map_hash": map_hash.upper(),
                "difficulty_rank": rank,
                "difficulty_name": diff_rank_to_name(rank),
                "game_mode": d.get("gameMode") or "",
                "difficulty_raw": diff_raw,
                "song_name": None, "level_author": None,
                "stars": None, "ranked": None, "qualified": None,
                "loved": None, "max_pp": None, "plays": None,
                "last_synced": now,
            })
            stats["failed"] += 1
            continue
        info["last_synced"] = now
        repo.upsert_ss_leaderboard(info)
        stats["fetched"] += 1
    return stats


def sync_maps_batch(cfg: Config, repo, map_hashes: list[str],
                    progress_cb=None, force: bool = False,
                    workers: int = 8) -> dict:
    """批量同步多个谱面的 leaderboard 元数据（后台任务用）。

    并发说明：ScoreSaber 对新 TCP 连接的首个请求有 ~43s 的固定延迟，
    同连接后续请求毫秒级。因此：
    1. 每个工作线程复用自己的 opener（keep-alive 连接池，见 _opener），
       首请求吃 43s 后本线程后续请求都是毫秒级；
    2. 8 个工作线程并行处理不同谱面，把总耗时从“串行 ×43s/请求”
       压到“请求数 ÷ 8 × ~0.2s + 43s”（全量 1000+ 谱面：数小时 → 分钟级）。

    失败重试：每轮结束后 failed 的谱面重新入队再同步（限速/瞬时错误恢复后
    自动补上）；单条累计失败 >=3 次放弃并记入 failed_songs（前端 toast 提示）。

    统计聚合与进度回调在锁内更新，保证线程安全。
    """
    total_stats = {"maps": len(map_hashes), "fetched": 0, "cached": 0,
                   "failed": 0, "network_failed": 0, "not_on_scoresaber": 0,
                   "failed_songs": []}
    if not map_hashes:
        return total_stats
    lock = threading.Lock()
    done = 0
    fail_count: dict[str, int] = {}
    pending = list(map_hashes)   # 当前轮队列（首轮 = 全部）
    total_hashes = len(map_hashes)

    def sync_one(mh: str):
        nonlocal done
        try:
            s = sync_map_leaderboards(cfg, repo, mh, force=force)
        except ScoreSaberError as e:
            s = {"failed": 1,
                 "network_failed": 1 if e.status is None else 0}
        with lock:
            for k in ("fetched", "cached", "failed", "network_failed"):
                total_stats[k] += s.get(k, 0)
            if s.get("not_on_scoresaber"):
                total_stats["not_on_scoresaber"] += 1
            done += 1
            if progress_cb:
                progress_cb(done, total_hashes, mh[:12])
        if s.get("failed"):
            with lock:
                fail_count[mh] = fail_count.get(mh, 0) + 1
            return mh   # 需要重试
        return None

    while pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(sync_one, pending))
        retry = []
        for mh, res in zip(pending, results):
            if res is None:
                continue
            if fail_count[res] < 3:
                retry.append(res)          # 重新入队（下一轮）
            else:
                row = repo.get_map(res)    # 放弃：记录谱面名供前端提示
                name = (row or {}).get("song_name") or res[:16]
                total_stats["failed_songs"].append(name)
        pending = retry
    return total_stats


def build_ranked_index(cfg: Config, player_id: str) -> dict[tuple, dict]:
    """拉取玩家成绩，构建 (song_hash, difficulty) -> {stars, pp} 索引。

    用于批量填充 map_ranked_cache：一次请求覆盖最多 100 首。
    """
    idx: dict[tuple, dict] = {}
    try:
        scores = fetch_scores(cfg, player_id, limit=100, sort="top")
    except ScoreSaberError:
        return idx
    for s in scores:
        if s.get("song_hash"):
            key = (s["song_hash"].upper(), norm_difficulty(s.get("difficulty")))
            idx[key] = {"stars": s.get("stars"), "pp": s.get("pp")}
    return idx
