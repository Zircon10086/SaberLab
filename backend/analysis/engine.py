"""分析引擎编排：parse 结果 -> 全部指标 -> DB 落库格式。

原则（设计文档 §3.4 / Rule 7）：所有数字由本层确定性计算，
AI 层只做解释，不产生数据。
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..bsor.models import Replay
from ..db.repository import Repository
from .scoring import compute_score
from .accuracy import analyze_accuracy
from .windows import build_windows
from .motion import analyze_motion
from .fatigue import analyze_fatigue


def analyze_replay(replay: Replay, cfg, repo: Optional[Repository] = None,
                   save: bool = True, filename_exit: bool = False) -> dict:
    """对单个已解析 Replay 跑全部分析。返回结构化结果。

    filename_exit: BeatLeader 文件名中的 "-exit-" 标记（中途退出）。
    文件名标记是游戏侧的权威信息，优先级最高。
    """
    # 1) 官方口径总分 / accuracy / combo
    score_result = compute_score(replay)

    # 2) Accuracy / Pre-Center-Post / 左右手 / 网格
    acc = analyze_accuracy(replay)

    # 3) 时间窗口
    windows = build_windows(replay, cfg.window_seconds, cfg.window_step_seconds)

    # 4) 运动学
    motion = analyze_motion(replay)

    # 5) 疲劳
    fatigue = analyze_fatigue(replay, windows, motion, cfg.fatigue_edge_seconds)

    duration = float(replay.frames["time"][-1]) if replay.frame_count else 0.0
    fps_median = float(np.median(replay.frames["fps"])) if replay.frame_count else 0.0

    counts = acc["counts"]
    full_combo = counts["bad"] == 0 and counts["miss"] == 0

    # 6) 完成度判断
    # 优先级：文件名 exit（中途退出，权威）> modifiers 含 NF（Fail 后自动启用）> fail_time > 时长
    # 获取谱面 song_length（从 repo 查询）
    song_length = 0.0
    if repo is not None and replay.info.map_hash:
        map_info = repo.get_map(replay.info.map_hash)
        if map_info:
            song_length = float(map_info.get("song_length") or 0.0)

    mods = (replay.info.modifiers or "").upper()
    has_nf = "NF" in mods          # No Fail：能量耗尽后自动启用，即实际 Fail 过
    fail_time = float(replay.info.fail_time or 0.0)
    if filename_exit:
        # BeatLeader 文件名明确标记中途退出 -> 未完成
        completion_status = "incomplete"
    elif has_nf:
        # NF 标记 = 实际 Fail 过（Beat Saber 自动启用 No Fail 继续）-> 已完成但 Fail
        completion_status = "failed"
    elif fail_time > 0:
        completion_status = "failed"
    elif song_length > 0 and duration < song_length * 0.98:
        completion_status = "incomplete"
    else:
        completion_status = "completed"

    # NF（Fail）分数惩罚：Beat Saber 官方口径 Fail 后实际得分减半
    raw_score = int(replay.info.score or 0)
    effective_score = raw_score // 2 if has_nf else raw_score

    summary = {
        "score_recomputed": score_result.total_score,
        "score_effective": effective_score,
        "has_nf": has_nf,
        "accuracy": round(score_result.accuracy, 5),
        "max_combo": score_result.max_combo,
        "full_combo": full_combo,
        "duration": round(duration, 2),
        "fps_median": fps_median,
        "frame_count": replay.frame_count,
        "note_count": len(replay.notes),
        "completion_status": completion_status,
        "song_length": song_length,
        "filename_exit": filename_exit,
        **{f"{k}_count": v for k, v in counts.items()},
    }

    result = {
        "summary": summary,
        "score_graph": score_result.score_graph,
        "accuracy": acc,
        "windows": windows,
        "motion": motion,
        "fatigue": fatigue,
    }

    if save and repo is not None:
        _persist(replay, result, repo)
    return result


def _persist(replay: Replay, result: dict, repo: Repository) -> None:
    rid = replay.file_sha256
    # notes 表
    repo.insert_notes(rid, result["accuracy"]["note_rows"])

    # metrics 表
    rows: list[tuple[str, str, float, str]] = []
    s = result["summary"]
    rows.append(("overall", "score", float(replay.info.score), ""))
    rows.append(("overall", "score_recomputed", float(s["score_recomputed"]), ""))
    rows.append(("overall", "accuracy", s["accuracy"], ""))
    rows.append(("overall", "max_combo", float(s["max_combo"]), ""))
    rows.append(("overall", "full_combo", 1.0 if s["full_combo"] else 0.0, ""))
    rows.append(("overall", "duration", s["duration"], ""))
    rows.append(("overall", "good_count", float(s["good_count"]), ""))
    rows.append(("overall", "bad_count", float(s["bad_count"]), ""))
    rows.append(("overall", "miss_count", float(s["miss_count"]), ""))
    rows.append(("overall", "bomb_count", float(s["bomb_count"]), ""))

    import json as _json
    for hand, hs in result["accuracy"]["hands"].items():
        for name in ("pre_score_avg", "center_score_avg", "post_score_avg",
                     "cut_distance_cm_avg", "saber_speed_avg", "time_dev_avg_ms",
                     "time_dev_abs_avg_ms", "time_dev_std_ms", "late_ratio",
                     "good", "bad", "miss"):
            v = hs.get(name)
            if isinstance(v, (int, float)):
                rows.append((hand, name, float(v), ""))
        rows.append((hand, "grid_acc", float(sum(g for g in hs["grid_acc"] if g)),
                     _json.dumps(hs["grid_acc"])))

    rev = result["motion"].get("reversal", {})
    for hand in ("left", "right"):
        r = rev.get(hand) or {}
        for name in ("fast_ratio", "fast_pairs", "single_hand_reversal_score",
                     "hit_interval_avg_ms", "fast_saber_speed_avg",
                     "fast_fail_rate", "fast_fail_concentration",
                     "speed_retention"):
            if isinstance(r.get(name), (int, float)):
                rows.append((hand, name, float(r[name]), ""))
        eco = (result["motion"].get("economy") or {}).get(hand) or {}
        if eco.get("economy_avg") is not None:
            rows.append((hand, "path_economy", float(eco["economy_avg"]), ""))
        mhand = result["motion"].get(hand) or {}
        for name in ("path_length_m", "speed_avg_mps", "speed_p95_mps",
                     "speed_peak_mps", "angular_velocity_avg_degps",
                     "angular_velocity_p95_degps", "angular_velocity_peak_degps",
                     "angular_velocity_std_degps"):
            if isinstance(mhand.get(name), (int, float)):
                rows.append((hand, name, float(mhand[name]), ""))

    if result["fatigue"].get("available"):
        for k, v in result["fatigue"]["deltas"].items():
            if isinstance(v, (int, float)):
                rows.append(("fatigue", f"delta_{k}", float(v), ""))
        for k, v in result["fatigue"]["slopes"].items():
            if isinstance(v, (int, float)):
                rows.append(("fatigue", k, float(v), ""))

    repo.save_metrics(rid, rows)

    # windows 表
    repo.save_windows(rid, [
        {"window_idx": w["window_idx"], "t_start": w["t_start"],
         "t_end": w["t_end"], "metrics": w["metrics"]} for w in result["windows"]
    ])

    # 运动序列（图表）
    if result["motion"].get("series"):
        repo.save_motion_series(rid, result["motion"]["series"])
