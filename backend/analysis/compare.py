"""Run A vs Run B comparison (design doc §16 Compare).

Only diffs are computed on deterministic metrics; AI interpretation happens in the report layer.
"""
from __future__ import annotations

KEY_METRICS = [
    ("overall", "accuracy"), ("overall", "score"), ("overall", "score_recomputed"),
    ("overall", "max_combo"),
    ("left", "pre_score_avg"), ("left", "center_score_avg"), ("left", "post_score_avg"),
    ("right", "pre_score_avg"), ("right", "center_score_avg"), ("right", "post_score_avg"),
    ("left", "cut_distance_cm_avg"), ("right", "cut_distance_cm_avg"),
    ("overall", "miss_count"), ("overall", "bad_count"),
    ("left", "saber_speed_avg"), ("right", "saber_speed_avg"),
]


def compare_metrics(metrics_a: dict, metrics_b: dict) -> list[dict]:
    """Return [{scope, name, a, b, diff}]."""
    rows = []
    for scope, name in KEY_METRICS:
        va = metrics_a.get(scope, {}).get(name)
        vb = metrics_b.get(scope, {}).get(name)
        if isinstance(va, dict):
            va = va.get("value")
        if isinstance(vb, dict):
            vb = vb.get("value")
        diff = None
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            diff = round(vb - va, 4)
        rows.append({"scope": scope, "name": name, "a": va, "b": vb, "diff": diff})
    return rows
