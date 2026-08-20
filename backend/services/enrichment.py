"""Replay 列表富化：附加 beatmap_key / stars / pp / nps（带进程内缓存）。

从 main.py 的 _enrich_replays 提取（架构审查 P1-3.1）。原实现每次
/api/replays、/api/replays/{id}、/api/history 请求都：
  1. 全量加载 maps 表（list_maps(limit=100000)）
  2. 全量加载 scoresaber_leaderboards
  3. 全量加载 map_ranked_cache
  4. 对每条 replay 重复 json.loads(同一谱面的 nps_json)

本服务把三张表构建为一份进程内快照（nps_json 只解析一次），
由数据变更方写穿失效（invalidate）。失效时机由 main.py 在
任务结束（_set_task(running=False)）与同步端点（scoresaber refresh、
清缓存）处触发。

线程模型：快照构建/读取无锁——最坏情形是并发下重复构建一次，
读到的数据与“无缓存时直接查库”一样新旧，无正确性影响。
"""
from __future__ import annotations

import json


def _lb_better(a: dict, b: dict) -> bool:
    """a 是否比 b 更优（ranked 优先 > SoloStandard 优先 > 有星级优先）。"""

    def score(lb):
        s = 0
        if lb.get("ranked"):
            s += 100
        if (lb.get("game_mode") or "") == "SoloStandard":
            s += 10
        if (lb.get("stars") or 0) > 0:
            s += 1
        return s

    return score(a) > score(b)


class EnrichmentService:
    """map 元数据快照 + replay 富化。

    snapshot 结构：
      key_map: {map_hash: {beatmap_key, nps: dict}}
      lb_map:  {(map_hash, difficulty_name): leaderboard row}
      rc:      {(map_hash, difficulty): ranked cache row}
    """

    def __init__(self, repo):
        self._repo = repo
        self._snapshot: tuple[dict, dict, dict] | None = None

    def invalidate(self) -> None:
        """数据变更后调用（rescan / ranked 同步 / NPS 更新 / 清缓存）。"""
        self._snapshot = None

    def _ensure_snapshot(self) -> tuple[dict, dict, dict]:
        if self._snapshot is not None:
            return self._snapshot
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
        for lb in self._repo.list_ss_leaderboards():
            key = (lb["map_hash"], lb["difficulty_name"] or "")
            cur = lb_map.get(key)
            if cur is None or _lb_better(lb, cur):
                lb_map[key] = lb
        rc: dict[tuple, dict] = {}
        for r in self._repo.list_ranked_cache():
            rc[(r["map_hash"], r["difficulty"])] = r
        self._snapshot = (key_map, lb_map, rc)
        return self._snapshot

    def enrich(self, days: list[dict]) -> None:
        """为按天分组的 replay 列表附加 beatmap_key / stars / pp / nps。"""
        if not days:
            return
        key_map, lb_map, rc = self._ensure_snapshot()
        for day in days:
            for r in day.get("replays", []):
                mh = (r.get("map_hash") or "").upper()
                meta = key_map.get(mh, {})
                r["beatmap_key"] = meta.get("beatmap_key", "")
                # NPS：方块密度（按 mode|difficulty 匹配难度文件）
                nps_map = meta.get("nps") or {}
                diff = r.get("difficulty") or ""
                mode = r.get("mode") or ""
                r["nps"] = nps_map.get(f"{mode}|{diff}") or nps_map.get(f"Standard|{diff}")
                # 星级：scoresaber_leaderboards（谱面属性，与玩家成绩无关）
                lb = lb_map.get((mh, diff))
                r["stars"] = lb.get("stars") if lb else None
                r["ranked"] = bool(lb.get("ranked")) if lb else None
                # ---- 0.00 星兜底：stars 为 0/None = 未认证（unranked）----
                # ScoreSaber 对 unranked leaderboard 写入 stars=0/ranked=0，
                # 展示层统一按"无星级"处理（列表页 "-"，详情页 UNRANKED），
                # 避免出现"0.00★"误导
                if r.get("stars") in (None, 0, 0.0):
                    r["stars"] = None
                    r["ranked"] = False
                # pp：玩家成绩索引（个人游玩记录）
                c = rc.get((mh, diff))
                pp = c.get("pp") if c else None
                # ---- pp 兜底策略 ----
                # 1. 中途退出的游玩（incomplete）不可能产生 pp
                # 2. 0 星或无星级 = 谱面未通过认证，无 pp 产出
                if r.get("completion_status") == "incomplete":
                    pp = None
                stars = r.get("stars")
                if stars in (None, 0, 0.0):
                    pp = None
                r["pp"] = pp

    def enrich_flat(self, replays: list[dict]) -> None:
        """为扁平 replay 列表（不分天）附加同样的富化字段。"""
        if replays:
            self.enrich([{"replays": replays}])
