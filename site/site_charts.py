"""Turn site/data/run.json (one real replay, exported by site/tools/export_run.py)
into static page fragments: the run chart as inline SVG, the tile grid, and the
numbers around them.

Everything here is presentation: it arranges already-measured values. The one
derived number is the "weakest 10 seconds" window (lowest mean cut score, misses
counted as 0), a plain sliding average over the exported per-note scores.

The SVG is drawn in a fixed 1000 x H viewBox and stretched to its box with
`preserveAspectRatio="none"`; every stroke is non-scaling (CSS), and all text sits in
HTML positioned by percentage, so nothing distorts at any width.
"""
from __future__ import annotations

import html
import json

W, H = 1000.0, 300.0
PAD_X = 6.0                                  # keeps the first/last tick off the frame
SCORE_TOP, SCORE_BOTTOM = 8.0, 196.0         # lane 1: per-note cut score
ACC_TOP, ACC_BOTTOM = 224.0, 292.0           # lane 2: running accuracy
SCORE_MIN, SCORE_MAX = 70.0, 115.0           # every good cut in a typical run sits in here
WINDOW_S, WINDOW_MIN_NOTES = 10.0, 12
MA_NOTES = 41                                # per-hand moving average, centred
REQUIRED = ("song", "mapper", "difficulty", "played", "duration", "accuracy", "counts",
            "curve", "notes", "tiles")


class RunDataError(ValueError):
    pass


def _f(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _mmss(t: float) -> str:
    t = max(0, int(round(t)))
    return f"{t // 60}:{t % 60:02d}"


def _pct(v: float, digits: int = 2) -> str:
    return f"{v * 100:.{digits}f}%"


def validate(run: dict) -> None:
    missing = [k for k in REQUIRED if k not in run]
    if missing:
        raise RunDataError(f"run.json: missing fields {missing}")
    if run.get("schema") != 1:
        raise RunDataError("run.json: unsupported schema (expected 1)")
    if len(run["tiles"]) != 12:
        raise RunDataError("run.json: tiles must hold the 4 x 3 grid (12 entries)")
    goods = [n for n in run["notes"] if n[5] == "g"]
    if len(goods) < WINDOW_MIN_NOTES or len(run["curve"]) < 2:
        raise RunDataError("run.json: too few notes to draw a run")


def _cut_total(note: list) -> int:
    return note[2] + note[3] + note[4] if note[5] == "g" else 0


def weakest_window(notes: list) -> tuple[float, float, float, int]:
    """(first, last note time, mean score per note, notes) of the lowest-scoring 10 s.

    Windows start at every note; misses and bad cuts count as 0. The first minimum wins,
    so the result is deterministic.
    """
    best, total, j = None, 0, 0
    for i, first in enumerate(notes):
        while j < len(notes) and notes[j][0] < first[0] + WINDOW_S:
            total += _cut_total(notes[j])
            j += 1
        count = j - i
        if count >= WINDOW_MIN_NOTES:
            mean = total / count
            if best is None or mean < best[2]:
                best = (first[0], notes[j - 1][0], mean, count)
        total -= _cut_total(first)
    if best is None:
        raise RunDataError(f"run.json: no {WINDOW_S:.0f} s window holds {WINDOW_MIN_NOTES} notes")
    return best


def _moving_average(values: list[float], k: int) -> list[float]:
    half, prefix = k // 2, [0.0]
    for v in values:
        prefix.append(prefix[-1] + v)
    n = len(values)
    return [(prefix[min(n, i + half + 1)] - prefix[max(0, i - half)])
            / (min(n, i + half + 1) - max(0, i - half)) for i in range(n)]


def _label(cls: str, text: str, left: float | None = None, top: float | None = None) -> str:
    style = ";".join(s for s in (f"left:{left:.2f}%" if left is not None else "",
                                 f"top:{top:.2f}%" if top is not None else "") if s)
    return f'<span class="{cls}" style="{style}">{html.escape(text)}</span>'


def build_chart(run: dict) -> dict:
    notes = run["notes"]
    t0, t1 = notes[0][0], notes[-1][0]
    span = max(t1 - t0, 1e-6)

    def x(t: float) -> float:
        return PAD_X + (t - t0) / span * (W - 2 * PAD_X)

    def y_score(s: float) -> float:
        s = min(max(s, SCORE_MIN), SCORE_MAX)
        return SCORE_BOTTOM - (s - SCORE_MIN) / (SCORE_MAX - SCORE_MIN) * (SCORE_BOTTOM - SCORE_TOP)

    # lane 1: one short tick per good cut, per hand, plus a centred moving average
    ticks, averages = {"l": [], "r": []}, {}
    for hand in ("l", "r"):
        cuts = [n for n in notes if n[5] == "g" and n[1] == hand]
        for n in cuts:
            y = y_score(_cut_total(n))
            ticks[hand].append(f"M{_f(x(n[0]))} {_f(y - 1.6)}v3.2")
        ma = _moving_average([float(_cut_total(n)) for n in cuts], MA_NOTES)
        step = max(1, len(cuts) // 240)
        pts = [(x(cuts[i][0]), y_score(ma[i])) for i in range(0, len(cuts), step)]
        averages[hand] = "M" + " L".join(f"{_f(a)} {_f(b)}" for a, b in pts)

    # lane 2: the official running accuracy, skipping the first 8 % where it swings wildly
    settle = t0 + 0.08 * span
    settled = [a for t, a in run["curve"] if t >= settle] or [a for _, a in run["curve"]]
    lo = int(min(settled) * 100) / 100
    hi = (int(max(settled) * 100) + 1) / 100

    def y_acc(a: float) -> float:
        a = min(max(a, lo), hi)
        return ACC_BOTTOM - (a - lo) / (hi - lo) * (ACC_BOTTOM - ACC_TOP)

    # drawn from the same point: before it the values sit outside the lane and would clamp flat
    acc_pts = [(x(t), y_acc(a)) for t, a in run["curve"] if settle <= t <= t1]
    acc_path = "M" + " L".join(f"{_f(a)} {_f(b)}" for a, b in acc_pts)
    final_y = y_acc(run["accuracy"])

    events = {"m": [], "b": []}
    for n in notes:
        if n[5] in events:
            events[n[5]].append(f"M{_f(x(n[0]))} {_f(SCORE_TOP)}V{_f(ACC_BOTTOM)}")

    w_first, w_last, w_mean, w_count = weakest_window(notes)
    wx0, wx1 = x(w_first), max(x(w_last), x(w_first) + 2)

    grid = [(s, y_score(s)) for s in (115, 100, 85, 70)]
    svg = "".join([
        f'<svg class="run-svg" viewBox="0 0 {_f(W)} {_f(H)}" preserveAspectRatio="none" '
        f'role="img" aria-labelledby="run-title" focusable="false">',
        f'<rect class="run-weak" x="{_f(wx0)}" y="{_f(SCORE_TOP)}" width="{_f(wx1 - wx0)}" '
        f'height="{_f(ACC_BOTTOM - SCORE_TOP)}"/>',
        '<path class="run-grid" d="' + "".join(f"M0 {_f(yy)}H{_f(W)}" for _, yy in grid)
        + f'M0 {_f(ACC_TOP)}H{_f(W)}M0 {_f(ACC_BOTTOM)}H{_f(W)}"/>',
        f'<path class="run-final" d="M0 {_f(final_y)}H{_f(W)}"/>',
        f'<path class="run-bad" d="{"".join(events["b"])}"/>',
        f'<path class="run-miss" d="{"".join(events["m"])}"/>',
        f'<path class="run-tick run-l" d="{"".join(ticks["l"])}"/>',
        f'<path class="run-tick run-r" d="{"".join(ticks["r"])}"/>',
        # each curve is stored once and drawn twice (glow + line); stroke paint is
        # inherited from the <use>, vector-effect is not, so it sits on the path
        '<defs>',
        f'<path id="run-avg-l" vector-effect="non-scaling-stroke" d="{averages["l"]}"/>',
        f'<path id="run-avg-r" vector-effect="non-scaling-stroke" d="{averages["r"]}"/>',
        f'<path id="run-acc" vector-effect="non-scaling-stroke" d="{acc_path}"/>',
        '</defs>',
        '<use href="#run-avg-l" class="run-avg-glow run-l"/><use href="#run-avg-l" class="run-avg run-l"/>',
        '<use href="#run-avg-r" class="run-avg-glow run-r"/><use href="#run-avg-r" class="run-avg run-r"/>',
        '<use href="#run-acc" class="run-acc-glow"/><use href="#run-acc" class="run-acc"/>',
        "</svg>",
    ])

    labels = [_label("run-y", str(s), top=yy / H * 100) for s, yy in grid]
    labels += [_label("run-y run-y-acc", _pct(v, 0), top=y_acc(v) / H * 100) for v in (lo, hi)]
    labels.append(_label("run-y run-y-final", _pct(run["accuracy"]), top=final_y / H * 100))
    first_tick = (int(t0 // 30) + 1) * 30
    labels += [_label("run-x", _mmss(t), left=x(t) / W * 100)
               for t in range(first_tick, int(t1) + 1, 30)]
    labels.append(_label("run-weak-tag", f"{_mmss(w_first)}–{_mmss(w_last)}",
                         left=(wx0 + wx1) / 2 / W * 100))

    hover = {"x0": PAD_X / W, "x1": (W - PAD_X) / W, "t0": t0, "t1": t1,
             "n": [[round(n[0], 2), n[1], n[2], n[3], n[4], n[5]] for n in notes]}
    return {
        "svg": svg, "labels": "".join(labels),
        "acc_top": ACC_TOP / H * 100,
        "hover": json.dumps(hover, separators=(",", ":")).replace("</", "<\\/"),
        "weak": (w_first, w_last, w_mean, w_count),
    }


def build_tiles(tiles: list[dict]) -> str:
    """4 x 3 grid, top row first (tile index = layer * 4 + line, layer 0 = bottom)."""
    used = [t["avg"] for t in tiles if t["count"]]
    lo, hi = min(used), max(used)
    # same rule as render(): the first tile with the lowest average
    weakest = min((i for i, t in enumerate(tiles) if t["count"]), key=lambda i: tiles[i]["avg"])
    cells = []
    for order, layer in enumerate((2, 1, 0)):
        for line in range(4):
            i = layer * 4 + line
            t = tiles[i]
            delay = order * 4 + line
            if not t["count"]:
                cells.append(f'<div class="tile tile-empty" style="--i:{delay}"><b>—</b></div>')
                continue
            heat = (t["avg"] - lo) / (hi - lo) if hi > lo else 1.0
            cls = "tile tile-weak" if i == weakest else "tile"
            cells.append(f'<div class="{cls}" style="--heat:{heat:.3f};--i:{delay}">'
                         f'<b>{t["avg"]:.1f}</b><small>{t["count"]}</small></div>')
    return "".join(cells)


def render(run: dict) -> tuple[dict, set]:
    """Placeholders for the page (all under `run.`) and the subset that is raw HTML."""
    validate(run)
    chart = build_chart(run)
    goods = [n for n in run["notes"] if n[5] == "g"]

    def mean(k: int) -> str:
        return f"{sum(n[k] for n in goods) / len(goods):.2f}"

    w_first, w_last, w_mean, w_count = chart["weak"]
    counts = run["counts"]
    weak_tile = min((t for t in run["tiles"] if t["count"]), key=lambda t: t["avg"])
    ctx = {
        "run.song": run["song"], "run.mapper": run["mapper"],
        "run.difficulty": run["difficulty"], "run.played": run["played"],
        "run.duration": _mmss(run["duration"]),
        "run.accuracy": _pct(run["accuracy"]),
        "run.notes": f"{counts['notes']:,}", "run.good": f"{counts['good']:,}",
        "run.misses": str(counts["miss"]), "run.bad": str(counts["bad"]),
        "run.pre": mean(2), "run.center": mean(3), "run.post": mean(4),
        "run.cut": f"{sum(_cut_total(n) for n in goods) / len(goods):.2f}",
        "run.weak_range": f"{_mmss(w_first)}–{_mmss(w_last)}",
        "run.weak_mean": f"{w_mean:.1f}", "run.weak_count": str(w_count),
        "run.acc_top": f"{chart['acc_top']:.2f}%",
        "run.svg": chart["svg"], "run.labels": chart["labels"],
        "run.hover": chart["hover"], "run.tiles": build_tiles(run["tiles"]),
        "run.weak_tile_avg": f"{weak_tile['avg']:.1f}",
        "run.weak_tile_count": str(weak_tile["count"]),
    }
    return ctx, {"run.svg", "run.labels", "run.hover", "run.tiles"}
