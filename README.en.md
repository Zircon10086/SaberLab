<div align="center">

<p><a href="README.md">中文</a> · <a href="README.en.md">English</a></p>

<p><img src="docs/screenshots/saberlab-logo-transparent.png" alt="SABER LAB" width="560"></p>

<p><strong>Local Replay Analysis Lab for Beat Saber</strong></p>

<p>
<a href="https://github.com/Zircon10086/SaberLab"><img src="https://img.shields.io/github/stars/Zircon10086/SaberLab?style=flat&label=%E2%AD%90&color=08C" alt="GitHub stars"></a>
<a href="https://github.com/Zircon10086/SaberLab/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0--or--later-2EA44F?style=flat" alt="GPL-3.0-or-later"></a>
<a href="https://github.com/Zircon10086/SaberLab/releases"><img src="https://img.shields.io/badge/Windows-10%2F11-4493F8?style=flat" alt="Windows"></a>
<a href="https://github.com/Zircon10086/SaberLab"><img src="https://img.shields.io/badge/Desktop-App-47848F?style=flat" alt="Desktop App"></a>
</p>

<p><b>Precision-computed</b> reconstruction of every swing —
accuracy, saber speed, fatigue, and direction changes, plus 3D replay and AI coach reports — all done locally.</p>
This project is undergoing rapid development and iteration; the actual functions may differ slightly from the images

<!-- Screenshots (v1.4.0) -->
<p>
<a href="docs/screenshots/overview.png"><img src="docs/screenshots/overview.png" alt="Overview dashboard" width="720"></a>
</p>
<p>
<a href="docs/screenshots/replay.png"><img src="docs/screenshots/replay.png" alt="Replay detail analysis" width="720"></a>
<a href="docs/screenshots/chro.png"><img src="docs/screenshots/chro.png" alt="ChroViewer 3D replay" width="720"></a>
</p>

</div>

---

## Highlights

| Capability | Description |
| --- | --- |
| **Local-first** | Reads local BeatLeader `.bsor` replays and local maps; every metric is computed deterministically in Python, and raw replays are always read-only |
| **Official algorithm** | Faithful port of the official BSOR decoder/scorer — recomputed totals match the recorded score **note for note** |
| **Standalone window** | Built-in WebView2 window with an acrylic (frosted-glass) background; auto-relocates the port if occupied |
| **3D replay** | A ChroViewer port rendering maps/replays/environments fully locally, from local data only |
| **AI coach** | Structured metrics interpreted by an LLM for personalized guidance; rule-based reports still available without AI |
| **Online sync** | ScoreSaber star/PP cache rooted at local maps, with 429 rate-limit backoff and retry |
| **Completion status** | Automatically detects mid-play exits / NF (Fail) / duration fallback — clear at a glance in lists and details |

---

## Download & Install

### GitHub Releases (recommended)

| File | Description | Size |
| --- | --- | --- |
| [SaberLab-v1.4.0-win64.zip](https://github.com/Zircon10086/SaberLab/releases/download/v1.4.0/SaberLab-v1.4.0-win64.zip) | **User edition**: all dependencies bundled (Python runtime + chro 3D viewer). Unzip and double-click to run | ~45 MB |
| [Source (saberlab-src)](https://github.com/Zircon10086/SaberLab) | **Developer edition**: repository source; install dependencies yourself as described under "Build from Source" | — |

> Older versions are available on the [Releases page](https://github.com/Zircon10086/SaberLab/releases).

**First run**:

1. Double-click `SaberLab.exe` — a console window and the app window open.
2. Go to "Settings → Game Path" and click "Choose folder…" to select your Beat Saber root directory — Replay/Map/SongCore paths are derived and validated automatically, and saved on success.
3. Optional: configure an AI API key (`.env`); without one you still get algorithm-based basic reports.

---

## Features

### Analysis Engine

- **Accuracy**: Pre(70) / Center(15) / Post(30) per hand, cut distance, timing offset, and official exclusion rules (slider/burst special scoring)
- **Time**: 30s windows / 1s step (adjustable), independently normalized display with a real-range legend and hover tooltips
- **Motion**: hand position velocity / angular velocity, path economy, single-hand consecutive direction-change analysis
- **Fatigue**: first-half vs second-half deltas + per-minute slope (kinematic inference, not a medical diagnosis)
- **Profile**: auto-builds a Saber Profile from each replay's controller offset, A/B experiment records (API-only)

### UI & Replay

- **Overview dashboard**: KPI stats row, per-day pagination, wide multi-column layout, completion-status gradients; task progress is shown directly on the "Task Status" card
- **Detail page**: completion card + 2×3 metric grid + time-series / fatigue / hand-motion charts; history for the same map
- **3D replay**: embedded iframe in the detail page (ChroViewer port), fully local WebGL rendering, local map source preferred (remote sources disabled by default)
- **Acrylic window**: automatically captures your local wallpaper for a frosted-glass background — beautiful and still readable

### Integration & Sync

- **ScoreSaber**: caches per-difficulty leaderboards rooted at local maps, four-tier star coloring, player PP, cross-validation; network failures never poison the cache
- **AI Coach**: LLM provider abstraction (OpenAI-compatible protocol) fed with structured metrics, single-variable experiments, facts/inference separation; algorithm-generated basic reports even without a key
- **NPS**: supports v2 / v3 note formats, one-click density computation for all maps

---

## System Requirements

- Windows 10 / 11 (x64)
- WebView2 Runtime (bundled with Windows 10/11)
- The packaged build runs out of the box; running from source requires Python 3.12+

## Build from Source

```bat
:: 1. Dependencies (venv without pip: install with an explicit interpreter)
py -3 -m venv --without-pip .venv
py -3 -m pip --python .venv\Scripts\python.exe install fastapi uvicorn numpy pyyaml pywebview

:: 2. chro subproject (3D replay; rebuild after changing its source)
cd frontend\chro && pnpm build

:: 3. Run
run.bat                 :: standalone window (acrylic)
run-browser.bat         :: dev mode (system browser)
```

Run tests:

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Documentation

- [Changelog](docs/CHANGELOG.en.md)
- [Development Guide](docs/DEVELOPMENT.en.md)

## License

SaberLab itself is released under **[GPL-3.0-or-later](LICENSE)**.

`frontend/chro/` (the ChroViewer port) is a separate aggregated program that follows the upstream [GPL-2.0](frontend/chro/LICENSE); the modification list is in [MODIFICATIONS.md](frontend/chro/MODIFICATIONS.en.md).

## Acknowledgments

- [ChroViewer](https://github.com/Umbranoxio/chroviewer) (Umbranoxio) — the ChroMapper-derived 3D replay engine
- [BS-Open-Replay](https://github.com/BeatLeader/BS-Open-Replay) (BeatLeader) — source of the official BSOR decoder and scoring logic port
- [ScoreSaber API](https://docs.scoresaber.com/) (ScoreSaber) — official ScoreSaber API documentation
- [SongCore](https://github.com/Goobwabber/SongCore) — reference for the map hash algorithm
- The Beat Saber community — for making it all worthwhile

<!-- Placeholder, hidden: ## Contributors -->

<!-- Placeholder: add https://contrib.rocks when there are contributors -->

<!-- Placeholder, hidden: ## Star History -->

<!-- Placeholder: add https://api.star-history.com chart -->
