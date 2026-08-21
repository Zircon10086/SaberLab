# Development Guide

> SaberLab's technical architecture, development conventions, and common pitfalls. Changelog: [CHANGELOG.en.md](CHANGELOG.en.md). Much of this document was summarized with AI assistance.

## 1. Project Overview

| Layer | Tech | Description |
|---|---|---|
| Backend | Python 3.12+ / FastAPI / uvicorn / numpy / pyyaml / pywebview | Monolithic FastAPI, SQLite (WAL) storage; the HTTP API is the only IPC |
| Frontend | Vanilla HTML/CSS/JS (zero dependencies, app.js ~1500 lines) | Dynamic forms driven by the backend schema; the acrylic layer is only enabled in window mode |
| 3D replay | `frontend/chro/` (Vite + React + Three.js, ChroViewer port) | Standalone build, served at `/chro/` |
| Packaging | PyInstaller onedir (`packaging/saberlab.spec`) | Fully bundled, double-click to run |

Design principles (from the design docs): local-first / deterministic-first; raw replays are read-only; AI only interprets and never generates data; the HTTP API is the only IPC (the frontend never calls pywebview's js_api).

## 2. Environment Setup

```bat
:: Special venv note: the official venv has no pip, so install with an explicit interpreter
py -3 -m venv --without-pip .venv
py -3 -m pip --python .venv\Scripts\python.exe install fastapi uvicorn numpy pyyaml pywebview

:: chro subproject (must rebuild after changing chro source, otherwise changes don't take effect)
cd frontend\chro && pnpm build
```

Current dependencies: fastapi / uvicorn / numpy / pyyaml / pywebview 6.2.1 / pyinstaller 6.22.2 (watchdog and httpx have been removed).

## 3. Running

| Command | Mode |
|---|---|
| `run.bat` (= `backend\host.py`) | Standalone window (WebView2 + acrylic) |
| `run-browser.bat` (= `backend\host.py --browser`) | System browser (dev mode, no acrylic) |
| `backend\host.py --acrylic-mode off` | Window without acrylic (visual comparison) |
| `backend\host.py --acrylic-mode backdrop\|acrylic` | Experimental: DWM backdrop board (known client-area grey limitation) |

- Port 8787, auto-relocates to 8788..8806 if occupied; single instance: prompts and exits if another instance is running
- Closing the window → graceful exit (uvicorn should_exit, no leftover processes)

## 4. Directory Layout

```
backend/
  bsor/        BSOR v1 parser (pure functions, zero external coupling)
  maps/        map hash parsing and caching
  analysis/    deterministic metrics (scoring/accuracy/windows/motion/fatigue/compare)
  ai/          LLM provider abstraction + prompts + rule-based fallback
  config/      ConfigService (config.yaml is the single source of truth) + schema (drives frontend dynamic forms)
  db/          SQLite schema + repository (all migrations consolidated; fresh DBs are created with the full schema)
  services/    enrichment (enrichment cache service), etc.
  watcher.py   scanning + layered analysis pipeline
  scoresaber.py  online sync (persistent per-thread connections + concurrency + 429 backoff)
  desktop.py   wallpaper / monitor geometry (ctypes Win32, acrylic scheme C backend)
  dialog.py    native dialog bridge (shared state between __main__ and backend.main)
  host.py      standalone window host (port / single instance / uvicorn thread / pywebview / acrylic)
  main.py      FastAPI entry (route assembly)
frontend/      vanilla dashboard (index.html + app.js + style.css)
frontend/chro/ ChroViewer port subproject (standalone Vite build)
tests/         unit tests (golden fixture regression + schema bootstrap/upgrade)
config/        config.yaml
packaging/     PyInstaller spec + packaging docs
others/        design docs (see §8 index)
_tools/        debug tools (cdp_stack/chro_smoke, etc.)
_tmp/          temporary test area (probes/screenshot scripts, safe to wipe)
```

## 5. Backend Notes

### 5.1 Config System (schema-driven)
- `config/schema.py` defines every config item (key/label/type/group/hidden/restart_required); the frontend generates the settings UI from it and the backend reads/validates against it
- Path derivation: `game.instance_root` → replay / custom_levels / songcore (`config/service.py` DERIVED_PATHS, standard Beat Saber relative paths); `hidden: True` items are handled by the "Game Path" card (native folder dialog + automatic validation)
- Atomic writes: tmp → flush → os.replace; a corrupt config.yaml is auto-backed up as `.corrupt-<ts>`

### 5.2 dialog.py Bridge (important)
When started via `python backend\host.py`, the script runs as `__main__`; if main.py does `from backend.host import ...` it gets a **duplicate module** of host.py (module-level global state is not shared — this once made the folder dialog permanently unavailable). Shared state always goes through `backend/dialog.py`: host.py registers the window shell, main.py routes read it.

### 5.3 Acrylic (Scheme C data flow)
```
backend/desktop.py  wallpaper path three-tier fallback + window/monitor geometry (ctypes)
host.py service thread  initial push + 1s polling (wallpaper mtime/size, monitor geometry changes → push)
frontend app.js         rAF reads screenX/Y every frame for self-cropping (zero IPC) + wallpaper preload/swap
move cover             moved/resized → __saberlabBackdropMoving(true); no events for 1s → false
                       (backend watchdog + frontend 1.5s fallback)
```
Frontend contract: `window.__saberlabBackdrop(payload)` (mode=wallpaper/backdrop, monitor, wallpaper_url?v=), `window.__saberlabBackdropMoving(bool)`. Browser mode (no `?shell=webview`) never enables it.

### 5.4 Task System
- 5 long-running tasks (ingest / analyze / map_scan / ranked_update / nps_update) = "acquire lock → daemon thread → frontend pollTask polls /api/status every 1.5s"; no queue, no cancellation, lost on restart
- Task status card: frontend `updateTaskKpi(t)` renders progress onto the KPI card background (red/blue gradient + text updates)

### 5.5 repository
SQLite opens a new connection per call (WAL, 30s timeout); all SQL lives in `db/repository.py`; schema migrations are consolidated in `db/models.py` (fresh DBs get the full schema; `_migrate` upgrades old DBs idempotently).

## 6. Frontend Notes

- Zero-dependency single files: `index.html` (static skeleton + settings/detail templates), `app.js` (render + interaction), `style.css`
- Dynamic settings form: driven by `/api/settings/schema` (`renderSettingsForm`, `hidden` items skipped)
- Charts: `lineChart` (SVG + crosshair hover); detail-page equal-height logic — `fixDetailLayout()` fixes chart heights once on entry (do not revert to `height: auto` live calculation; it triggers a positive-feedback height loop)
- Acrylic layer: `#acrylic-backdrop` (fixed inset 0 + blur); the `.moving` class maxes the blur while moving

## 7. Testing & Debugging

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```
- Golden Fixture #001: SECRET BOSS Expert (`tests/test_bsor_parser.py`, 2069 notes, full assertions)
- `_tmp/` probes (reusable): `probe_transparent.py` / `probe_dwm.py` (acrylic capabilities), `probe_kpi*.py` (KPI/task card styles), `probe_layout.py` (detail chart height), `probe_height.py`
- `_tmp/shot.ps1` screenshots by window title; `_tmp/pngstats.py` numpy pixel statistics (for validating the UI without a vision model)
- Debug notes: window-mode logs go to the run.bat console; `print` to pipes/redirection needs `flush=True`

## 9. Common Pitfalls Quick Reference

1. **venv without pip**: always install with `py -3 -m pip --python .venv\Scripts\python.exe install ...`
2. **Duplicate module**: main.py must not import backend.host module-level state directly (see §5.2; use dialog.py)
3. **WebView2 transparency limitation**: pywebview transparent mode has no window-level transparency (client area = window background color), so acrylic schemes A/B are infeasible; the DWM backdrop board is only visible on the title bar
4. **chro build**: after changing chro source you must `pnpm build`, otherwise the backend serves stale artifacts
5. **Packaging**: pass `uvicorn.Config(app=app)` the object, not an import string (unresolvable when frozen); `PROJECT_ROOT` equals the exe directory when frozen
6. **Console encoding**: Chinese output is fine on a GBK console; when piping/redirecting, confirm the encoding and add flush
