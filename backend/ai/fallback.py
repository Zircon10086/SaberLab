"""确定性规则报告（LLM 未配置/调用失败时的兜底）。

所有阈值判断都基于引擎计算好的指标；输出明确标注为规则报告。
"""
from __future__ import annotations


def _v(d: dict, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    if isinstance(cur, dict):
        return cur.get("value", default)
    return cur


def rule_based_report(ctx: dict) -> str:
    r = ctx.get("replay", {})
    left = ctx.get("hands", {}).get("left", {})
    right = ctx.get("hands", {}).get("right", {})
    fat = ctx.get("fatigue", {})
    hist = ctx.get("history_same_map", [])

    lines = [
        "> ⚠️ 规则报告（未接入 LLM）。由确定性规则从指标生成，仅覆盖常见模式；"
        "接入 API key 后可获得完整 AI 分析。",
        "",
        "## 结论",
        f"《{r.get('song_name')}》[{r.get('difficulty')}] "
        f"总分 {r.get('score')}（重算 {r.get('score_recomputed')}），"
        f"accuracy {float(r.get('accuracy') or 0) * 100:.2f}%，"
        f"Good/Bad/Miss = {r.get('good')}/{r.get('bad')}/{r.get('miss')}，"
        f"{'Full Combo' if r.get('full_combo') else '非 FC'}。",
        "",
        "## 主要瓶颈",
    ]

    findings = []
    lc = _v(left, "center_score_avg", default=0) or 0
    rc = _v(right, "center_score_avg", default=0) or 0
    lp = _v(left, "pre_score_avg", default=0) or 0
    rp = _v(right, "pre_score_avg", default=0) or 0
    lpost = _v(left, "post_score_avg", default=0) or 0
    rpost = _v(right, "post_score_avg", default=0) or 0

    center_weak = min(lc, rc) < 8.0
    if center_weak:
        findings.append(
            f"- **Center（切准）是首要瓶颈**：左手 {lc:.2f}/15，右手 {rc:.2f}/15，"
            f"而 Pre（{lp:.1f}/70、{rp:.1f}/70）与 Post（{lpost:.1f}/30、{rpost:.1f}/30）"
            f"相对更高——符合 H1/H2 假设的形态：挥刀框架完整、切准退化。")
    asym_c = abs(lc - rc)
    if asym_c > 0.8:
        better = "右手" if rc > lc else "左手"
        findings.append(
            f"- **左右手 Center 不对称**：差 {asym_c:.2f} 分（{better}更好），"
            f"建议弱手单独做低速切准练习。")
    miss = r.get("miss") or 0
    bad = r.get("bad") or 0
    total_notes = (r.get("good") or 0) + miss + bad
    if total_notes and (miss + bad) / max(1, total_notes) > 0.03:
        findings.append(
            f"- **失误密度偏高**：Bad {bad} + Miss {miss}（{(miss + bad) / total_notes * 100:.1f}%），"
            f"需要定位时间窗（见时间序列图）。")

    # 高速连续切割（§12）
    for hand in ("left", "right"):
        h = ctx.get("hands", {}).get(hand, {})
        conc = _v(h, "fast_fail_concentration", default=None)
        fast_ratio = _v(h, "fast_ratio", default=None)
        ret = _v(h, "speed_retention", default=None)
        if conc is not None and fast_ratio is not None and conc > 1.5 and fast_ratio > 0.05:
            lines_hand = ("左" if hand == "left" else "右")
            findings.append(
                f"- **{lines_hand}手高速连续段跟不上**：失误集中度 {conc:.2f}（高速段失误率显著高于整体），"
                f"高速段占比 {fast_ratio:.0%}"
                + (f"，且高速段刀速仅为低速段的 {ret:.0%}" if ret is not None and ret < 0.9 else "")
                + "，与 H3 的单手换向问题一致。")
    if not findings:
        findings.append("- 未发现显著结构性瓶颈（规则阈值内）。")
    lines.extend(findings)

    lines += ["", "## 证据（关键指标）",
              f"- Pre/Center/Post 左: {lp:.2f} / {lc:.2f} / {lpost:.2f}",
              f"- Pre/Center/Post 右: {rp:.2f} / {rc:.2f} / {rpost:.2f}",
              f"- cut 距离均值 左 {_v(left, 'cut_distance_cm_avg', default=0):.2f} cm / "
              f"右 {_v(right, 'cut_distance_cm_avg', default=0):.2f} cm",
              f"- 刀速均值 左 {_v(left, 'saber_speed_avg', default=0):.1f} / "
              f"右 {_v(right, 'saber_speed_avg', default=0):.1f}",
              ]

    # 疲劳
    lines += ["", "## 后程变化（运动学推断，非医学诊断）"]
    if fat:
        d_acc = fat.get("delta_accuracy")
        d_center = fat.get("delta_center")
        d_miss = fat.get("delta_miss_rate")
        parts = []
        if d_acc is not None:
            parts.append(f"accuracy {d_acc:+.3f}")
        if d_center is not None:
            parts.append(f"Center {d_center:+.2f}")
        if d_miss is not None:
            parts.append(f"miss 率 {d_miss:+.3f}")
        if parts:
            trend = "、".join(parts)
            verdict = "后段出现与局部疲劳一致的运动学特征" if (
                (d_center is not None and d_center < -0.5) or
                (d_miss is not None and d_miss > 0.01)) else "后段未见系统性下降"
            lines.append(f"- 前段 vs 后段：{trend} → {verdict}。")
        else:
            lines.append("- 歌曲过短或窗口数据不足，无法对比。")
    else:
        lines.append("- 无疲劳数据。")

    # 历史
    lines += ["", "## 历史同谱对比"]
    if hist:
        for h in hist[:3]:
            lines.append(
                f"- {h.get('timestamp')}：score {h.get('score')}，"
                f"acc {float(h.get('accuracy') or 0) * 100:.2f}%，"
                f"miss {h.get('miss_count')}")
        cur_acc = float(r.get("accuracy") or 0)
        prev_best = max((float(h.get("accuracy") or 0) for h in hist), default=0)
        if cur_acc > prev_best + 0.001:
            lines.append(f"- 本次 accuracy 超过历史同谱最佳（{prev_best * 100:.2f}%）。")
    else:
        lines.append("- 暂无同谱历史记录（这是该谱第一份本地分析）。")

    lines += [
        "",
        "## 下一轮建议",
        "1. 单变量优先：若调整 Saber Offset，一次只动一个轴（如只调 X 位移），同谱打 3-5 把再对比。",
        "2. 若 Center 是瓶颈：选低密度谱面刻意练习切准（盯 cut_distance_cm 均值下降）。",
        "3. 若高速段失误集中：降低 10-20% 速度练连续段，观察 fast_fail_concentration 是否回落到 1 附近。",
        "",
        "## 不确定性",
        "- 本报告由固定规则生成，无法结合谱面结构与你的训练史做深层解释。",
        "- 疲劳指标是运动学推断，可能受体温、状态、谱面结构影响。",
    ]
    return "\n".join(lines)
