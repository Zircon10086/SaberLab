# Development Guide

> SaberLab's technical architecture, development conventions, and common pitfalls. Changelog: [CHANGELOG.en.md](CHANGELOG.en.md). Much of this document was summarized with AI assistance.

## 1. Project Overview

| Layer | Tech | Description |
|---|---|---|
| Backend | Python 3.12+ / FastAPI / uvicorn / numpy / pyyaml / pywebview | Monolithic FastAPI, SQLite (WAL) storage; the HTTP API is the only IPC |
| Frontend | Vanilla HTML/CSS/JS (zero dependencies, app.js ~1500 lines) | Dynamic forms driven by the backend schema; the acrylic layer is only enabled in window mode |
| 3D replay | **External component** Local-ChroViewer (Vite + React + Three.js, ChroViewer port, independent GPL-2.0 project) | Not in this repo's source; build output auto-detected at runtime and served at `/chro/` |
| Packaging | PyInstaller onedir (`packaging/saberlab.spec`) | Fully bundled, double-click to run |

Design principles (from the design docs): local-first / deterministic-first; raw replays are read-only; AI only interprets and never generates data; the HTTP API is the only IPC (the frontend never calls pywebview's js_api).

## 2. Environment Setup

```bat
:: Special venv note: the official venv has no pip, so install with an explicit interpreter
py -3 -m venv --without-pip .venv
py -3 -m pip --python .venv\Scripts\python.exe install fastapi uvicorn numpy pyyaml pywebview

:: (Optional) 3D replay component Local-ChroViewer (independent GPL-2.0
:: project, not in this repo's source). Clone/build it next to SaberLab; the
:: backend auto-detects it at startup and mounts /chro/:
::   git clone <Local-ChroViewer repo> ..\Local-ChroViewer
::   cd ..\Local-ChroViewer && pnpm install && pnpm build
```

Current dependencies: fastapi / uvicorn / numpy / pyyaml / pywebview 6.2.1 / pyinstaller 6.22.2 (watchdog and httpx have been removed).

## 3. Running

| Command | Mode |
|---|---|
| `run.bat` (= `backend\host.py`) | Standalone window (WebView2 + acrylic) |
| `run-browser.bat` (= `backend\host.py --browser`) | System browser (dev mode, no acrylic) |
| `backend\host.py --acrylic-mode off` | Window without acrylic (visual comparison) |
| `backend\host.py --acrylic-mode backdrop\|acrylic` | Experimental: DWM backdrop board (known client-area grey limitation) |

- Port 6980 by default. If the standard/configured range contains an old SaberLab,
  startup verifies both its `/api/status` identity and TCP owner PID, terminates
  it, and binds 6980 again. Unrelated port occupants are never killed and still
  cause safe fallback to 6981..6999. A Windows named mutex serializes concurrent
  launchers through server readiness, preventing double-launch races.
- Closing the window → graceful exit (uvicorn should_exit, no leftover processes)

## 4. Directory Layout

```
backend/
  bsor/        BSOR v1 parser (pure functions, zero external coupling)
  maps/        map hash parsing and caching
  analysis/    deterministic metrics (scoring/accuracy/notes/motion/fatigue/compare)
  ai/          LLM provider abstraction + prompts + rule-based fallback
  config/      ConfigService (config.yaml is the single source of truth) + schema (drives frontend dynamic forms)
  db/          SQLite schema + repository (all migrations consolidated; fresh DBs are created with the full schema)
  services/    enrichment (enrichment cache service), etc.
  watcher.py   scanning + layered analysis pipeline
  scoresaber.py  online sync (persistent per-thread connections + concurrency + 429 backoff)
  desktop.py   wallpaper / monitor geometry (ctypes Win32, acrylic scheme C backend)
  dialog.py    native dialog bridge + backdrop-ready flag (shared state between __main__ and backend.main)
  host.py      standalone window host (port / single instance / uvicorn thread / pywebview / acrylic)
  main.py      FastAPI entry (route assembly)
frontend/      vanilla dashboard (index.html + app.js + style.css + i18n.js)
frontend/i18n/ language tables (zh-CN/en-US/ja-JP.json, each with its own lang.name)
(outside repo) Local-ChroViewer/  3D replay external component (independent GPL-2.0
                        project, Vite build; backend probes candidate paths for its
                        dist/, see §5.6)
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
- Atomic writes: tmp → flush → os.replace; a corrupt config.yaml is auto-backed
  up as `.corrupt-<ts>`. Check the warning's **absolute path** first: test
  fixtures commonly live under `_tmp` and must not be mistaken for the user's
  real `config/config.yaml`
- Key items: `ai.ai_report_enabled` ("Use AI for Reports" — unchecked short-circuits `run_ai_report` to the rule report, no LLM calls); `analysis.slope_group_notes` (note-group size); `analysis.window_seconds/window_step_seconds` (deprecated, hidden, kept for compatibility)

### 5.2 dialog.py Bridge (important)
When started via `python backend\host.py`, the script runs as `__main__`; if main.py does `from backend.host import ...` it gets a **duplicate module** of host.py (module-level global state is not shared — this once made the folder dialog permanently unavailable). Shared state always goes through `backend/dialog.py`: host.py registers the window shell, main.py routes read it.

### 5.3 Acrylic (Scheme C data flow)
```
backend/desktop.py  wallpaper path three-tier fallback + window/monitor geometry (ctypes)
host.py service thread  initial push + 1s polling (wallpaper mtime/size, monitor geometry changes → push)
frontend app.js         rAF reads screenX/Y every frame for self-cropping (zero IPC) + wallpaper preload/swap
move cover             moved/resized → __saberlabBackdropMoving(true); no events for 1s → false
                       (backend watchdog + frontend 1.5s fallback)
reload re-push         frontend POSTs /api/desktop/backdrop-ready on load/reload (dialog.py
                       flag bridge) → service thread consumes it and re-pushes the payload
                       (otherwise any reload — e.g. language switch — permanently loses the glass)
```
Frontend contract: `window.__saberlabBackdrop(payload)` (mode=wallpaper/backdrop, monitor, wallpaper_url?v=), `window.__saberlabBackdropMoving(bool)`. Browser mode (no `?shell=webview`) never enables it.

### 5.4 Task System
- 5 long-running tasks (ingest / analyze / map_scan / ranked_update / nps_update) = "acquire lock → daemon thread → frontend pollTask polls /api/status every 1.5s"; no queue, no cancellation, lost on restart
- Task status card: frontend `updateTaskKpi(t)` renders progress onto the KPI card background (red/blue gradient + text updates)

### 5.5 repository
SQLite opens a new connection per call (WAL, 30s timeout); all SQL lives in `db/repository.py`; schema migrations are consolidated in `db/models.py` (fresh DBs get the full schema; `_migrate` upgrades old DBs idempotently).

### 5.6 Plugin system (v2.0.0: plugins directory detection & loading)
- The root `plugins/` directory is detected and loaded by convention for
  first-party plugins: **projects under different licenses or other complete
  features** are shipped as plugins placed into `plugins/<name>/` and integrated
  at startup. Current mechanism: a directory with an entry file (`index.html`)
  is mounted/enabled; **first-party only — no third-party plugin interface or
  spec** (no dedicated API).
- The only plugin today = 3D replay (Local-ChroViewer): source lives in the
  **independent project** `Local-ChroViewer/` (GPL-2.0, ChroViewer port, not in
  this repo); **single detection path** `<repo>/plugins/chro` (mounted at
  `/chro/` when `index.html` exists; when frozen `PROJECT_ROOT` == `<exe dir>`,
  the same path covers the packaged layout) — **no fallback**: removing the
  plugin directory immediately disables it
- When present → `/api/status` returns `chro.available=true`; otherwise the
  detail-page "Replay" pane shows a grey install hint (zh/en/ja, pointing at
  `plugins/chro/`); the frontend decides iframe vs hint via
  `window.chroAvailable` (set by loadStatus)
- Plugin directory conventions: `plugins/README.md`

## 6. Frontend Notes

- Zero-dependency single files: `index.html` (static skeleton + settings/detail templates), `app.js` (render + interaction), `style.css`
- Dynamic settings form: driven by `/api/settings/schema` (`renderSettingsForm`, `hidden` items skipped)
- Charts: `lineChart` (SVG + crosshair hover); detail-page equal-height logic — `fixDetailLayout()` fixes chart heights once on entry (do not revert to `height: auto` live calculation; it triggers a positive-feedback height loop)
- Acrylic layer: `#acrylic-backdrop` (fixed inset 0 + blur); the `.moving` class maxes the blur while moving

### 6.1 i18n (multilingual)
- Mechanism: `frontend/i18n.js` (`I18N.init/t/renderLangSwitch`) + `frontend/i18n/{lang}.json`
  tables (zh-CN is the baseline; missing keys fall back to Chinese); the preference
  lives in localStorage (`saberlab.lang`)
- **Auto-discovery**: `GET /api/i18n/langs` scans `frontend/i18n/*.json` (filename regex
  `[a-z]{2}(-[A-Z]{2})?`) and reads each file's `lang.name` for the button label —
  adding a JSON file is all it takes to enable a new language
- Text hooks: static text `data-i18n` / `data-i18n-placeholder` / `data-i18n-title`
  (titles containing child elements must wrap the text in `<span data-i18n>`); dynamic
  text via `t(key, params)`; backend error messages via `tErr(msg)` (en/ja `err` tables
  keyed by the Chinese source text, `{param}` template regex matching)
- Settings wording: schema labels/descriptions are Chinese; the frontend looks up
  `set.{key}.label/.desc` + `set.group.{group}` by config key (falls back to Chinese)
- Chart labels (TL_LABELS/TL_VALUE_FMT) depend on the dict, so build them after
  `I18N.init()` via `buildTimelineI18n()` (top-level t() calls run before the dict loads)
- Squircle corners: at the END of style.css
  `@supports (corner-shape: squircle) { .surface, .kpi { border-radius: 40px; corner-shape: squircle; } }`
  (native on Chrome 139+, 12px fallback elsewhere; **must be at the end** — earlier it
  gets overridden by `.kpi`'s own border-radius, see §4.18)

### 6.2 AI report language
- `ai/prompts.py`: `build_system_prompt(lang)` = English base rules + a STRONG output-language
  instruction (MUST / 务必 / 必ず) + language-specific section headings
  (## 结论 / ## Conclusion / ## 結論) — the prompt body must stay English, otherwise the
  LLM follows the body language (§4.16 lesson)
- Entry points pass `lang` through: `/api/ai/analyze/{id}?lang=`, batch-analysis body `lang`
  (frontend sends `I18N.lang`); the rule report (`ai/fallback.py` `_TEXT`, three languages) follows too
- Whether the LLM is called at all is decided by `ai.ai_report_enabled` (single short-circuit
  inside `run_ai_report`)

## 7. Testing & Debugging

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```
- **Final UI/E2E acceptance must use the standalone WebView2 window**, launched
  by `run.bat` or `backend/host.py` without `--browser`. Browser mode is allowed
  for narrow diagnostics, but browser rendering does not count as final UI/E2E
  verification. Target the window title `SaberLab — Beat Saber 本地分析实验室`.
- Golden Fixture #001: SECRET BOSS Expert (`tests/test_bsor_parser.py`, 2069 notes, full assertions)
- `_tmp/` probes (reusable): `probe_transparent.py` / `probe_dwm.py` (acrylic capabilities), `probe_kpi*.py` (KPI/task card styles), `probe_layout.py` (detail chart height), `probe_height.py`
- `_tmp/shot.ps1` screenshots by window title; `_tmp/pngstats.py` numpy pixel statistics (for validating the UI without a vision model)
- Debug notes: window-mode logs go to the run.bat console; `print` to pipes/redirection needs `flush=True`

## 9. Build & Release Conventions (2026-08 user decision, mandatory)

> Goal: the dev environment must behave exactly like the user edition so that
> user-edition-only issues surface during development.

1. **Clean up after building**: after each successful PyInstaller build/export
   (i.e. once the `GitHub_Build\<version>\` archive exists) the temporary build
   artifacts **must be deleted** — `build/` (PyInstaller intermediates) and
   `dist/` (build output). `_tools/export_github_pkg.ps1` already does this at
   the end; for manual builds follow the same convention. Version archives are
   the only retained builds; always reference old-version code from
   `GitHub_Build\<version>\saberlab-src\` — never rely on local `dist/build`
   (they may be deleted at any time).
2. **No fallback references to builds**: code/detection logic must not contain
   "fall back to a local build artifact" paths. Anti-example (fixed): chro used
   to fall back to `../Local-ChroViewer/dist`, so the dev environment loaded the
   viewer while the user edition lacked it — detection now uses the single
   first-party plugin path `plugins/chro/`.
3. **Dev environment == user behavior**: detection paths, dependency resolution
   and permissions in the dev environment must match the release. Any
   "dev-convenience" path that differs from the user edition must be evaluated
   for behavioral divergence; prefer copying/placing artifacts to the same
   location the user edition uses over adding dev-only fallbacks.

## 10. Common Pitfalls Quick Reference

1. **venv without pip**: always install with `py -3 -m pip --python .venv\Scripts\python.exe install ...`
2. **Duplicate module**: main.py must not import backend.host module-level state directly (see §5.2; use dialog.py)
3. **WebView2 transparency limitation**: pywebview transparent mode has no window-level transparency (client area = window background color), so acrylic schemes A/B are infeasible; the DWM backdrop board is only visible on the title bar
4. **chro independent build**: the 3D replay viewer is the sibling external
   project Local-ChroViewer; after changing its source you must `pnpm build`,
   then place the build output into the first-party plugin directory
   `plugins/chro/` — the **only** detection path, no fallback: without it
   `/chro/` is not mounted and the detail page shows the install hint
5. **Packaging**: pass `uvicorn.Config(app=app)` the object, not an import string (unresolvable when frozen); `PROJECT_ROOT` equals the exe directory when frozen
6. **Console encoding**: Chinese output is fine on a GBK console; when piping/redirecting, confirm the encoding and add flush
