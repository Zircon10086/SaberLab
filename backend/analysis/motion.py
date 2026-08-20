"""Motion Analyzer（设计文档 §11/§12）。

重要约束（§11.2）：BSOR 记录的是 controller pose，不是手腕关节。
本模块输出 controller angular velocity / hand motion proxy，
绝不将其命名为“腕关节角速度”。

数据源：
- frames（120Hz pose 序列）：手位置速度、路径长度、controller 角速度
- note cut info：saber 方向、切割点 → 单手连续换向分析
"""
from __future__ import annotations

import math

import numpy as np

from ..bsor.models import Replay, GOOD, BAD, MISS

# pose 布局：head=pos[0:3],rot[3:7]; left=pos[7:10],rot[10:14]; right=pos[14:17],rot[17:21]
LEFT_POS = slice(7, 10)
LEFT_ROT = slice(10, 14)
RIGHT_POS = slice(14, 17)
RIGHT_ROT = slice(17, 21)
HEAD_POS = slice(0, 3)


def _quat_angles(q: np.ndarray) -> np.ndarray:
    """相邻四元数之间的旋转角（弧度）。q: (N,4)"""
    if len(q) < 2:
        return np.zeros(0, dtype=np.float64)
    a = q[:-1]
    b = q[1:]
    dot = np.abs(np.sum(a * b, axis=1))
    dot = np.clip(dot, 0.0, 1.0)
    return 2.0 * np.arccos(dot)


def analyze_motion(replay: Replay, max_series_points: int = 600,
                   warmup_sec: float = 1.0, max_speed: float = 15.0) -> dict:
    """对单个 Replay 做手部运动学分析。

    数据清洗（§11.2 工程约束）：
    - warmup_sec：丢弃开头 N 秒。BSOR 首帧 pose 是设备的初始默认位置
      （不是真实手位），首帧→真实位置的跳变会产生几十~上百 m/s 的假速度；
      同时开头若干帧时间戳重复（都为 0），进一步放大该假值。
    - max_speed：物理上限。速度超过该值（m/s）的帧视为异常/追踪丢失，
      统计时排除，序列中插值平滑，避免污染均值/P95/峰值与图表。
    """
    result: dict = {"available": replay.frame_count > 1}
    if replay.frame_count < 2:
        return result

    frames = replay.frames
    t = frames["time"].astype(np.float64)
    pose = frames["pose"].astype(np.float64)

    # ---- 预热裁切：丢弃开头 warmup 秒（含首帧初始化跳变）----
    keep = t >= warmup_sec
    if not np.any(keep):
        # 兜底：至少丢弃首帧（它必然是初始化垃圾帧）
        keep = np.arange(len(t)) >= 1
    t = t[keep]
    pose = pose[keep]
    if len(t) < 2:
        return {"available": False, "reason": "warmup 裁切后帧数不足"}

    dt = np.diff(t)
    # 重复/异常时间戳保护
    median_dt = float(np.median(dt[dt > 0])) if np.any(dt > 0) else 1 / 120
    dt_safe = np.where(dt > 1e-6, dt, median_dt)

    series = {}
    for name, ps, rs in (("left", LEFT_POS, LEFT_ROT),
                          ("right", RIGHT_POS, RIGHT_ROT)):
        pos = pose[:, ps]
        rot = pose[:, rs]
        dist = np.linalg.norm(np.diff(pos, axis=0), axis=1)
        speed = dist / dt_safe                      # m/s
        ang = _quat_angles(rot) / dt_safe           # rad/s

        # ---- 物理上限过滤：异常帧统计排除 + 序列插值平滑 ----
        valid = speed <= max_speed
        speed_clean = speed
        if not np.all(valid):
            good = np.where(valid)[0]
            if len(good) >= 2:
                speed_clean = np.interp(np.arange(len(speed)), good, speed[good])
            else:
                speed_clean = np.full_like(speed, float(np.median(speed[valid])))
        stat = speed[valid] if np.any(valid) else speed_clean
        ang_stat = ang[valid] if np.any(valid) else ang
        result[name] = {
            "warmup_sec": warmup_sec,
            "trimmed_frames": int(len(speed) - valid.sum()),
            "path_length_m": round(float(dist.sum()), 2),
            "speed_avg_mps": round(float(stat.mean()), 3),
            "speed_p95_mps": round(float(np.percentile(stat, 95)), 3),
            "speed_peak_mps": round(float(stat.max()), 3),
            "angular_velocity_avg_radps": round(float(ang_stat.mean()), 3),
            "angular_velocity_avg_degps": round(float(np.degrees(ang_stat.mean())), 1),
            "angular_velocity_p95_degps": round(float(np.degrees(np.percentile(ang_stat, 95))), 1),
            "angular_velocity_peak_degps": round(float(np.degrees(ang_stat.max())), 1),
            "angular_velocity_std_degps": round(float(np.degrees(ang_stat.std())), 1),
        }
        series[name] = {"speed": speed_clean, "ang_deg": np.degrees(ang)}

    # 头部位移（身体运动代理）
    head = pose[:, HEAD_POS]
    head_dist = np.linalg.norm(np.diff(head, axis=0), axis=1)
    head_speed = head_dist / dt_safe
    head_valid = head_speed <= max_speed
    result["head"] = {
        "path_length_m": round(float(head_dist.sum()), 2),
        "speed_avg_mps": round(float(head_speed[head_valid].mean()
                                    if np.any(head_valid) else 0.0), 3),
    }

    # ---- 单手连续换向分析（§12）----
    result["reversal"] = _reversal_analysis(replay)

    # ---- 每刀路径效率（直线距离 / 实际路径）----
    result["economy"] = _path_economy(replay, t, pose, dt_safe)

    # ---- 降采样序列（前端图表）----
    n = len(t) - 1
    if n > max_series_points:
        idx = np.linspace(0, n - 1, max_series_points).astype(int)
    else:
        idx = np.arange(n)
    ts = t[:-1] if len(t) > 1 else t
    result["series"] = {
        "t": [round(float(x), 3) for x in ts[idx]],
        "left_speed": [round(float(x), 3) for x in series["left"]["speed"][idx]],
        "right_speed": [round(float(x), 3) for x in series["right"]["speed"][idx]],
        "left_ang_deg": [round(float(x), 1) for x in series["left"]["ang_deg"][idx]],
        "right_ang_deg": [round(float(x), 1) for x in series["right"]["ang_deg"][idx]],
    }
    return result


def _vec_angle_deg(a: tuple, b: tuple) -> float:
    ax, ay, az = a
    bx, by, bz = b
    la = math.sqrt(ax * ax + ay * ay + az * az)
    lb = math.sqrt(bx * bx + by * by + bz * bz)
    if la < 1e-9 or lb < 1e-9:
        return 0.0
    dot = max(-1.0, min(1.0, (ax * bx + ay * by + az * bz) / (la * lb)))
    return math.degrees(math.acos(dot))


def _hand_of(note) -> str | None:
    """该 note 归属哪只手：good/bad 用实际 saberType，miss 用颜色。"""
    if note.cut is not None:
        return note.cut.saber
    return note.params.saber


def _reversal_analysis(replay: Replay, fast_dt: float = 0.35) -> dict:
    """单手高速连续切割（stream/换向）能力分析 —— 设计文档 §12。

    不用“相邻刀方向夹角”当换向信号（连续切割方向本来就不同，无区分度）。
    改为以“同手相邻 note 间隔 < fast_dt 秒”定义【高速段】，回答：
      - 玩家在多少比例的时间里处于该手的高速连续段？
      - 高速段内的失误（bad/miss）是否显著集中？——“跟不上”的直接证据
      - 高速段内刀速是否保持得住？（高速段刀速 / 低速段刀速）
    全部来自 Replay 实际数据；single_hand_reversal_score 为内部指标。
    """
    out = {}
    notes = sorted(replay.notes, key=lambda n: n.event_time)

    for hand in ("left", "right"):
        hn = [n for n in notes if _hand_of(n) == hand]
        intervals = []
        fast_notes = 0          # 落在高速段内的 note 数
        fast_fails = 0          # 高速段内的 bad/miss
        fast_speeds = []        # 高速段内 good cut 的刀速
        slow_speeds = []        # 非高速段 good cut 的刀速
        fails_total = 0
        for prev, cur in zip(hn, hn[1:]):
            dt = cur.event_time - prev.event_time
            if dt <= 0 or dt > 2.0:
                continue
            intervals.append(dt)
            in_fast = dt < fast_dt
            if in_fast:
                fast_notes += 1
            is_fail = cur.event_type in (BAD, MISS)
            if is_fail:
                fails_total += 1
                if in_fast:
                    fast_fails += 1
            elif cur.event_type == GOOD and cur.cut is not None:
                (fast_speeds if in_fast else slow_speeds).append(cur.cut.saber_speed)

        if not intervals:
            out[hand] = {"pairs": 0}
            continue

        n_pairs = len(intervals)
        fast_ratio = fast_notes / n_pairs
        fast_fail_rate = (fast_fails / fast_notes) if fast_notes else 0.0
        overall_fail_rate = fails_total / n_pairs
        fs = sum(fast_speeds) / len(fast_speeds) if fast_speeds else 0.0
        ss = sum(slow_speeds) / len(slow_speeds) if slow_speeds else 0.0
        speed_retention = (fs / ss) if ss > 1e-6 else None
        avg_dt = sum(intervals) / n_pairs

        out[hand] = {
            "pairs": n_pairs,
            "hit_interval_avg_ms": round(avg_dt * 1000, 1),
            "hit_interval_min_ms": round(min(intervals) * 1000, 1),
            "fast_pairs": fast_notes,
            "fast_ratio": round(fast_ratio, 4),
            "fast_fail_rate": round(fast_fail_rate, 4),
            "overall_fail_rate": round(overall_fail_rate, 4),
            # 高速段失误集中度：>1 表示失误偏向高速段（跟不上的信号）
            "fast_fail_concentration": (round(fast_fail_rate / overall_fail_rate, 3)
                                         if overall_fail_rate > 1e-9 else None),
            "fast_saber_speed_avg": round(fs, 2),
            "slow_saber_speed_avg": round(ss, 2),
            "speed_retention": (round(speed_retention, 3)
                                 if speed_retention is not None else None),
            # 内部指标：高速段占比 × 高速段刀速保持率（越高=高速连续能力越强）
            "single_hand_reversal_score": (round(fast_ratio * (speed_retention or 0.0) * 100, 2)
                                            if speed_retention is not None else None),
        }
    return out



def _path_economy(replay: Replay, t: np.ndarray, pose: np.ndarray,
                  dt_safe: np.ndarray) -> dict:
    """相邻同手切割之间：直线路径 vs 实际手部路径 → 运动经济性（≤1）。

    用每只手的累积路径长度数组做 O(1) 区间查询，避免逐对切片。
    """
    out = {}
    good = [n for n in replay.notes if n.event_type == GOOD and n.cut is not None]
    good.sort(key=lambda n: n.event_time)

    # 每手累积路径长度 cum[i] = 从帧0到帧i 的路径长
    cum_by_hand = {}
    for hand, ps in (("left", LEFT_POS), ("right", RIGHT_POS)):
        pos = pose[:, ps]
        seg_len = np.linalg.norm(np.diff(pos, axis=0), axis=1)
        cum_by_hand[hand] = np.concatenate([[0.0], np.cumsum(seg_len)])

    for hand, st in (("left", 0), ("right", 1)):
        cum = cum_by_hand[hand]
        hc = [n for n in good if n.cut.saber_type == st]
        ratios = []
        for a, b in zip(hc, hc[1:]):
            straight = math.dist(a.cut.cut_point, b.cut.cut_point)
            if straight < 0.02:
                continue
            i0 = int(np.searchsorted(t, a.event_time))
            i1 = int(np.searchsorted(t, b.event_time))
            i0 = min(max(i0, 0), len(cum) - 1)
            i1 = min(max(i1, 0), len(cum) - 1)
            if i1 - i0 < 1:
                continue
            actual = float(cum[i1] - cum[i0])
            if actual > 1e-6:
                ratios.append(min(1.0, straight / actual))
        out[hand] = {
            "samples": len(ratios),
            "economy_avg": round(sum(ratios) / len(ratios), 4) if ratios else None,
        }
    return out
