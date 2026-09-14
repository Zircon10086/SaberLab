# Changelog

> Version format: `vX.Y (date)` — change summary. Parts of this document were described with AI assistance.

## v2.2.0 (in development, unreleased)

> Status: **current development version**. Entries are added below as work lands;
> a final pass (release date, bilingual RELEASE_NOTES, test counts) happens before
> release. Until then, every current-version reference in the docs means `v2.2.0`.

### Performance
- **Analysis computation is 26.6% faster (serial batch 13.4 s → 9.8 s; 33 → 24.2 ms
  per replay)** (2026-09, step 3):
  1. **`NoteParams.decode` memoized + decoded once at parse time**: the accuracy,
     scoring, notes and energy passes each decoded the same notes — 40531 decodes per
     full analysis (1.02 ms of pure decoding for one large replay). The parser now
     decodes once into `NoteEvent._params`; warm cost 0.38 ms and the 40531 property
     accesses are gone
  2. **`cut_scores` memoized**: both the scoring pass and the note-group pass need it
     (twice per good/bad note). It is a pure function of the cut info and noteID, so
     it is cached on the note object
  3. **`_group_metrics` counts in one pass**: it used to run four `_attr()`
     classification calls per note (~40k function calls per analysis); the NoteEvent
     path now counts in a single loop with **the metric expressions and names
     unchanged, character for character**
- Numerical safety: step 3 treats "metric values must not change" as a hard red line —
  re-analysing 12 replays produced **zero row-level differences** in `metrics` and
  `notes` (including energy/fail_time), all 311 tests pass, and after a full recompute
  the row counts and sampled metrics (accuracy/score/fail_time/min_energy/total_drain)
  match the baseline
- **Batch analysis is ~12x faster (246 s → 20.4 s for all 416 replays)** (2026-09,
  approved plan):
  1. **Merged database writes**: `Repository` now reuses one connection per thread
     and wraps a replay's whole set of writes in a **single transaction**
     (`Repository.session()`). It used to open 4 connections and commit 4 separate
     transactions per replay; measured 18.7 ms for a fresh connection vs 2.2 ms
     reused. Per-replay persistence went **59.3 ms → 9.5 ms**, and a replay's rows
     are now **atomic** (a failure rolls the whole replay back instead of leaving it
     half-written)
  2. **Removed repeated full map-library rescans**: `resolve()` triggers a full
     CustomLevels walk on a DB miss (1032 map folders here, **13.8 s each**). A batch
     requested one for every unmatched replay, and each of the 4 worker processes did
     it again — **that was the real bottleneck**. Batch paths now scan **once up
     front** and workers never rescan (`map_scan=False`); interactive single-file
     analysis still rescans, so a freshly downloaded map is still picked up
  3. **Batch analysis runs on 4 processes**: threads are useless here (1.04–1.24x,
     GIL-bound Python loops), processes measured **1.95x** — the rest of the gain
     comes from the two items above
- **Batch analysis skips replays whose source file is gone**: DB rows outlive files
  (ingest is add-only), and every batch used to produce a round of "file not found"
  errors for them

### Added
- **API key source display** (2026-09, key-exposure investigation): when an API key is
  configured, the settings page now labels where it came from — "Source: environment
  variable <name>" or "Source: .env". A machine-wide environment variable set by other
  tools reaches SaberLab through double-click launches and was previously
  indistinguishable from a self-saved key; `/api/status` ai section gains an
  `api_key_source` field (`env` / `env_file` / empty). The unconfigured state still
  shows "Not configured"; key handling behavior is unchanged
- **Replay right-click menu implemented** (2026-09, specified by the user): the items are
  **Open details · Attempts on this map · —— · Show in folder · Delete replay file**
  - **Attempts on this map** now puts the **song name** into the history search box
    (it used to put the map_hash, but the history search only matches song names and the
    5-character beatmap_key, so a hash never matched anything)
  - **Show in folder**: opens the OS file browser with the .bsor selected; when the file
    is gone it falls back to opening the containing folder (and says so)
  - **Delete replay file**: a **confirmation dialog** → the file is **moved to the OS
    recycle bin** (not permanently deleted, restorable from there); a success toast says
    so explicitly, and on failure the dialog stays open with the button re-enabled so
    nobody believes it was deleted. SaberLab's analysis data is kept — the original file
    is the only thing moved. Both file actions are disabled when the file is gone
  - **Copy replay ID was removed** (the ID is a per-device sha256 with no value to the
    user); the now-unused `copyText()` and the `[data-copy]` fallback entry were removed
    with it, leaving no dead code
- **Modal action buttons** (`openModal({actions})`, 2026-09): a button row (cancel /
  destructive action) for confirmations like deleting a replay. Clicking does **not**
  auto-close — the caller closes it after the request succeeds, so the UI cannot claim
  success the backend did not confirm
- **Context menu framework** (2026-09, framework first): right-clicking any registered
  element opens a menu that matches the project's look (acrylic material, enter/exit
  animation, Escape / outside-click / scroll dismissal) by reusing the existing
  `openPopover` overlay, which gained an `at: {x, y}` coordinate mode.
  **Adding a right-click feature means adding one entry to the `CTX_MENUS` registry**
  — no framework changes. Two entries are registered:
  - `.replay-item` (shared by overview / history / detail same-map history):
    open details, attempts on this map, copy replay ID
  - `[data-copy]`: generic "Copy", for future elements to hang off
  Details: the native menu is allowed through inside inputs; so is a matched element
  whose `items()` returns nothing; items support a danger colour and a disabled state;
  the menu closes before the action runs and a throwing action reports a toast;
  `copyText()` has a three-step fallback (Clipboard API → retry after focusing →
  textarea + execCommand) and reports an empty value explicitly instead of failing
  silently
- **Local fail-time computation** (2026-09): the `.bsor` `failTime` field is **never
  written by the mod** (498/498 local replays are 0.0), so the energy curve and the
  fail moment are recomputed locally with the **official algorithm recovered by
  decompilation** (`GameEnergyCounter`). Official constants (identical in 1.39.0,
  1.40.8 and 1.44.1): good note +0.01 / bad −0.10 / miss −0.15 / bomb hit −0.15 /
  burst slider element +0.002 / −0.025 / −0.03 / obstacle −1.3 per second; Bar mode
  starts at 0.5, Battery and instaFail at 1.0 (4 cells), cap 1.0; energy ≤1e-5 means
  a fail. Implemented in `backend/analysis/energy.py` (deterministic pure functions,
  no network, no LLM) and it **also derives more telemetry in the same pass**: the
  energy event stream (re-playable timeline), drain per cause, charge/drain per hand,
  obstacle-hit checkpoints, minimum energy, time spent in the danger zone — all
  persisted under the `metrics` `energy` scope, readable by existing paths with **no
  new table**. **UI**: the detail page gained an energy summary (lowest energy, fail
  time, drain broken down by cause, obstacle hits) and the timeline draws the
  **fail-time red line** (pixel-checked: 0.04px from the axis mapping). **Fail time is
  deliberately kept apart from the completion classification** — finishing after
  failing, quitting before failing and quitting after failing are all shown as facts,
  never guessed. `completion_status` semantics are **unchanged** (switching it to the
  computed value is a separate product decision)
- **Forced batch recompute**: `POST /api/analyze/all?force=true` re-analyzes every
  replay regardless of `analysis_status` — for backfilling history after the engine
  gains a metric, instead of wiping the analysis cache for a few new numbers
- **New "By Session" paging on the overview** (2026-09, user request): replays are
  grouped by the **time gap between neighbouring plays** — a gap longer than the
  threshold (new setting `analysis.session_gap_minutes`, default **60 minutes**, far
  longer than a song) starts a new play session. This fixes the two things "By Day"
  gets wrong: **a midnight run split across two days**, and **a morning plus an
  afternoon block merged into one day**. Measured on the local 425 replays: 44 day
  groups vs 48 sessions — `2026-09-07 23:24 – 09-08 00:03` (19 records) was split
  into two days before, and 4 days that showed as one group actually hold 2–3
  sessions. Pagination unit = one session, with matching labels ("N sessions /
  Previous session / Next session") in all three languages. **It sits leftmost in
  the tab row and is the default selection** ("By Day" moves to second place and
  stays selectable; "By Count" is unchanged). **Session headers always carry the
  date** (single-day: `2026-09-03 18:49 – 19:50`; cross-midnight: both ends dated,
  `2026-09-07 23:24 – 2026-09-08 00:03`) — clock times alone would not tell the
  player which day they played
- **Sidebar player card**: the bottom-left corner (above "Server running") now shows
  the current player — avatar, name, global rank and country rank with its flag. It
  reads the same player profile snapshot as the cloud data page, written by
  "Fetch data & compute dynamic level" (or the cloud sync stage of Quick Refresh);
  the card itself is read-only local cache and makes no network requests. The avatar
  is **downloaded into the local cache during the sync** and served by a backend
  endpoint afterwards (visible offline, no external domain exposed to the WebView,
  honours the configured proxy); without a cached avatar the card falls back to the
  player's name initial, and a missing flag falls back to the country code text —
  never a broken image. With no cloud data synced yet the card stays hidden (a
  normal state, not an error). New read-only endpoints: `GET /api/player/card`,
  `GET /api/player/avatar`, `GET /api/player/flag`
- **Sidebar player section (card -> divider)**: the bottom-left corner now holds
  **two sections** — player info (avatar + name + global/country rank) on top and
  the server status below — separated by the same 1px grey line that already sits
  above "Server running"; the rounded card background is gone, so the block
  matches the existing footer's visual language. Both sections ride one
  `margin-top: auto` container to the bottom (with the auto margin on each, the
  free space gets split evenly and the block floats to the middle)
- **New "replay retention" row in the Game Path card** (2026-09, follows the §4.25
  root cause): after entering/picking the game root, the check now also reads the
  BeatLeader mod's "keep latest only" setting (`OverrideOldReplays` in
  `UserData/BeatLeader.json`) — **enabled → red ❌ warning** (telling the user to
  turn it off in-game so new saves stop deleting older .bsor files of the same map
  & difficulty); **disabled → green ✅**. With the mod absent, the key missing, or
  the config unparsable it shows a **neutral note** (neither green nor an alarm, to
  avoid false warnings); the row is advisory only and **never changes the
  "verified" badge** (the path itself is fine; the fix happens inside the game).
  All three languages covered
- **Ranks on one line (wrapping only when needed)**: global and country rank sit
  side by side (~8px to spare), and drop to a second line only when their
  combined width exceeds the text column — `flex-wrap` breaks at an item
  boundary, never inside a number, and nothing gets clipped. Measured: local
  values 55.3+45.8+8 gap = 109.1px < 117px available -> one line; synthetic long
  values -> two lines with no overflow

### Documentation / conventions
- **New product rule: user-facing text** (2026-09, recorded in `AGENTS.md` section 9.1):
  every string SaberLab renders has **the ordinary player** as its reader — **state only
  what will happen, never explain why**; no implementation details (tables/fields/tasks,
  how paths are derived, cache or file-copy behaviour, token wording); **parentheses that
  explain a technical term are kept** (units, ranges, score parts, parameter names such as
  `Center (0-15)`, `Pre / Center / Post`, `(x0.5)`, `(ms)`, `(B - A)`); avoid internal
  jargon (fallback/rollback/caliber-like wording); "why" belongs in developer docs;
  `title` tooltips and `aria-label`s follow the same rule

### Changed
- **The AI key is read from the local config file only** (2026-09, requested): the key was
  also read from the system environment, so a machine-wide `DEEPSEEK_API_KEY` set for
  another tool made a never-configured SaberLab report "configured" and use it. The key is
  now read **only from the `.env` inside the program directory**; environment variables
  have no effect. A key saved in the settings applies immediately, and the source is
  always labelled "local config file"
- **Player skill estimate reworked into three baselines (80% / 94% / 96%)** (2026-09,
  requested): the star palette no longer derives one yellow baseline from your best PP
  or an average star rating. It answers "which star rating can this player hold at a
  given accuracy": the **80% baseline** is what you can clear on hard maps, the
  **94% baseline** is your regular level, the **96% baseline** is your high-accuracy
  level. Settings → Star Palette lets you pick any of them for colouring the STARS
  numbers in lists and on the detail page. **A baseline without enough data shows no
  number and cannot be selected**: it is greyed out with "Not enough data" in the
  settings, the cloud page shows "Not enough data" for that column too, and a
  baseline that only has a lower bound says so explicitly ("shown N★") instead of
  presenting an unverified range as a precise rating
- **3D replay pauses when you leave its page** (2026-09, requested):
  the "Data / AI analysis / Replay" tabs on the detail page are one page moved by
  transform, so a replay kept playing in the background after you switched away —
  audio and scene included — and only reset when another replay was opened. Now
  playback pauses on leaving and keeps its position. **Coming back does not resume
  it automatically** — press play inside the replay to continue from where it stopped
- **Scope-2 text pass applied (7 more strings)** (2026-09):
  - Main table: `settings.datasource.desc` (dropped the "auto-resolved from Replay /
    cached data is kept" mechanics), `detail.file_missing_desc` (dropped "already
    persisted / needs the original file" and now only states what still works),
    `settings.game.retention_warn` (first clause was too long -> "Older replays will be
    deleted - turn off \"keep latest only\" in the in-game BeatLeader replay settings")
  - Backend error strings (4) — **the backend original AND the en/ja mapping keys must
    change together**, otherwise the mapping stops matching:
    "This Replay was already analyzed (sha256 dedup)" -> "already analyzed";
    the empty-database message lost its internal pipeline list; the retention-status
    notes now state the outcome ("older replays are kept" / "can be ignored")
  - `index.html`'s static placeholder for `settings.datasource.desc` realigned (it had
    drifted from its i18n value)
  - Test updated with the wording: `test_config_absent_is_neutral` asserted the old phrase
    ("not installed"); it now asserts "can be ignored" plus `ok=False`, keeping the real
    intent (neutral note, never a warning)
  - **Kept untouched by the rule**: `detail.acc_hint`, `reversal.hint`, `compare.hint`
    (ranges/direction/colour meaning = technical notes), the other 36 `err` entries,
    3 `msg` entries and 7 `task.current` entries
- **User-facing text optimised per the new rule (15 strings)** (2026-09):
  - **Cause/mechanism explanations removed**: `NF (auto-enabled after fail)` -> `NF`;
    `No Fail auto-enabled after fail; effective score halved` -> `No Fail - effective score
    halved`; `Not configured (rule-based fallback)` -> `Not enabled`; `Rule-based report
    (LLM not called)` -> `Rule-based report`; `Rule fallback` -> `Rule-based report`;
    `No cached avatar (fetched on the next cloud sync)` -> `No cached avatar`;
    `Config could not be parsed (other checks unaffected)` -> `Config could not be parsed`;
    the "non-zero diffs usually mean..." paragraph -> "The cloud entry is a personal best,
    so it can differ from a single local play."
  - **Internal mechanics/jargon turned into user language**: the path-derivation sentence
    -> "other paths are found automatically"; "Original Replay files and the map library
    are untouched" removed (implementation detail); "Game path applies immediately; port/AI
    changes need a restart" -> "Port / AI changes take effect after a restart";
    `Fallback estimate` -> `Estimate`
  - Backend settings descriptions in `config/schema.py` trimmed the same way
  - Two static placeholders in `index.html` realigned with their i18n values (they had
    kept the older, longer text, which would reappear if i18n failed to load)
  - All three languages (zh / en / ja) updated; **the 16 technical parentheses were kept**
  - Out of scope by the user's decision: `settings.datasource.desc`,
    `detail.file_missing_desc`, `settings.game.retention_warn`, `detail.acc_hint`,
    `reversal/compare.hint`, plus the `err`/`msg`/`task` backend-message tables
    (error hints deserve their own pass)
- **History paging position and default behaviour aligned with the overview** (2026-09,
  user request): (1) the pagination control **moved up into the filter row and now matches
  the overview exactly** — it reuses the same three-column symmetric grid
  (`minmax(0,1fr) auto minmax(0,1fr)`: filters left, paging centre, empty right), measured
  **0 px** centring offset; the history content is wrapped in a `.surface` and the filters
  moved into `.title-left`; (2) **paging is now shown by default (no query)** — previously
  the no-query view fetched only 300 rows and had no paging control, so "300 shown but no
  way to page" was simply wrong; the default view now also fetches everything and pages
  (verified: 424 rows → page 1 has 300, page 2 has 124, with correct highlights)
- **History page gained count-based paging (300 rows/page) and full-library search**
  (2026-09, proposed by the user): it reuses the **overview's pagination buttons**
  (`unit=count`, same component, same styles, same ellipsis logic), driven by the history
  page's own loader; changing the query or the date range returns to page 1
- **Search fetch cap raised 2000 → 10000, and `/api/history`'s ceiling 2000 → 50000**
  (2026-09, per the user's request to simulate scale first): measurements show **the fetch
  cap is the only real constraint** — the server returns ~1083 bytes/row (424 rows =
  448 KB / 23 ms), while client-side filtering+sorting of 6000 rows takes **2 ms** and
  rendering a 300-row page **13 ms**. The old 2000 cap **missed 67% of hits** on a
  6000-replay library (4527 hits, only 1512 found). Extrapolated to 10800 rows that is
  ~11.4 MB over loopback, which is acceptable
- **Settings now auto-save when focus leaves a field** (2026-09, user request):
  editing a value and moving focus away (Tab / clicking elsewhere) saves
  automatically; dropdowns and checkboxes save as soon as their value changes.
  Success is reported by a **toast**, and the old "Save All Settings" button plus
  its hint text are **removed**. It reuses the single save path
  (`collectSettings()` submits only genuinely changed fields), so no per-field save
  logic was needed; with nothing changed it stays silent (clicking blank space
  shows no prompt).
- **Session-gap setting renamed `analysis.session_gap_minutes` →
  `ui.session_gap_minutes`** (2026-09 fix): it is only the overview's "By Session"
  grouping threshold and takes part in no analysis, but living under `analysis.*`
  made the "analysis parameter changed → reset cache" rule fire — **changing it
  once wiped the whole library's analysis data** (metrics/motion_series cleared,
  every replay back to pending). It now lives under `ui.*` (Settings → Interface)
  with a one-shot migration: reading an old config moves the key to the new group
  and drops it from the file, so no duplicate key lingers in two places
- **Enum dropdowns in Settings restyled to frosted glass** (2026-09, user request):
  a native `<select>`'s popup list is drawn by the OS and cannot be styled by the
  page, so it is now a "glass trigger button + the generic anchored popover
  `openPopover` that the PP prediction uses" — same material
  (`rgba(20,22,30,0.66)` + `blur(14px) saturate(150%)`), same enter/exit animations
  (`popIn`/`popOut`), same anchoring (flips up when there is no room, clamped
  sideways) and the same dismissal paths (outside click / Escape / scroll / zoom).
  The value still lives in a hidden `input[data-key]`, so **the save and
  dirty-field detection paths are untouched**. Covers every enum in Settings
  (star palette, data source, AI provider, ...). Custom-dropdown ARIA added too
- **Deleting a replay now also removes its record** (2026-09, user decision): if the user
  deletes the file, keeping the local analysis around is not what they asked for.
  `Repository.delete_replay()` removes the `replays` row and all derived data
  (`notes` / `metrics` / `windows` / `motion_series` / `accuracy_curve` / `ai_reports`)
  in a **single transaction**; baseline/candidate references inside `experiments` are
  nulled rather than deleting the experiment record. The order is safe: **the record is
  only removed after the file reached the recycle bin**, so a mid-way failure can never
  leave "record gone but file still there"; a file that is already missing still allows
  cleaning up the record. Cloud-side data is untouched
- **Delete confirmation dialog** (2026-09, user request): added **fade in/out animations**
  (same parameters as the context menu: 0.18 s ease-out in, 0.15 s ease-in out with a
  slight scale); **overlay dimming lowered** from 0.55 to 0.42; the redundant
  **"Close" button was removed** when action buttons are present (a plain informational
  modal keeps it, being its only exit)
- **Confirmation copy now states only what will happen** (2026-09, user's rule): title
  "Delete replay", buttons "Cancel / Delete", hint "The file will be moved to the system
  recycle bin, SaberLab will remove this record, and data on the cloud site is kept." —
  no parenthetical asides and no explaining why. The success toast likewise reads
  "Deleted "<name>" — the file was moved to the recycle bin"
  (`aria-haspopup/expanded`, `role=listbox/option`). **Clicking the same trigger
  again closes it** (it no longer replays the enter animation), matching the
  toggle semantics of the PP prediction popover

### Fixed
- **Two i18n mapping mismatches** (2026-09) — failures that raise nothing; they only make
  translations silently ineffective:
  - **Replay-retention warning** (`err` section): the backend message is an **implicit
    two-line concatenation**, so last round's whole-string replacement never matched. The
    backend kept the old wording while the i18n key had already been updated, so all three
    languages lost the translation. The backend string now carries the new wording and the
    en/ja keys were moved with it
  - **Orphan entry `当前未联网`**: the frontend uses the dotted key `t("err.offline")`
    (zh wording "当前未联网，无法更新在线数据"), so `tErr()` can never receive that
    message — the en/ja entry was dead → removed
  - **New regression test `tests/test_i18n_mapping.py` (5 cases)** pins both conventions:
    (1) every `err` key must correspond to a real backend message (literals extracted via
    AST, so Python adjacent-string concatenation and f-string placeholders are handled);
    (2) every dotted key the frontend references must exist in all three languages;
    (3) en/ja must cover the zh baseline key set; (4) message sections must have the same
    size across languages
  - Note: the "3 pre-existing failures" reported last round came from a **crude regex
    check**; proper template matching shows `「{kind}」任务已在运行` and
    `谱面文件夹过大 ({size}MB)` were fine all along — the real problems were the two
    above (the flaw in my checker is fixed by using AST in the new test)
- **History search missed older records** (2026-09; the user reported "I can see a Noob
  record in the overview but the history search cannot find it"): the history page always
  fetched only the newest **300** rows while the library holds 424 — **the oldest 124 are
  all from the pre-login period under the default name "Noob"**, so they were cut off at
  fetch time and it looked like player-based filtering. The search itself
  (`histScore`) **only matches song names and the 5-character beatmap_key and never the
  player name**, and that stays as is (one machine usually has one player, and the
  default pre-login name is the same person — filtering by player name would only make
  one person's records hide from each other). Now: **no query → 300 rows (keeps first
  paint fast); any query → full fetch** (`limit=2000`, plenty for a local library).
  Verified: previously unfindable old records such as `Toxic` and `Twisted Drop Party`
  are searchable again
- **"Show in folder" opened the Documents folder instead** (2026-09, reported by the
  user): explorer's `/select,` switch and the path **must be two separate argv entries**;
  passed as one argument (`/select,<path>`) explorer fails to parse the switch and falls
  back to opening the user's Documents folder. Now
  `["explorer.exe", "/select,", path]`, verified by enumerating Explorer windows through
  Shell.Application (it really opens `file:///D:/.../BeatLeader/Replays`).
  **Lesson**: the previous check only verified that explorer *started*, not *which folder
  it opened*

### Internal
- Avatar/CDN image caching consolidated in `backend/services/player_assets.py`
  (download once -> local cache -> served locally; a failed download never breaks
  the sync, and the read path never touches the network); cache lives in `data/assets/`
- Fixed `_tmp/shot.ps1` screenshot-by-window-title: it only matched `python`, while
  `run.bat`/`run-browser.bat` launch `pythonw.exe` (both are accepted now); it also
  uses **PrintWindow + PW_RENDERFULLCONTENT** to render the window itself instead of
  `CopyFromScreen`, which captured **another application** whenever the window was not
  foreground / was occluded (this caused a UI-verification misdiagnosis once)

## v2.1.0 (2026-09-02, released 2026-09-03)

### Added
- **PP prediction (accuracy preview)**: click a ranked replay's PP value to open
  a preview card **anchored right below the PP value** (flips above when space is
  tight, with enter/exit animations; closes on outside click / Escape / scroll,
  clicking the same PP value again toggles it), and drag the slider to read the
  estimated PP at any accuracy (layout mirrors ScoreSaber's official Accuracy
  preview card; the reset button returns to this play's accuracy). Adds a
  generic anchored popover component, `openPopover` (no overlay, does not block
  page interaction); the fullscreen `openModal` is a **standalone global layer
  for strong prompts** (slice details already live inline on the detail page —
  the modal is not tied to SliceDetails)
- **ScoreSaber PP formula replication**: `pp = maxPP × curve(acc)` — the curve is
  embedded from ScoreSaber's official pp-curve endpoint (37 piecewise-linear
  knots, normalized at acc=0.95) and maxPP comes from the leaderboard cache;
  verified against local replay data (clean samples within ±0.1%). The
  prediction only needs the leaderboard maxPP, so it works for every ranked map
  (no top-100 cache requirement); awarded NF PP uses the score SS actually saw,
  while the preview slider remains anchored to the replay's displayed accuracy;
  computation lives in `backend/analysis/pp_predict.py` (deterministic pure
  functions, works offline)
- New `GET /api/replays/{id}/pp-preview`: ScoreSaber data source only
  (BeatLeader uses a different formula, not supported yet); unranked maps /
  unsynced stars return a structured 404; i18n for all three languages
- **Popover material + fade-out + slider rework**: the preview card now uses the
  project's acrylic glass presets — background `rgba(20, 22, 30, 0.66)` (the
  `body.acrylic` `--surface` value, uniform across browser/webview modes instead
  of an opaque `#191e29` block in browser mode) with the header's
  `backdrop-filter: blur(14px) saturate(150%)`; `z-index` raised to 200 (above
  header/sidebar, below toast). Fixed the missing exit fade (`popOut` only had a
  `to` keyframe, so its start was the element's base `opacity: 0` → a 0→0
  animation that vanished instantly; now has an explicit `from` and reverses
  `--pop-drop` so it slides out in the opposite direction of entry). The slider
  was manually rewritten (native `input[type=range]` renders the WebKit thumb
  linearly over `[half-width, width-half-width]` while the gradient track is
  `[0, width]` — up to 9px misalignment at the ends): self-drawn
  track/fill/thumb share one coordinate system (fill right edge == thumb
  center), pixel-matched to the native value→position mapping, and the hidden
  native input remains the interaction layer (drag/click/keyboard all native).
- **Toast rework**: moved from bottom-right to **top-center, dropping in from
  above**; fixed the missing enter animation (the old `:not(.leaving)` state
  matched on the very first frame, so the transition never ran — now the toast
  enters `.in` after a double `requestAnimationFrame`, so the transition starts
  from the base state); the legacy left color band was retired in favor of the
  **semantic graduated background** used by list status cards (16% alpha fading
  out at 65%, over `var(--surface-2)`); added a **full-width progress bar** at
  the card top (shrinks right-to-left to the left edge over the lifetime, left
  edge fixed); when the bar hits zero the toast fades up and out (JS
  `setTimeout` fallback under reduced-motion); stacked toasts queue vertically
  at the top.
- **Toast size & states**: card enlarged (padding 14/22, max-width 440, text
  15px — the background grows more than the text); lifetime shortened from 4s
  to **3s**; new **success** state (green graduated background + green progress
  bar, used by task-completion toasts; kind family = success green /
  error red for failures & warnings / info blue).
- **Exact ACC input on the PP card**: the accuracy readout right of the slider
  is now an **editable input** — type a precise ACC percentage (60–100) and
  press Enter to apply instantly (slider/bar/PP value all follow; out-of-range
  values clamp to the edges, invalid input rolls back; dragging the slider
  syncs the input display).
- **Missing original replay file degradation mark** (2026-09): ingest is
  add-only, so DB rows can outlive their original .bsor — when a file is gone
  the detail page shows a red warning banner under the hero ("Original replay
  file missing" + explanation), slice details / 3D replay / hand-motion charts
  display an explicit "unavailable — original file missing" reason instead of a
  generic "no data", while persisted data (timeline etc.) still renders;
  overview/history/same-map-history rows get a red "File missing" badge
  (all three languages).
- **LocalLeaderboard as a second replay source** (2026-09, zero-config auto
  detection): when `UserData/LocalLeaderboard/Replays` exists it is scanned
  alongside BeatLeader — same-session copies are skipped (no duplicate rows);
  if a row's original .bsor was deleted externally, one-click refresh
  automatically repairs that session's `file_path` to point at the surviving
  LocalLeaderboard twin (analysis data untouched); sessions only stored by
  LocalLeaderboard are ingested as normal rows; the ingest toast reports
  recovered / newly-ingested counts. Background: the BeatLeader mod's replay
  setting "keep latest only" (on by default; config key `OverrideOldReplays`)
  **deletes older .bsor files of the same map & difficulty whenever it saves a
  new replay** (confirmed via game logs) — this feature is the second source
  against exactly that loss (exit replays have no LocalLeaderboard copy;
  disabling that in-game setting stops the deletion entirely). Measured
  2026-09-11 across every `Logs/*.log(.gz)`: 217 deletions / 213 distinct files /
  2025-12-13..2026-09-07, **78% of them exit replays**. **Turn that setting off
  in-game** — that alone stops the loss, no SaberLab change required.

### Changed
- **Launching replaces an old SaberLab instance**: when port 6980 (or the
  fallback range) is occupied by SaberLab, the launcher confirms both the
  status identity and TCP owner PID, terminates the stale instance, and binds
  6980 again instead of exiting with a potentially hidden window. Unrelated
  port occupants are never killed and still trigger the 6981..6999 fallback;
  a named startup mutex prevents concurrent double-launch races
- **ScoreSaber PP is now calculated per local replay**: `map_ranked_cache.pp`
  is the cloud best for a difficulty, not every local attempt. Overview/history/
  detail responses now estimate each completed replay independently from the
  cached leaderboard `maxPP × curve(this play's acc)`. Five Cyaegha Expert
  attempts that all incorrectly showed `300.9pp` now show about `300.9 / 291.4 /
  280.6 / 269.0 / 260.5pp`. NF uses ScoreSaber's effective-score convention;
  exits do not fabricate awarded PP. **This formula remains ScoreSaber-only;
  BeatLeader PP prediction is intentionally not implemented**
- **One-click refresh now includes player cloud data**: the existing five task
  groups remain; after leaderboard/player-PP indexing, `ranked_update` refreshes
  the active platform's profile and recent scores and recomputes the dynamic
  level in the same task (no duplicate parallel API traffic). Manual cloud-data
  refresh now shows a green success toast in all three languages
- **Batch analysis no longer generates any reports** (user decision,
  2026-09-01): the v2.0.1 fix that made batch reports actually run exposed a
  design collision — a full post-cache-clear batch called the LLM **once per
  replay** (~20s each, 345 plays ≈ 2 hours), so one-click refresh appeared
  stuck at "batch analysis" (measured: only 23 reports in 7 minutes). The
  analysis pipeline (watcher) now never produces reports; the
  `run_ai/ai_client/build_context/lang` parameter chain is removed. **The only
  report entry point is the detail page's "Generate report" button**
  (`/api/ai/analyze/{id}`), where the "Use AI for reports" toggle decides LLM
  vs rule report; the button reads "Generate report" when no report exists and
  "Regenerate" otherwise. Verified: 321 replays analyzed in ~110s with zero
  new ai_reports rows.
- **Standalone modal**: `openModal`/`closeModal` are a **standalone global layer
  for strong prompts** (top-level overlay, `.modal-*` styles, `onClose` via the
  `modalclose` event), independent of SliceDetails — slice details moved to the
  detail page's **inline card** (click a tile to expand in place), and the
  modal-based `openSliceModal` was removed.
- **Unused endpoints marked deprecated**: task/analysis endpoints superseded
  by one-click refresh are kept but annotated DEPRECATED (no deletions, API
  compatibility preserved).

### Fixed
- **NF/exit PP previews no longer jump to 60%**: the default is the replay's
  currently displayed accuracy; when it is below the normal 60% floor, the
  slider range extends down to that value instead of clamping it
- **Task-card spinner restored**: i18n previously replaced the whole title
  container with text and deleted its spinner node. Only the label span is now
  translated, while `data-task=running` controls spinner visibility
- **Window scrollbar and BeatLeader unverified-star polish**: the WebView2
  black/white native scrollbar is replaced by a dark translucent track and
  restrained red/blue thumb across document/nested scrollers; unverified-star
  yellow backgrounds/borders are dimmer so the numeric rating stays legible
- **Saving settings no longer wipes the analysis data**: the settings form
  submits every field on each save and the backend treated any submitted
  `analysis.*` key as a parameter change, so every save cleared the analysis
  cache (metrics/windows/motion_series deleted + all replays reset to pending).
  Change detection is now value-based (backend `save_values` returns the list
  of actually-changed keys; the frontend only submits dirty fields); the
  "restart required" hint likewise only appears when a restart-required
  setting actually changed.
- **Batch-analysis AI reports silently always failing**: the analysis pipeline
  passed the `build_context` function into `run_ai_report`'s context slot;
  `json.dumps` raised TypeError before any try/except, so reports from
  one-click refresh / batch analysis / analyze-latest (including the
  rule-based fallback) were never persisted. The detail-page "regenerate"
  button passed the right argument, which is why this went unnoticed.
- **Some AI settings not taking effect after save**: the post-save hot reload
  never refreshed the LLM client snapshots — temperature/max_tokens
  (non-restart settings) silently kept their old values, and the AI status
  stayed stale after configuring an API key for the first time.
- **New API key not applied after in-app restart**: the restarted child
  process inherits the parent's environment while `.env` loading never
  overrides existing vars — the host now strips `.env`-provided keys from the
  child's environment so `.env` is re-read fresh.
- **BeatLeader stars could attach to the wrong difficulty**: the enrichment
  tiebreak relied on `game_mode`, which the snapshot query never selected, and
  the `"SoloStandard"` literal never matched BeatLeader's `"Standard"` — with
  several characteristics sharing a difficulty name an arbitrary row won
  (Lightshow stars could land on Standard).
- **Completion detection broken for v3 maps**: the song-length fallback did
  not read v3's `colorNotes`/`b` fields → song_length=0 → the <98% completion
  check never fired (mid-run quits without a `-exit-` filename were marked
  completed).
- **Map negative-cache poisoning**: hashes arriving during a scan (or within
  the 30s debounce) were added to the negative cache without ever being
  searched — maps downloaded afterwards would never resolve until restart.
- **Full combo now accounts for bombs**: hitting a bomb breaks the combo just
  like bad cuts/misses (consistent with the official scoring port);
  full_combo previously only checked bad/miss (`counts.bomb` counts only
  bombs actually cut; untouched bombs produce no event in the BSOR stream).

### i18n
- Added keys missing from all three languages: compare-table headers
  (`compare.value_a/b`), offline guard (`err.offline`), empty-DB hint
  (`err.db_empty`) — these previously rendered as raw key strings under en/ja.
- The "Loading…" placeholders are now i18n-aware (overview/history initial
  state); the failed-songs separator renders per language.
- Task progress strings ("Preparing…", "Map sync: …") and the settings-save /
  cache-clear confirmations are translated via the new `msg` / `task.current`
  table sections (en/ja; zh shows the original).
- Rule-report language passthrough completed: `/api/analyze/{id}` and
  `/api/analyze/by-path` accept `?lang=`.
- 14 new backend-message mappings in the `err` section; enum options
  off/personal/custom translated.

### Internal
- Enrichment snapshot is now assigned as a single tuple (removes a torn-read
  window during platform switches).
- Cover fallback sorting guards the stat race (a vanished file no longer 500s
  the cover endpoint).
- 212 unit tests pass.

## v2.0.0 (2026-08-23)

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
- NF fail-time red-line marker: implemented but **paused** — the local .bsor
  `failTime` field is 0 in every sample (326/326). Corrected 2026-09-11: this is a
  **missing feature of the replay (writer) component** (the field is simply never
  written), so the plan is now to compute the fail time locally in SaberLab
  (see HANDOFF §4.11)

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
