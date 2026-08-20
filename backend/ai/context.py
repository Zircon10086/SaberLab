"""AI Context Builder（设计文档 §15.1）。

AI 不算原始指标；这里把引擎算好的结构化结果 + 历史同谱成绩 + 研究假设
打包成紧凑 JSON。
"""
from __future__ import annotations

from ..config import Config
from ..db.repository import Repository

RESEARCH_HYPOTHESES = (
    "H1: Center 是主要 Accuracy 技术瓶颈；"
    "H2: 高难度下 Center 会退化（低压 vs 高压对比）；"
    "H3: 局部疲劳首先表现为连续换向失败和 Miss，而非立即 Center 下降；"
    "H4: Saber Profile 显著影响运动经济性；"
    "H5: 训练有效性 = 跨时间、跨难度保持动作质量，而非单次 PP。"
)


def build_context(repo: Repository, cfg: Config, replay_id: str,
                  include_windows: int = 8) -> dict:
    replay = repo.get_replay(replay_id)
    if replay is None:
        raise KeyError(f"replay 不存在: {replay_id}")
    metrics = repo.get_metrics(replay_id)
    windows = repo.get_windows(replay_id)
    motion_series = repo.get_motion_series(replay_id)

    # 历史同谱（同难度）对比
    history = repo.previous_attempts_on_map(
        replay["map_hash"], replay["difficulty"], replay["timestamp"], limit=5)

    # 窗口序列压缩：取前中后各若干 + 关键量
    win_summary = []
    for w in windows:
        m = w["metrics"]
        win_summary.append({
            "t": [w["t_start"], w["t_end"]],
            "acc": m.get("accuracy_local"),
            "center": m.get("center_avg"),
            "miss_rate": m.get("miss_rate"),
            "bad_rate": m.get("bad_rate"),
            "speed": m.get("saber_speed_avg"),
            "density": m.get("note_density"),
            "imbalance": m.get("lr_imbalance"),
        })
    if len(win_summary) > include_windows:
        head = win_summary[: include_windows // 2]
        tail = win_summary[-(include_windows // 2):]
        win_summary = head + [{"note": "...中间窗口省略..."}] + tail

    ctx = {
        "meta": {
            "analysis_scope": "本地 Replay 确定性分析结果，AI 仅解释",
            "units": {"center_score": "0-15 越高越好", "pre_score": "0-70",
                      "post_score": "0-30", "cut_distance_cm": "越小越好",
                      "time_dev_ms": "切刀时间偏差"},
            "fatigue_note": "疲劳相关输出均为运动学推断，不是医学诊断",
        },
        "replay": {
            "song_name": replay["song_name"],
            "difficulty": replay["difficulty"],
            "mode": replay["mode"],
            "modifiers": replay["modifiers"],
            "score": replay["score"],
            "score_recomputed": replay["score_recomputed"],
            "accuracy": replay["accuracy"],
            "duration": replay["duration"],
            "won": bool(replay["won"]),
            "good": replay["good_count"], "bad": replay["bad_count"],
            "miss": replay["miss_count"], "bomb": replay["bomb_count"],
            "max_combo": replay["max_combo"],
            "full_combo": bool(replay["full_combo"]),
            "hmd": replay["hmd"], "controller": replay["controller"],
            "profile_id": replay["profile_id"],
        },
        "hands": {
            "left": metrics.get("left", {}),
            "right": metrics.get("right", {}),
        },
        "overall_metrics": metrics.get("overall", {}),
        "fatigue": metrics.get("fatigue", {}),
        "windows_timeline": win_summary,
        "history_same_map": history,
        "research_hypotheses": RESEARCH_HYPOTHESES,
    }
    return ctx
