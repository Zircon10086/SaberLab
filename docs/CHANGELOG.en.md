# Changelog

> Version format: `vX.Y (date)` — change summary. Parts of this document were described with AI assistance.

## v2.0.0 (2026-08-23, pending release)

> Version note: this release was originally planned as v1.6.0; it was bumped to
> v2.0.0 because of the **plugin system**, an architecture-level change that
> affects the directory layout, detection mechanism, packaging and license
> boundaries.

### Architecture: plugin system (plugins directory detection & loading)
- **First-party plugin system added**: the root `plugins/` directory is detected
  and loaded by convention — projects under different licenses or other complete
  features can be shipped as plugins placed into `plugins/<name>/`, detected at
  startup and integrated into the main flow (current mechanism: a directory with
  an entry file is mounted/enabled). First-party only for now; **no third-party
  plugin interface/specification**.
- **First plugin = 3D replay (Local-ChroViewer)**: moved to an **independent
  project** (`Local-ChroViewer/`, GPL-2.0-only, ChroViewer port); the SaberLab
  repo no longer contains its source. Its build output loaded as a plugin from
  `plugins/chro/` (the only detection path, no fallback — removing it disables
  the viewer); releases that bundle it do so as separate works (mere
  aggregation) and declare the external GPL-2.0 component
- **Missing-plugin hint**: the detail-page "Replay" pane shows a grey install
  hint (zh/en/ja, pointing at `plugins/chro/`) when the plugin is absent;
  everything else keeps working

### Dual-platform cloud data (ScoreSaber | BeatLeader)
- **Data-source switch**: Settings → Player → "Cloud Data Source" card
  (segmented control; saves and reloads immediately); `player.data_source`
  config item (schema-driven)
- **Shared player ID**: both platforms use the ScoreSaber ID auto-parsed from
  BSOR replays (= Steam ID); no manual input
- **Data isolation**: player profile / map stars / stars-pp index / personal
  palette caches are all platform-scoped (`platform` column + composite PKs);
  switching keeps the other platform's cached data untouched — switch back and
  forth freely; legacy DBs migrate automatically (old rows marked
  `scoresaber`, nothing lost)
- **BeatLeader client** (`backend/beatleader.py`): profile/scores/full
  per-map difficulty stars in one request; ranked = difficulty.status==3;
  official OST maps (status 5/7) **show stars but never produce PP**
- **Cloud data page**: nav renamed to "Cloud Data" (former ScoreSaber entry),
  fetches and displays the ACTIVE platform (profile + recent scores + dynamic
  level band); cross-validation is ScoreSaber-only; the "Fetch Data & Compute
  Dynamic Level" button is shared
- **One-click refresh / ranked update route by platform**; the personal
  palette is cached per platform (yellow baseline and list colors follow the
  active source)

### Cut details (SliceDetails port)
- The "Judgments" card became **"Cut Details"**: a 4×3 note grid (12 tiles) of
  average scores; clicking a tile expands left/right **9-compass grids** in place
  (grid shrinks to the top, selected tile highlighted)
- **Pure-Python reimplementation** (`backend/analysis/slicedetails.py`):
  direction mapping, circular angle means, effective denominators for
  slider/burst special notes, exclusion of out-of-grid/non-standard events; no
  DB writes — live parsing of the original `.bsor` (~19 ms)
- **Signed cut offsets**: note center reconstruction (x/y grid formula +
  z = cut-point z; self-consistent with BSOR cutDistanceToCenter to ~6 mm and
  cross-validated against the SimSaber reverse-engineered motion model to
  <1 mm / a few mm)
- **Cut-trajectory visualization**: solid line = actual cut path, dashed line =
  center reference (perfect path); their separation = signed offset. Note art
  uses hand-drawn slicenote.svg / slicenote-any.svg (tinted by hand, rotated
  with the direction for diagonal notes, dot variant for Any)
- Score rows under each cell (avg score / note count); hover shows
  pre/post/acc/offset details

### Detail & overview UI
- Hero KPIs extended: GOOD / MISS-BAD (same logic as list rows) / NOTE moved in,
  with a vertical divider between NOTE and NPS; BOMB hidden
- Timeline charts hide the fixed 0-100% y-axis ticks (grid lines, true-range
  legends and hover values kept)
- Overview pagination moved into the title row (same height as mode toggle /
  search, centered); page refreshes fade items in one by one
- Grid/compass animation system: centered shrink/grow (width +
  align-self:center), compass container `0fr→1fr` height transition (monotonic
  card height, no gap), pure-opacity fade-in delayed to align with the grid
  animation (fades in place, independent of the equal-height layout)

### Settings & visual
- **Configurable star palette**: new "Star Palette" dropdown under Settings →
  Player (schema-driven enum, localized option names). Tier definitions are
  shipped by the backend via `/api/status` (`ui.star_palette` +
  `ui.star_palettes`) and the frontend only applies them (falls back to the old
  4 tiers when absent). The default "Community" palette has 5 tiers: <3 grey /
  3–5 green / 5–7 yellow / 7–9 red / 9★+ purple. Palettes for different player
  abilities can be added by extending the backend `STAR_PALETTES` only.
- **Personal dynamic palette** (`player.star_palette = personal`): the yellow
  baseline is computed from the player's own ScoreSaber records (valid
  records sorted by PP descending, top 20 → Q25/Q50 mean, rounded to 0.25;
  time deliberately ignored to exclude short-term form swings; NF excluded);
  colors mean the map difficulty relative to that player's level
  (grey/green/yellow/red/purple, ±0.5 / ±1.5 boundaries). The ScoreSaber page's
  "Fetch Data & Compute Dynamic Level" button pulls scores and computes the
  palette in one step; the result is cached locally (works offline; without a
  cache it falls back to Community). The player info card shows the current
  average level in yellow plus a five-color band (each segment labeled with
  its range). Algorithm spec: `docs/STAR_PALETTE_ALGORITHM.md`. The player ID
  is parsed from BSOR replays only (`player.scoresaber_id` config is now
  deprecated and no longer read).

### Data research
- **SimSaber cross-validation** (MIT): three-way note position verification
  (x identical / y mm-level / cutDistance 0.1 mm self-consistent) + scoring
  reconciliation (official port matches stored scores per note)
- Tooling: `_tools/start_headless_edge.ps1` (CDP instance),
  `_tmp/verify_v160.mjs` (37+ assertion UI regression)

### Tests
- New `tests/test_slice_details.py` (23 cases: tile/direction mapping, circular
  means, special notes, signed offsets, exclusions); 128 unit tests pass

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
