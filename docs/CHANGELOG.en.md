# Changelog

> Version format: `vX.Y (date)` — change summary. Parts of this document were described with AI assistance.

## v1.5.0 (2026-08-23)

### Analysis engine: fixed time windows retired
- All time-based analysis is now **note-anchored** (timeline = first/last note):
  saber speed & density are per-note curves (±7 local mean / ±5 neighborhood +
  smoothing), fatigue slopes & AI timeline summaries use note groups
  (N notes per group, configurable); mid-song density dips faithfully reflect
  the map layout; the fixed-window config keys are marked deprecated
  (hidden, kept for compatibility)
- **Accuracy curve now uses the official formula** (score/maxScore incl.
  penalties & multiplier) — the curve end matches the replay record and the
  3D viewer exactly

### Multilingual (简体中文 / English / 日本語)
- UI language switching (JSON tables; the settings language card **auto-discovers
  language files** — adding a JSON file enables a new language)
- AI and rule-based report language follows the UI language; backend comments
  and logs are now in English

### Settings & visuals
- New "Use AI for Reports" toggle (Settings → AI): unchecked = deterministic
  rule-based reports, no LLM calls (saves quota)
- Squircle (G2 continuous-curvature) card corners (native on Chrome 139+,
  graceful fallback elsewhere)

### Fixes
- Frosted-glass background lost after language switch (reload re-pushes the
  wallpaper via the backdrop-ready notification)
- Settings page crashed on boolean items (local variable shadowing the global
  i18n function)
- NF fail-time red-line marker: implemented but **paused** — BeatLeader 0.9.33
  writes failTime=0 in every local .bsor

## v1.4 (2026-08-21)

### Acrylic Background
- **The wallpaper-push scheme is the production default**; scheme A (true Acrylic via undocumented API) / scheme B (DWM backdrop board) proven infeasible in practice
  —— pywebview 6.2.1 has no true window transparency (WebView2 is transparent, but the window BackColor is not — the client area is always grey), kept as experimental switches (`--acrylic-mode backdrop|acrylic`, `--acrylic-legacy`)

### Settings Page
- "Game Path" and "Path" cards merged: only the game root directory remains (replay / maps / SongCore are derived deterministically from the root); 4 schema items marked `hidden`
- **Native folder picker dialog**: new `backend/dialog.py` bridge (fixes the bug where running `python backend\host.py` as `__main__` created a duplicate `backend.host` module and the dialog was permanently unavailable); browser mode falls back to manual input
- After selection, reachability is validated automatically with prompts.

### UI / Visuals
- Replay items: removed the solid left status color bar (redundant with the background gradient), keeping only the left-to-right status gradient
- Game path validation box: removed the color band, added the same green/red background gradient as replay items
- KPI card visual polish: removed the left color band and the gradient; background back to translucent
- **Task status card integration**: progress is rendered directly on the card background — idle = grey "Idle / no background tasks"; running = grey + a translucent red/blue gradient filling left-to-right by progress, big text = the item currently being processed, small text = task name; done = back to grey + toast ("✅ Done: ×××")
- Detail page time-series card: fixed infinite height growth when clicking checkboxes (legend-height positive-feedback loop) — chart height is fixed once on entry and redrawn (viewBox matches the rendered height, no stretching); later height changes are ignored

### Bug Fixes
- Fixed the `main.py` decorator-concatenation bug and the silently lost route bug

### Other
- **Packaging slimming**: `saberlab.spec` now uses `Tree` to package the frontend (excluding node_modules 174MB and chro source src/public) — dist 381MB → 136MB, user zip 107MB → **44.6MB**

## v1.3 (2026-08-21)

- **Empty-path blocking**: when the game path is unavailable, all 5 task buttons on the overview (scan ingest / batch analysis / rescan maps / star sync / NPS) are blocked with a toast pointing to Settings; identical backend validation as a fallback (400 with a clear error); `/api/status` adds `maps_dir.exists`
- **Restart button + instant effect**: Settings adds a "Restart SABER LAB" button (`/api/restart` → dialog bridge → host spawns a new process after a 2s delay and exits gracefully; restarts the exe when frozen); after saving settings the runtime config is **hot-reloaded** (resolver/pipeline paths take effect immediately, no restart)
- **Lazy cover repair**: `/api/maps/{hash}/cover` triggers a targeted `ensure_map_path` scan when the DB row path is missing/invalid (fixes the packaged-build instability where covers were all default after the first ingest and only recovered after a restart; with negative caching + 30s debounce, fake hashes never trigger repeated full scans)
- **NPS overwrite bug fixed**: `upsert_map`'s `nps_json` default changed from `"{}"` to `None` — `map_scan` no longer overwrites computed NPS with an empty object when rescanning the map library (NPS used to be wiped in the one-click-refresh parallel scenario, showing "-" on the detail page); verified: after recompute 914→1023 maps have values and they survive a rescan; detail-page NPS/stars/PP chain verified (7.82 / 7.28★ / ranked ✓)
- **Task dependency race fixed**: with the 5 parallel one-click-refresh tasks, `batch` / `ranked_update` read the replays table immediately at startup — after clearing data, ingest wasn't done, star sync collected an empty hash list (0 items) and batch analysis missed entries.
  Added `_wait_ingest_done(kind)`: these two tasks wait for the ingest in the same group to finish before reading data
  (verified end-to-end after clearing: 679 leaderboards + 50 pp cache ✓; also fixed a `NameError` from a missing `import time` in main.py)
- **NPS no-op race fixed**: on a fresh DB / after clearing, one-click refresh ran `nps_update` before `map_scan` created the maps rows, completing 0/0 with no work (NPS entirely missing; reproduced on the user-edition first run). Added `_wait_map_scan_done(kind)`:
  nps_update waits for the map scan to finish first (verified on a fresh DB: 1027/1027, nps_json 1023 rows ✓)
- **0.00-star forced override**: ScoreSaber writes stars=0 / ranked=0 for unranked leaderboards —
  the enrichment layer now uniformly converts 0/None stars to unranked (stars=None, ranked=False),
  with a `> 0` double check on the frontend list/compare pages; 0.00 stars no longer appear, always "-"/UNRANKED
- **Task group cleanup**: `_start_task` removes finished old tasks before starting new ones — `/api/status`'s `tasks` array only keeps the currently active group
- **Automatic player ID**: the player's 17-digit platform ID is extracted from the BSOR replay automatically; **filters out "Noob"** (the likely default fallback username when not logged into ScoreSaber); PP filling is skipped when there is no ID (no 404 from an empty ID)
- **Button refactor + multi-task parallelism**: the 5 separate overview buttons reduced to 2 — ① **「⚡ One-click Refresh」** (`/api/refresh/all`): runs all 5 tasks in parallel (ingest / batch analysis / map scan / NPS / online stars), processing only new/changed data locally (sha256 dedup + pending filter); ② **「Refresh Online Data」** (`/api/refresh/online`): force-refreshes cloud stars/PP only (local analysis data untouched, for cloud-side data adjustments). Backend task model: single task slot → **multi-task dict** (keyed by kind; same-kind conflicts return 409, different kinds run in parallel); `/api/status` returns the `tasks` array; the KPI task card distinguishes progress intelligently: **single task = task-detail mode** (big text = item currently processed, small text = task name, background = in-task done/total, e.g. online update 0/200).
  **The old progress bar component is removed** (progress display is fully handled by the KPI card); a loading animation was added next to the task status card title.

### Online Stability
- **Offline interception**: new `/api/network/check` (4s timeout probe of scoresaber.com) — a pre-check before clicking "Refresh Online Data"; when offline a toast「当前未联网」(not connected) is shown and the click is blocked, no more idle background tasks
- **Failure retry rounds**: `sync_maps_batch` single round → multi-round queue — failed maps are re-queued and re-synced after each round (transient rate-limit/network errors recover automatically); a map is given up after >=3 cumulative failures and its name is recorded
- **Failure list toast**: the completion toast shows the given-up items (「联网获取数据成功，失败项目：xxx」/ "Online sync succeeded, failed items: xxx", up to 3 listed)

## v1.2 (2026-08-20)

- **Standalone window**: `backend/host.py` (port detection / single instance / uvicorn thread / pywebview window / exit) + `run.bat` / `run-browser.bat` dual modes; fully bundled PyInstaller packaging (`packaging/`, ~345MB including a 66MB offline environment package)
- **Architecture cleanup**: schema migrations consolidated (fresh DBs get the full schema, old DBs upgrade idempotently, `tests/test_db_schema.py` regression); `_enrich_replays` extracted to the cached `services/enrichment.py`; /api/status switched to COUNT(*); ScoreSaber network failures no longer poison the cache; corrupt config.yaml auto-backup; lock-free task status read fixed
- **Dead-code cleanup**: backend dead code; frontend dead CSS/elements + 7 alerts replaced by toasts; chro diagnostic logs and orphan workers removed; unused dependencies removed
- **New button**: overview page adds a "Recompute Map NPS" button (linked to the unified progress bar)
- **Star/PP sync speedup**: persistent per-thread HTTPS connections + 8-thread concurrency + 429 backoff retry; full 217 maps in 130s (previously tens of hours)

## v1.1 (2026-08-19)

- **Layered analysis strategy**: metadata snapshot (ingest) → lazy detail analysis → background precompute; `analysis_status` state machine
- **Completion three-state completed**: decided at ingest time (exit / fail / normal clear)
- **Detail charts**: time-series independent normalization + real-range legend + hover crosshair value box
- **Optimized cache clearing**: takes effect immediately + global data refresh
- **ChroViewer port**: core extraction → integration into the original shell + SaberLab local data source → fixed the freeze bug

## v1.0 (2026-08-18)

- **Core engine**: BSOR v1 parser; map hash parsing (SongCore algorithm + SongHashData.dat cache); deterministic metrics (Accuracy / Pre-Center-Post / windows / kinematics / fatigue / single-hand direction changes); AI Coach (DeepSeek / Qwen / OpenAI compatible + rule fallback)
- **Completion judgment**: `-exit-` / NF / duration fallback; star ratings cached rooted at local maps (four-tier coloring); NPS (v2/v3 compatible)
- **UI v2**: NavigationView + Beon neon wordmark; KPI row, per-day pagination, wide multi-column; detail slider pagination, 2×3 grid; skeleton screens + race protection; HERO gradient, cover fallback, hover zoom
- **Data fixes**: hand-motion warm-up trimming + physical upper-bound filtering; speed_peak backfill; three-tier cover fallback
- **Ops**: run.bat CRLF fix; database migrations v1–v5; background tasks (batch analysis / star sync / NPS)
