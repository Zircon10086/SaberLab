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
- **When adding a config item, decide first whether it is an *analysis
  parameter***: changing any `analysis.*` key calls `reset_analysis_cache()`
  (metrics/motion_series wiped, every replay back to pending). Pure UI
  preferences (grouping thresholds, display toggles, ...) must live in another
  namespace such as `ui.*` — in 2026-09 the overview's session-gap threshold was
  placed under `analysis.*` and a single tweak wiped the whole library's analysis
  data.
- Key items: `ai.ai_report_enabled` ("Use AI for Reports" — unchecked short-circuits `run_ai_report` to the rule report, no LLM calls); `analysis.slope_group_notes` (note-group size); `analysis.session_gap_minutes` (removed; see `ui.session_gap_minutes` below); `ui.session_gap_minutes` (gap threshold for the overview's "By Session" paging, default 60 minutes: two neighbouring replays further apart than this start a new session; `load_config` clamps out-of-range values to [1,1440] so a broken config.yaml cannot merge the whole library into one session. **It must stay under `ui.*`, never `analysis.*`** — a change there resets the analysis cache, while this setting takes part in no analysis at all); `analysis.window_seconds/window_step_seconds` (deprecated, hidden, kept for compatibility)

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

### 5.7 Dual-platform cloud data (scoresaber | beatleader, 2026-08)
- **Switching the source**: Settings -> Player -> "Cloud data source" card (a
  segmented control backed by the `player.data_source` config item, schema-driven);
  clicking saves and reloads the page
- **One ID for both platforms**: the ScoreSaber ID (= Steam ID, 17 digits) parsed
  automatically from BSOR replays; no manual input
- **Data isolation**: `scoresaber_cache` (profile + scores),
  `scoresaber_leaderboards` (map stars), `map_ranked_cache` (stars/pp index) and
  `player_palette_cache` (personal palette) all carry a `platform` column (part of
  the PK); switching platforms leaves the other platform's data completely
  untouched, and old rows are migrated as `scoresaber` (idempotent rebuild in
  `repository._migrate`)
- **enrichment** reads leaderboards/ranked_cache for the active platform (snapshots
  are per platform), so lists / detail / history / star colours all follow along;
  **ranked_update / Quick Refresh** route by platform
  (`scoresaber.sync_maps_batch` vs `beatleader.sync_maps_batch`; BeatLeader uses
  `/leaderboards/hash/{hash}` to fetch every difficulty at once, ranked =
  `difficulty.status == 3`, and official OST maps (status 5/7) **show stars but
  never produce PP** — a user decision)
- **Quick Refresh cloud semantics**: still 5 task groups; after the
  leaderboard/player-PP index finishes, `ranked_update` serially calls
  `_cloud_page_refresh(active_platform)` to refresh the player profile, recent
  scores and dynamic level. A manual POST from the cloud page shows a success
  toast in all three languages
- **Cloud Data page**: navigation entry (formerly the ScoreSaber page) calls
  `/api/scoresaber|/api/beatleader` for the active platform (GET = cache, POST =
  fetch and compute dynamic level); the cross-validation button is ScoreSaber-only
- **Per-platform personal baselines**: each platform runs its own skill model (star
  ratings are never mixed), cached per platform
- BeatLeader API client: `backend/beatleader.py` (fields aligned with scoresaber,
  including the `accuracy` / `base_score` the skill model needs; see the module docstring)

### 5.8 PP prediction (accuracy preview, v2.1.0)
- Formula: `pp = maxPP * curve(acc)` (a black-box replication of ScoreSaber's
  official pp-curve). The curve is embedded in `backend/analysis/pp_predict.py`
  (37-piecewise-linear nodes, acc=0.95 -> multiplier 1.0; source and fetch date in
  the module docstring) — **offline and deterministic, never fetched at runtime**
- maxPP source: `scoresaber_leaderboards.max_pp` (written by the leaderboard-info
  sync). Semantics = the PP at 95% accuracy (H1 "PP at 100% accuracy" was refuted
  by local data, off by ~5.4x; H2 matched clean samples within +-0.1%, evidence
  scripts `_tmp/verify_pp_curve.py` + `_tmp/pp_curve.json`)
- Endpoint `GET /api/replays/{id}/pp-preview`: scoresaber platform only (the BL
  formula differs -> 400); leaderboard selection reuses
  `enrichment.pick_leaderboard` (same `_lb_better` tiebreak as snapshot folding);
  the preview default is always the replay's own displayed acc (NF/exit are not
  halved and do not jump to 60%; below 60% the slider lower bound expands);
  unranked / missing max_pp -> 404
- Frontend: ranked rows in the replay list get `.pp-click` on the PP cell
  (ranked + ScoreSaber platform only); `openPpPreview` uses the generic anchored
  popover `openPopover` (app.js, `closePopover` is idempotent): opens below the
  trigger, flips above when there is no room, clamped at the left/right edges, no
  overlay and no interaction blocking, closes on outside click / Escape / scroll /
  zoom, same-cell click toggles; enter/exit animations `popIn/popOut` with a
  `prefers-reduced-motion` fallback. The full-screen `openModal` is a **separate
  global attention interface** (top-level overlay; the slice-details view moved
  into an in-page card, the two are unrelated). Curve nodes come from the backend;
  the frontend only interpolates linearly between them (the formula itself lives
  in the backend); i18n keys `replay.pp_*` + `err.pp_*`
- **Data semantics**: `map_ranked_cache.pp` is the player's **cloud best** for that
  difficulty (top-100 sync) and must never be pasted onto every local replay of the
  same map; ScoreSaber enrichment computes per-play PP as
  `leaderboard max_pp * curve(local acc)` (for NF, the earned PP is additionally
  scaled by `score_effective/score`; exit is left empty). PP preview uses maxPP
  only; `replay_pp` is a cloud-best reference field. BeatLeader keeps the existing
  cached display and gets no prediction
- Pitfalls: the slider value is a percentage (60-100) — display it raw and divide by
  100 only when looking up the curve (double division once showed acc as 0.78%);
  the popover's outside-click listener must be bound via `setTimeout(0)` (otherwise
  the very click that opened it closes it immediately)
- **Popover material (fixed 2026-08)**: `rgba(20,22,30,0.66)` (the `body.acrylic`
  `--surface` value, uniform across modes — do not use `var(--surface)`, which is an
  opaque grey block in browser mode) + `backdrop-filter: blur(14px) saturate(150%)`
  (same as the header); `z-index` 200 (above header/sidebar, below toast at 1000)

### 5.9 Local replay retention (why replays disappear)
- **The mod, not SaberLab, deletes replays**: the BeatLeader mod's replay setting
  "keep latest only" (config key `OverrideOldReplays` in
  `UserData/BeatLeader.json`, **on by default**) **deletes older .bsor files of
  the same map & difficulty whenever it saves a new replay**. Evidence: the game
  log states `[WARNING] OverrideOldReplays is enabled, old replays will be deleted`
  and `[INFO] Deleting old replay: <file>`. Measured (2026-09-11, all
  `Logs/*.log(.gz)`): 217 deletions, 213 distinct files, spanning 2025-12-13 to
  2026-09-07 — of which **78% (170/217) were `-exit-` replays**. It mirrors
  BeatLeader's server-side policy (the site also keeps only the latest replay per
  leaderboard), which is why "the replay is not on the website either".
  Full evidence chain: HANDOFF §4.25; re-runnable statistics:
  `_tmp/_verify_bl_count.py` (must decompress `.gz`, must not regex across lines).
- Consequences and current handling:
  - **LocalLeaderboard as a second read-only source** (`game.local_leaderboard_dir`,
    derived `UserData/LocalLeaderboard/Replays`, auto-enabled when present): it
    keeps one copy per session, so one-click refresh repairs a missing row's
    `file_path` to the surviving twin. Same-session dedup key = player + map hash +
    10-digit timestamp; LL-only sessions are ingested normally.
  - **exit replays have no LocalLeaderboard copy** → they were unrecoverable, and
    rows whose file is gone show the explicit "file missing" degradation (detail
    banner + badges, see §4.26①) instead of generic "no data".
  - **The behaviour is disabled in-game now** (setting off since 2026-09-11), so
    exit replays are finally retained. Do not assume a missing file means SaberLab
    lost it: check the game log first.
- **The Game Path card checks that setting in place** (2026-09): after the folder
  checks, `service.check_paths()` appends a `replay_retention` row
  (`beatleader_replay_deletion_check()` reads `OverrideOldReplays` from
  `<root>/UserData/BeatLeader.json`). Key points:
  - **Three-state `status`**: `ok` (green check, disabled) / `bad` (red cross,
    enabled) / `note` (neutral — mod not installed, key absent, config
    unparsable, or no root entered); the frontend renders by `status` and falls
    back to `ok` when it is missing
  - **Only an explicitly enabled value counts as enabled** (`True` / `1` /
    `"true"` / `"yes"`); the string `"False"` and any unknown value do not —
    never a false warning
  - **The row never affects `valid`**: `valid` still only requires the root plus
    `CustomLevels`, because the path is fine and the change happens inside the
    game (the red row is advisory, it does not turn the badge into "invalid")
  - Text: the label uses `settings.game.retention_label`; the Chinese note
    strings are keyed by `settings.game.retention_*` in `zh-CN.json`, and en/ja
    additionally map the Chinese originals in their `err` section (the same
    mechanism used for other backend messages)

### 5.10 Energy / fail time (official algorithm ported by decompilation, 2026-09)
- **Why we compute it ourselves**: the `.bsor` `failTime` field is never written by
  the BeatLeader mod (498/498 local files are 0.0), so the fail moment has to be
  recomputed locally
- **The algorithm comes from decompilation, not guesswork**. The game logic lives in
  `Beat Saber_Data\Managed\Main.dll` (a Mono/.NET assembly, not IL2CPP, so it can be
  decompiled completely). Tools are installed globally: `dotnet` (SDK 8.0.425) plus
  `ilspycmd` (`%USERPROFILE%\.dotnet\tools`). Usage: `ilspycmd -l c <dll>` to list
  types, `-t <Type>` to decompile one type (output is UTF-16 — convert it).
  **`GameEnergyCounter` has identical constants and logic in all three versions**
  (1.39.0 / 1.40.8 / 1.44.1; 1.44 only swaps `Time.deltaTime` for
  `TimeHelper.DeltaTime` and adds obstacle analytics hooks)
- **Official constants** (recorded verbatim in the `backend/analysis/energy.py`
  module header): good +0.01, bad −0.10, miss −0.15, bomb hit −0.15, burst element
  +0.002/−0.025/−0.03, obstacle −1.3 per second; Bar starts at 0.5, Battery/instaFail
  at 1.0 (4 cells), cap 1.0; energy ≤1e-5 → 0 and the run fails; a fail is latched.
  **NF is special**: the game stops draining once NF is active, but NF is
  auto-enabled by the game at the instant a run fails — so the simulation does
  **not** suppress drain for NF (`EnergyConfig.no_fail_declared` only records it);
  doing otherwise would erase exactly the fail that caused the NF
- **Wiring**: `analysis/engine.py` calls `simulate_energy()`, the result goes into
  `summary.energy` and is persisted under the `metrics` `energy` scope (no new table).
  **`completion_status` is deliberately unchanged** — it still follows the original
  rules (filename exit / NF / recorded fail_time / duration fallback); switching it to
  the computed value is a separate product decision
- **Measured effect of the NF fix**: runs classified as having failed went from
  **1 to 174** (of the 416 replays whose source file still exists)
- **UI (2026-09)**: the detail page gained an `#d-energy` summary under the timeline
  card (lowest energy, fail time or "never depleted", drain broken down by cause,
  obstacle hits) and the timeline now draws the **fail-time red line** (no longer
  depending on the always-zero `.bsor failTime`). **Fail time is deliberately kept
  apart from `completion_status`**: finishing the chart after failing, quitting
  before failing and quitting after failing are each shown as they are — the
  frontend never guesses intent
- **Batch recompute**: `POST /api/analyze/all?force=true` re-analyzes every replay
  regardless of `analysis_status` (used to backfill history after the engine gains a
  metric, instead of wiping the whole analysis cache for a few new numbers)
- **TODO (assigned by the user, 2026-09): compute `completion_status` precisely from
  replay data.** Today it relies on the **filename** `-exit-` marker plus the NF
  modifier, the recorded fail_time and a duration fallback — and **filenames are
  unreliable** (third-party naming, renames, other mods writing the file). Direction:
  replace filename inference with reproducible facts inside the replay (end time vs
  map length, whether energy reached zero, last event time, pauses/restarts), keep
  the completion classification separate from the factual fail time, and mirror the
  change in `tests/` and docs with an old-vs-new classification comparison
- **Extra telemetry produced in the same pass** (per the requirement that anything
  derivable from the same data should be computed once and exposed): the energy event
  stream (re-playable timeline), `drain_by_reason`, `charge_by_hand`, `drain_by_hand`,
  `obstacle_hits` (wall checkpoints), `min/max/end_energy`, `time_in_danger`,
  `final_charge/total_drain`
- **Validation evidence**: all 45 "energy at the moment of entering an obstacle"
  checkpoints are reproduced by the official formula, and the implied obstacle dwell
  times are 0.0105–0.311 s (median 0.067 s, none negative). Independent criterion: in
  exit/NF replays the model reaches zero before the replay ends in 183/281 (Bar) and
  254/281 (Battery) cases
- **Known boundary**: obstacles are modelled as "entry moment + one frame of dwell"
  (the replay records only the entry, not the exit; exact integration needs map
  geometry plus the HMD trajectory and is a follow-up). **Measured error (2026-09,
  whole library, 498 files)**: only 22 replays (4.4%) carry obstacle data, 45 entries
  in total; missed drain per entry is median 0.072 / mean 0.108 / max **0.555**
  (about a whole 0.5 Bar); after re-adding the true drain **1 replay flips from
  "never failed" to "failed"** and 3 shift their fail time by >1 s (2 by >5 s, worst
  **17 s**). So the bias is negligible for ~98% of the library and severe for replays
  that really spend time inside obstacles — fixing it needs map obstacle geometry plus
  a head-vs-obstacle sweep over the HMD trajectory (frames are parsed and carry pose),
  a medium-sized increment. Burst slider elements are correctly distinguished via
  `NoteParams.scoring_type` (they do occur locally: 1279 in the first 40 replays)

### 5.11 Analysis performance: persistence path & batch parallelism (2026-09, measured)
- **Measured profile (whole library, 416 replays)**: parse 4.5 ms + analysis 16 ms +
  **persistence 59 ms** (74%) — **the bottleneck was database round-trips, not
  computation**. After the work below: parse 4.5 + analysis 16 + persistence 9.5
  ≈ 30 ms per replay
- **Three rules — read these before touching the write path**:
  1. `Repository` **reuses one connection per thread** (`_cached_conn()`). A fresh
     connection costs ~**18.7 ms** on the 100 MB local DB, reuse ~**2.2 ms** (8.7x).
     Connections are no longer closed per call, so **temporary databases/tests must
     call `repo.close()` explicitly** — otherwise Windows refuses to delete the file
     (that is exactly what `_close_repos()` in `tests/test_platform_sync.py` and
     `test_db_schema.py` exists for)
  2. A replay's whole set of writes goes through `Repository.session()` →
     **one connection, one transaction**; never open another connection inside it
     (`_ConnCtx` is a no-op there). The win is not only speed: a replay's rows become
     **atomic** and roll back together on failure
  3. **Never trigger a full map-library rescan from a batch path**.
     `MapResolver.resolve()` calls `scan()` on a DB miss by default, and one scan
     costs **13.8 s** here (1032 map folders). Batch runs use
     `ReplayPipeline(map_scan=False)` plus **one scan up front**; interactive
     single-file analysis keeps the default so a freshly downloaded map is found
- **Batch parallelism**: **processes only** (threads measured 1.04–1.24x — the hot
  loops are GIL-bound Python; 4 worker processes measured **1.95x**).
  `BATCH_WORKERS=4` (design target is a 6-core machine, leaving headroom),
  `BATCH_PARALLEL_MIN=24` (below that the pool's start-up cost does not pay off).
  On Windows spawn requires a **`__main__` guard**, and workers must avoid
  closures/local functions
- **End result (forced full recompute of all 416 replays)**: **246 s → 20.4 s
  (~12x)**, of which the upfront map scan (~14 s) is a fixed cost and the analysis
  itself went 13.4 s → 6.9 s
- **Step 3 (computation memoization / single-pass, 2026-09)**: serial analysis
  13.4 s → **9.8 s** (33 → 24.2 ms per replay, **+26.6%**), end-to-end full batch
  20.4 s → **18.7 s**. Three changes:
  1. `NoteParams.decode` **memoized** (`_PARAMS_CACHE`; the cached value is a frozen
     dataclass) plus **decoded once at parse time** into `NoteEvent._params` — a full
     analysis used to decode 40531 times
  2. `cut_scores` **memoized** (cached on `NoteEvent._cut_scores`): both the scoring
     pass and the note-group pass need it, so every good/bad note was computed twice
  3. `_group_metrics`' NoteEvent path now **counts in a single pass** instead of four
     `_attr()` calls per note (~40k function calls per analysis) — **with the metric
     expressions and names unchanged, character for character**
- **Red line**: no optimisation may change a metric value. After every change run
  `_tmp/verify_numeric_regression.py` (recompute, then compare metrics/notes row by row
  against the database, requiring zero differences) and then the full test suite.
  Step 3 measured: 12 replays, **zero differences**
- **Hot spots still unoptimised (measured, for later decisions)**:
  `scoring.cut_scores` 16 ms, `accuracy.analyze_accuracy` 14 ms (per-note loop),
  `_path_economy`'s 14 ms per-pair loop, 69245 `round()` calls (9 ms), and
  **notes/walls per-row Python decoding 16–22 ms** (that one needs notes to become a
  numpy structured array: biggest win, highest risk, a separate refactor)
- **Optional durability trade-off (not applied)**: with WAL, `synchronous=FULL` costs
  7.66 ms per commit while `NORMAL` (SQLite's own recommendation for WAL) costs
  2.99 ms. The database is fully derived and rebuildable from the `.bsor` files, but it
  is still a durability trade-off and needs its own decision

### 5.12 Sidebar player card & player asset cache (v2.2.0)
- **Data source**: the card reads `scoresaber_cache.profile_json` (the player
  profile snapshot, same one the cloud data page uses) for
  name / country / rank / countryRank; it is written by `_cloud_page_refresh`
  (cloud page "Fetch data & compute dynamic level", and the `ranked_update`
  stage of Quick Refresh). **No new table**: the snapshot already carries those
  fields, and `GET /api/player/card` just shapes them for the card
- **Avatar & flag**: `backend/services/player_assets.py` — downloaded once
  during the sync into `data/assets/` (`avatar_<platform>_<player_id>` /
  `flag_<CC>`, no extension, MIME sniffed from magic bytes) — then served by
  `GET /api/player/avatar|flag` from local disk only (URLs carry
  `?v=<file mtime>`, so they cache hard yet still bust on change).
  Design constraints: **the read path never touches the network** (works
  offline, the WebView never talks to an external domain) and **a failed
  download never breaks the sync** (the old image is kept; `sync_player_assets`
  returns `attempted/stored` so the log can tell "no source" from "failed");
  honours `network.proxy`
- **Avatar URL sources**: ScoreSaber `profilePicture` (cdn.scoresaber.com);
  BeatLeader `player.avatar` normalised to `avatarUrl` by
  `beatleader.fetch_profile` (Steam CDN). **The flag is an image, not an
  emoji**: WebView2 (Chromium/Windows) does not render regional-indicator
  glyphs, so the flag emoji degrades to the bare letters "CN" (measured
  2026-09-10); the 40px PNG from flagcdn.com is cached the same way. When
  either is missing the frontend falls back to the name initial / country code
  text — never a broken image
- Cache file paths are **derived**, never written into the profile snapshot: a
  local cache path is infrastructure state, not API payload, and deriving it
  keeps the snapshot re-fetchable
- Endpoints: `GET /api/player/card` (no snapshot → `profile: null`; the frontend
  hides the block, which is a normal state), `GET /api/player/avatar`,
  `GET /api/player/flag` (404 → frontend fallback)
- Frontend: `loadPlayerCard/renderPlayerCard` in `frontend/app.js` (loaded in
  parallel with the recent-replay list at startup; refreshed right after a cloud
  sync); markup lives in `index.html`'s `.sidebar-bottom` (player section +
  "Server running" pushed to the bottom as ONE block by `margin-top: auto` — with
  the auto margin on both, the free space is split evenly and the player section
  floats to the middle); styles `.player-card/.pc-*`
- **Two sections split by a divider (do not turn them back into a card)**: the
  bottom-left corner holds two sections (player info / server status), each
  separated by `border-top: 1px solid var(--border)` — the very line that already
  sat above `.sidebar-footer`, with matching `--space-3` side padding so both
  sections share one text left edge. The player section has **no background and
  no radius** (it was a `--surface-2` rounded card until 2026-09-10, switched to
  the divider so it matches the footer's language); the local
  `.player-card.hidden` rule is gone (the global `.hidden` already forces
  `display:none !important`, and two same-specificity rules on one element fight
  each other)
- **Adaptive rank row (do not turn it back into a fixed column)**: `.pc-ranks`
  uses `flex-wrap: wrap`, so the two ranks sit side by side and wrap at an
  **item boundary** only when their combined width exceeds the text column
  (each `.pc-rank` keeps `white-space: nowrap`, so a number never breaks
  mid-value). Measured baseline: 117px available, local values need 109.1px
  (55.3 + 45.8 + 8 gap) -> one line; synthetic long values wrap to two lines
  with no overflow either side. It was once wrongly pinned to two lines via
  `flex-direction: column` — an over-correction (`_tmp/verify_player_ranks.mjs`
  / `_wrap.mjs` re-measure both criteria)

### 5.13 ACC-weighted player skill model (2026-09)
- **The specs are the product definition; the code follows them**:
  `docs/ACC_WEIGHTED_SKILL_MODEL.md` (model + parameters) and
  `docs/ACC_WEIGHTED_SKILL_MODEL_VALIDATION.md` (direct-evidence gate, lower bounds,
  "insufficient data"). Pure functions in `backend/analysis/skill_model.py`
  (deterministic, no network, no LLM, no UI). **Read those two documents before
  changing a parameter** — they are a specification, not a suggestion.
- **Three baselines**: `personal96` (capability at 96% accuracy), `personal94`
  (regular), `personal80` (challenge). One physical model and one official PP curve;
  they differ only in target accuracy, accepted evidence and transfer limit.
- **Where the accuracy comes from** (easy to get wrong): the model needs the accuracy
  ScoreSaber itself scored the play at — `baseScore / leaderboard.maxScore`, **not**
  `modifiedScore`. `scoresaber.fetch_scores` therefore keeps `base_score` /
  `max_score`; BeatLeader reports `accuracy` directly (a percentage, divided by 100).
  A local replay's `accuracy` is the same quantity (verified against the pp curve over
  62 samples: median deviation 4e-6, max 1e-3), so the model also works offline.
- **The ±2.0 transfer gate is the core mechanism**: a play contributes a number to a
  track only when `|(u(acc) - u(target)) / lambda| <= 2.0`; farther plays are reported
  as an observed lower bound instead. At lambda=0.09 each track accepts a narrow band
  around its target: 96% ~ 93.1-97.2%, 94% ~ 87.8-96.3%, 80% ~ 80.0-89.8%
  (`tests/test_skill_model.py` pins those three bands). **Do not re-derive them** — a
  mistaken derivation once looked like a contradiction with the spec while the
  implementation was correct.
- **"Insufficient data" is a legitimate result**: a track with fewer than 8 direct
  records has no value; the UI shows "数据不足" and the settings option is disabled
  (`option_meta` ships via `/api/status` and `/api/settings/schema`). Never fall back
  to another algorithm just to fill all three tracks.
- **Data flow**: Cloud Data page "fetch data & compute dynamic level" -> score fetch
  (ScoreSaber merges recent+top, capped by `CLOUD_SCORE_LIMIT=300`; BeatLeader pages by
  date) -> `skill_model.rate_player()` -> `player_palette_cache` -> `/api/status`
  injects the selectable palettes -> frontend `starColor` tiers. The colour anchor is
  the selected track; `build_tiers()` remains the only tier implementation.
- **Naming (user decision 2026-09-14)**: public ids are always
  `personal80` / `personal94` / `personal96` (config enum, API payloads, i18n,
  `option_meta`), leaving room for other target accuracies. The **cache columns stay
  short** (`r80`/`r94`/`r96`) — a storage detail, and the live database already has
  them — so the translation lives in exactly one place in `backend/main.py`
  (`TRACK_CACHE_COLUMNS` / `_track_column()`). `_palette_public_payload()` also maps a
  legacy `method='r80'` back to the public id, otherwise an old cache row could not say
  which baseline is in effect.
- **The old `classify_player` is disabled but kept** (the module docstring says
  DEPRECATED): the runtime no longer calls it and its tests still pass; it will be
  deleted once the new feature has settled (user decision).
- **Calibration**: `_tools/calibrate_skill_model.py`. It reads the cloud cache by
  default (the same evidence the live model uses; `--source replays` switches to local
  replays), splits 80/20 by time, and reports per-track coverage / MAE / P90 error /
  bias / curve clamps / three-track monotonicity, plus a baseline that always predicts
  the mean accuracy; `--fit` sweeps lambda (0.03-0.20) against the gate. **Measured on
  this machine: too few samples (8 validation plays), so the fit is not usable** —
  turning the shipped parameters from template values into data-backed ones needs more
  players' histories (`--json` accepts an external record file). **Never apply a sweep
  winner directly**: the spec requires it to win consistently across players and splits.
- **Verification**: `_tmp/verify_skill_palette_ui.py` (asserts the rendered strings, not
  just presence — it caught a key rename that had been missed),
  `_tmp/verify_frozen_package.py` (release artifact on the frozen build),
  `_tools/cdp.py` (standard-library CDP client for Python-side UI checks).

## 6. Frontend Notes

- Zero-dependency single files: `index.html` (static skeleton + settings/detail templates), `app.js` (render + interaction), `style.css`
- Dynamic settings form: driven by `/api/settings/schema` (`renderSettingsForm`, `hidden` items skipped)
- **Enum items (`type: "enum"`) do not use a native `<select>`** (2026-09): a native
  dropdown's popup list is drawn by the OS and cannot be styled, so it is a "glass
  trigger button `.enum-trigger` + `openEnumPopover()` reusing the generic
  `openPopover` list" — same material / enter-exit animation / flip-and-clamp
  anchoring / dismissal paths as the PP prediction popover. **The value lives in a
  hidden `<input data-key>`**, so `collectSettings()`'s generic read, the
  dirty-field detection and the backend save path need no changes at all;
  `settingsEnums` holds the {value,label} pairs of the current render, and the
  popover gained list-variant styles `.popover-list/.popover-opt`
  (`.popover:has(.popover-list)` only overrides padding/radius, so the PP preview
  and other popovers are unaffected). **Toggle semantics**: `enumOpenId` remembers
  which dropdown is open, so clicking the same trigger again calls `closePopover()`
  instead of replaying the enter animation; it is reset in `onClose`, so the control
  can be reopened after an outside click / Escape / scroll. Keep that guard when
  touching this code — without it the control "pops open on every click"
- Charts: `lineChart` (SVG + crosshair hover); detail-page equal-height logic — `fixDetailLayout()` fixes chart heights once on entry (do not revert to `height: auto` live calculation; it triggers a positive-feedback height loop)
- Acrylic layer: `#acrylic-backdrop` (fixed inset 0 + blur); the `.moving` class maxes the blur while moving

### 6.1 Context menu framework (2026-09)
- **Goal**: adding a right-click feature means adding one entry to the registry — **no
  framework changes**. Everything lives in `frontend/app.js`:
  - `CTX_MENUS`: the registry, each entry `{ match: selector, items(el) -> items[] }`
  - `bindContextMenus()`: a **single document-level delegation** (called from `init()`
    after `I18N.init`, because labels need `t()`). It matches with `closest`
    **inner-to-outer**, first hit wins
  - `openContextMenu(items, x, y)`: render + position + click dispatch
  - `buildContextMenuHtml()` / `ctxMenuItem()` / `ctxSeparator()`: item construction
- **Reuses the existing overlay instead of re-implementing one**: `openPopover` gained
  `at: {x, y}` (viewport coordinates, taking precedence over `anchor`), `className` and
  `blockContextMenu`. The menu therefore inherits the acrylic material, enter/exit
  animations, Escape, outside-click and scroll/resize dismissal. `blockContextMenu`
  makes a right-click elsewhere close the old menu first (capture-phase
  `contextmenu` listener), so menus never stack
- **Items**: `{ label, action(ev, el), danger?, disabled? }` or `ctxSeparator()`.
  The registry entry supplies the label via `t()` (the framework only applies
  `escHtml` as a safety net); `danger` renders red (destructive actions), `disabled`
  greys out and does nothing. **The framework closes the menu before running the
  action**, and an action that throws reports through a toast
- **When the native menu is allowed through**: (1) the target is inside
  `input/textarea/[contenteditable]` (text copy/paste is essential), or (2) a registry
  entry matched but `items()` returned no entries
- **Clipboard**: `copyText(text, okKey)` handles it with a three-step fallback
  (Clipboard API → retry after focusing → hidden textarea + execCommand).
  **`writeText` fails while the document is unfocused** (that is what happens when the
  window is in the background), hence the focus retry; an empty value reports
  `ctx.copy_empty` instead of failing silently
- **Registered: `.replay-item`** (2026-09, specified by the user — implemented):
  **Open details · Attempts on this map · —— · Show in folder · Delete replay file**
  - **Attempts on this map** fills in the **song name** (`data-song`). The history search
    (`histScore`) only matches song names and the 5-character beatmap_key, so
    **a map_hash never matches** — an early version did exactly that; do not go back
  - Both file actions are disabled from `data-file-available` (derived from the backend's
    `file_available`)
  - **Delete replay**: `POST /api/replays/{id}/recycle` does two steps, in an order that
    must not be swapped: (1) the file goes to the recycle bin via
    `Microsoft.VisualBasic.FileIO.FileSystem.DeleteFile(..., SendToRecycleBin)` (**never a
    hard delete**; the path travels base64-encoded so quotes/CJK survive the command
    line); (2) `Repository.delete_replay()` removes the `replays` row and all derived data
    (notes/metrics/windows/motion_series/accuracy_curve/ai_reports) in **one
    transaction**, nulling `experiments` baseline/candidate references. **The record is
    removed only after the file reached the recycle bin**, so a mid-way failure can never
    leave "record gone but file still there"; a missing file still allows cleaning up the
    record. The in-memory `_energy_curve_cache` entry is dropped too
  - **`explorer /select` must be two separate argv entries**:
    `["explorer.exe", "/select,", path]`. As a single argument (`"/select," + path`)
    explorer rejects the switch and **falls back to opening the Documents folder** (bug
    reported by the user, 2026-09). This is easy to break while "tidying up" code — read
    this before changing it
  - `POST /api/replays/{id}/reveal` runs `explorer /select,<file>` and falls back to the
    containing folder when the file is gone (**reveal never touches the database**;
    `list` dedupes by `file_path`, which is why the old "remove the record only" behaviour
    would re-ingest the file on the next scan — now the file is already in the recycle bin
    by then, so it cannot come back)
  - The dialog reuses `openModal({ actions })` (new button row); **clicking an action does
    not auto-close** — the caller closes it after the request succeeds and leaves it open
    with the button re-enabled on failure, so the UI can never claim a delete that did not
    happen
- To add a scenario: give the element a class (or `data-*` carrying what the items need)
  and add one entry to `CTX_MENUS`. **Note**: the early `[data-copy]` fallback and
  `copyText()` were removed together with "Copy replay ID" (no callers = dead code);
  re-introduce them when a menu item actually needs copying
- **Acceptance scripts** (DOM-level, no eyeballing): `_tmp/verify_ctx_menu.mjs`,
  `_tmp/verify_ctx_features.mjs` (menu contents, song-name search, delete confirmation,
  disabled items for missing files), `_tmp/verify_delete_flow.mjs` (success and failure UI
  paths with `fetch` stubbed, **deleting nothing**), `_tmp/verify_recycle_endpoint.py` +
  `_tmp/verify_recycle_bin_fs.py` (real delete → verify the recycle bin's `$I*` metadata
  records the original path and full content size) and `_tmp/verify_popover_regression.mjs`
  - **Test-environment notes (both bitten us)**: (1) in headless CDP,
    `Input.dispatchMouseEvent` **silently drops events while the page is unfocused**, which
    makes right-clicking look flaky — the acceptance script now dispatches a page-level
    `MouseEvent` instead (deterministic), with the real-mouse path verified separately;
    (2) a fixed `sleep` waiting for app init fails on slow starts — **poll for the list
    item to appear** instead

### 6.1b Search & paging at library scale (2026-09 measurements + stress-test TODO)
- **History paging**: reuses the overview's pagination component
  (`renderPagination(total, page, pages, unit, onPage, el)`; the history page passes
  `unit="count"`, container `#history-pagination`, and injects `onPage`), **count-based
  only, 300 rows per page**; changing the query or the date range returns to page 1
- **Paging position**: the **same three-column symmetric grid as the overview**
  (`#tab-history .surface-title` copies `#tab-overview .surface-title`: filters left,
  paging centre, empty right) → the control sits on top and is strictly centred (measured
  0 px offset); the history content lives inside a `.surface` and the filters moved into
  `.title-left`
- **Fetch strategy**: **both the default view and searches use
  `HIST_SEARCH_LIMIT = 10000`** (frontend constant) and then filter + page locally — the
  default view must be pageable too (showing 300 rows with no way to page is simply wrong).
  `/api/history`'s `limit` ceiling was raised to **50000** (was 2000)
- **Why 10000 (measured)**:
  | Item | Measured |
  |---|---|
  | Server fetch | ~**1083 bytes/row** (424 rows = 448 KB / **23 ms**) |
  | Client filter+sort on 6000 rows | **2 ms** |
  | Rendering one 300-row page | **13 ms** (9230 DOM nodes) |
  | Hits lost with the old 2000 cap on a 6000-row library | **67% missed** (4527 hits, only 1512 found) |
  Extrapolated: 6000 rows ≈ 6.3 MB, 10800 rows ≈ 11.4 MB. **The fetch cap is the only
  real constraint — not filtering, not rendering**
- **⚠️ TODO (requested by the user, not yet done): extreme-scale stress test** — blow the
  local library up to **10800 replays** (~25x today's 424) and verify every SaberLab
  feature still works: ingest/scan, batch analysis (4-process persistence), overview
  paging by day/session, history search and paging, detail page, timeline, compare, AI
  reports, cloud sync, plus **database size and query time** (`data/saberlab.sqlite` is
  ~100 MB at 424 replays; re-evaluate indexes and paged queries after scaling).
  **Suggested method**: copy the real DB to a temp path and scale it by cloning rows
  (`replays` + derived tables together, pointing `file_path` at real files or marking them
  missing), run it in a **separate process/port** so the user's DB is never touched, and
  delete the temp DB afterwards
- Search matches **song name + the 5-character beatmap_key only**, **never the player
  name**: one machine usually has one player, and the pre-login default name (e.g. `Noob`)
  is the same person (measured: 56 shared maps), so filtering by player name would only
  make one person's records hide from each other

### 6.2 i18n (multilingual)
- **Two mapping conventions (must be changed in pairs, or they fail silently)**
  1. **Keys in the `err`/`msg`/`task.current` sections are the backend's original
     message text** (the zh table has none of these sections); `tErr()/tMsg()` look them
     up. **Changing backend wording requires updating the en/ja keys with it**, otherwise
     the lookup falls back to the original text — no error, the translation just quietly
     stops applying
  2. **Dotted frontend keys** (`t("err.offline")`, `data-i18n`) must exist in all three
     tables; when missing the UI **renders the key itself**
  - Trap: long backend messages are often written as **implicit concatenation across two
    lines**, so searching/replacing the full string **will not find them** (that is exactly
    how one backend message was missed when its wording changed)
- **Regression test `tests/test_i18n_mapping.py`** (5 cases) covers both conventions:
  backend Chinese literals are extracted via AST (handling implicit concatenation and
  f-string placeholders) and template-matched, plus checks that referenced keys exist,
  that en/ja cover the zh baseline, and that the three sections keep equal sizes

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

### 6.3 AI report language
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

## 8. Build & Release Conventions (2026-08 user decision, mandatory)

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
4. **Every version must ship a RELEASE_NOTES.md**: after each packaged export
   (`GitHub_Build\<version>\` produced by `_tools/export_github_pkg.ps1`), write
   `GitHub_Build\<version>\RELEASE_NOTES.md` — a concise bilingual user-facing
   change log (headline + What's in this version + Quick Start + Full Changelog
   links, following the previously published release notes). It is published
   with the GitHub Release; never skip or backfill it.
   **Note**: the exporter first *empties* `GitHub_Build\<version>\`, so this file must be
   written **after** the export.
5. **Release packages carry no secrets (2026-09 user decision + incident review)**:
   - `packaging\saberlab.spec` **does not embed** `config/config.yaml` or `.env`. While it
     did, the frozen app read the author's file out of PyInstaller's `_MEIPASS`, so a local
     build kept the author's port/paths and any secret inside it could reach a release.
     A missing file is safe: `load_config()` starts from defaults.
   - The user zip ships only an empty `.env.example` (verified 2026-09-14); the app writes
     the real `.env` when a key is saved in the settings page.
   - The withdrawn placeholder scheme is **recorded as a decision, not implemented**
     (`PLACEHOLDER_API_KEY` / `is_placeholder_secret()` / `_tools/check_release_secrets.py`
     do not exist): shipping a placeholder `.env` would only mask a symptom, and the actual
     cause turned out to be the ambient environment variable.
   - **The AI key is read from the local config file only (2026-09-14 user decision,
     superseding the earlier "show the key's source" step)**: `Config.ai_api_key` re-reads
     `<config dir>/.env` on every access and **never consults the process environment**.
     Rationale: a machine-wide user environment variable (`DEEPSEEK_API_KEY` in
     `HKCU\Environment`) reached the app on double-click launches and made a
     never-configured install display a key that was not its own (HANDOFF §4.37). With the
     file as the only source, setting the variable has no effect at all.
     - Implementation: `read_env_file(path, names)` reports **what the file says**;
       `Config.dotenv_path` is pointed at the config file's directory by `load_config()`,
       so an explicit `--config` path keeps `config.yaml` and `.env` together. Empty values
       and comment lines do not count as configured.
     - An old problem disappears with it: `load_dotenv()` injects into the environment and
       **never overrides an existing variable**, which used to make a newly saved key lose
       to a stale one across restarts - the host's restart spawn worked around that by
       stripping the names from the child environment; that strip stays as defence in depth.
     - The settings page and `/api/status` still carry `ai.api_key_source`
       (`env_file` / empty); the label reads "source: local config file". Regression
       coverage: `tests/test_api_key_source.py` (10), one test asserting explicitly that
       **the environment variable must be ignored**.
     - `ai.api_key_env` now only selects **which key name to look for inside `.env`**
       (default `DEEPSEEK_API_KEY`); it no longer means "read the environment".
   - **Test fixtures must not look like keys** (e.g. `sk-bdb...`): any secret scanner
     (including a future export gate) cannot tell such a fixture from a real key. Assemble
     key-shaped values at runtime (`"sk-" + "a1b2c3..."`) instead of writing a literal
     (the current tests follow this).

## 9. Common Pitfalls Quick Reference

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
