"""Export one real replay's measured data for the website (site/data/run.json).

Run locally, where the SaberLab database and the replay file exist:

    .venv\\Scripts\\python.exe site\\tools\\export_run.py <replay_id prefix>

Everything goes through the established layers: the replay row and the official
accuracy curve come from `Repository`, per-note scores from
`analysis.scoring.cut_scores` and the tile grid from
`analysis.slicedetails.analyze_slice_details` — the same functions that fill the
app's own tables, so the site shows the app's numbers, not a re-derivation.
The .bsor is only read. Player name/id, file paths and the replay id are left out.
The output is a derived artifact: commit it, and re-run this after an intentional
analysis change. `site/build.py` never touches the database.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.analysis.scoring import cut_scores  # noqa: E402
from backend.analysis.slicedetails import analyze_slice_details  # noqa: E402
from backend.bsor.models import BAD, GOOD, MISS, SCORING_BURST_SLIDER_ELEMENT  # noqa: E402
from backend.bsor.parser import parse_file  # noqa: E402
from backend.config import load_config  # noqa: E402
from backend.db.repository import Repository  # noqa: E402

OUT = ROOT / "site" / "data" / "run.json"
CURVE_POINTS = 400          # the official running-accuracy curve, decimated for the chart
TYPE_CODE = {GOOD: "g", BAD: "b", MISS: "m"}


def _decimate(t: list[float], y: list[float], n: int) -> list[list[float]]:
    if len(t) <= n:
        idx = range(len(t))
    else:
        idx = sorted({round(i * (len(t) - 1) / (n - 1)) for i in range(n)})
    return [[round(t[i], 3), round(y[i], 5)] for i in idx]


def export(prefix: str) -> dict:
    cfg = load_config()
    repo = Repository(cfg.db_path)
    try:
        matches = [r for r in repo.list_replays(limit=100000) if r["replay_id"].startswith(prefix)]
        if len(matches) != 1:
            raise SystemExit(f"replay id prefix {prefix!r} matches {len(matches)} replays")
        row = repo.get_replay(matches[0]["replay_id"])
        curve = repo.get_accuracy_curve(row["replay_id"], ma_window=1)
    finally:
        repo.close()
    if not row.get("file_path") or not Path(row["file_path"]).is_file():
        raise SystemExit("the replay file is missing; per-note scores need it")

    replay = parse_file(row["file_path"])
    notes = []
    for note in sorted(replay.notes, key=lambda n: n.event_time):
        params = note.params
        if note.event_type not in TYPE_CODE or params.scoring_type == SCORING_BURST_SLIDER_ELEMENT:
            continue                                   # bombs and burst links are not block cuts
        pre, center, post = cut_scores(note) if note.event_type == GOOD else (0, 0, 0)
        notes.append([round(note.event_time, 3), "l" if params.color_type == 0 else "r",
                      pre, center, post, TYPE_CODE[note.event_type]])

    tiles = analyze_slice_details(replay.notes, height=replay.info.height,
                                  left_handed=replay.info.left_handed)["tiles"]
    played = datetime.fromtimestamp(row["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d")
    return {
        "schema": 1,
        "song": row["song_name"], "mapper": row["mapper"],
        "difficulty": row["difficulty"], "mode": row["mode"],
        "played": played, "duration": round(row["duration"], 2),
        "accuracy": round(row["accuracy"], 5),
        "counts": {"notes": row["note_count"], "good": row["good_count"],
                   "bad": row["bad_count"], "miss": row["miss_count"]},
        "curve": _decimate(curve["t"], curve["acc"], CURVE_POINTS),
        # [time, hand l/r, pre, center, post, type g/b/m]
        "notes": notes,
        # 4x3 grid, index = layer * 4 + line (layer 0 = bottom row)
        "tiles": [{"count": t["count"], "avg": round(t["score_avg"], 2)} for t in tiles],
    }


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    data = export(argv[0])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    good = [n for n in data["notes"] if n[5] == "g"]
    print(f"{data['song']} [{data['difficulty']}] acc={data['accuracy']:.4f} "
          f"notes={len(data['notes'])} good={len(good)} -> {OUT.relative_to(ROOT)} "
          f"({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
