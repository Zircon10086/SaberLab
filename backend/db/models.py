"""SQLite schema (design doc §17).

Principles:
- Raw BSOR files are kept and never stored in the DB; SQLite only stores summaries and metrics.
- The replay primary key = sha256 of the file content (design doc §7.4).
- Every re-analysis produces a new analysis_version.
- The whole migration history is consolidated here + repository._migrate(): a fresh DB gets the
  complete structure via executescript(SCHEMA); old DBs get columns/tables added idempotently by
  _migrate() (no longer relying on _tools scripts).
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS maps (
    map_hash        TEXT PRIMARY KEY,
    folder_name     TEXT,
    path            TEXT,
    song_name       TEXT,
    song_author     TEXT,
    mapper          TEXT,
    bpm             REAL,
    song_length     REAL,              -- song duration in seconds, from info.dat
    version         TEXT,
    difficulties    TEXT,          -- JSON: [{characteristic, difficulty, label}]
    info_json       TEXT,          -- full info.dat (compression optional for storage; keep raw text for now)
    hash_source     TEXT,          -- songcore_cache | computed
    ranked_difficulty TEXT,        -- ranked difficulty name (e.g. "Expert")
    stars           REAL,          -- star rating
    scoresaber_updated TEXT,      -- last update time
    beatmap_key     TEXT,          -- BeatSaver key (extracted from the folder name "16633 (song)")
    nps_json        TEXT DEFAULT '{}',  -- JSON: {"Standard|Expert": nps, ...}
    last_scanned    TEXT
);

CREATE TABLE IF NOT EXISTS replays (
    replay_id       TEXT PRIMARY KEY,      -- sha256(file bytes)
    file_path       TEXT,
    file_name       TEXT,
    file_size       INTEGER,
    file_mtime      REAL,
    timestamp       INTEGER,               -- unix time of play start
    player_id       TEXT,
    player_name     TEXT,
    platform        TEXT,
    tracking_system TEXT,
    hmd             TEXT,
    controller      TEXT,
    game_version    TEXT,
    mod_version     TEXT,
    map_hash        TEXT,
    song_name       TEXT,
    mapper          TEXT,
    difficulty      TEXT,
    mode            TEXT,
    environment     TEXT,
    modifiers       TEXT,
    score           INTEGER,               -- total score recorded in the replay
    score_recomputed INTEGER,              -- total score independently recomputed by the parser (for validation)
    score_effective INTEGER,               -- effective score (halved on NF/Fail, otherwise = score)
    has_nf          INTEGER DEFAULT 0,     -- modifiers contains NF (actually failed)
    jump_distance   REAL,
    left_handed     INTEGER,
    height          REAL,
    start_time      REAL,
    fail_time       REAL,               -- .bsor failTime (official semantics: only meaningful if failed;
                                        --   measured with mod 0.9.33 it is always 0 → frontend marks a pause on the red axis, see parser.py comment)
    speed           REAL,
    won             INTEGER,
    frame_count     INTEGER,
    fps_median      REAL,
    duration        REAL,
    note_count      INTEGER,
    good_count      INTEGER,
    bad_count       INTEGER,
    miss_count      INTEGER,
    bomb_count      INTEGER,
    accuracy        REAL,                  -- recomputed accuracy (BeatLeader convention)
    max_combo       INTEGER,
    full_combo      INTEGER,
    completion_status TEXT DEFAULT 'completed',  -- completed | failed | incomplete | pending
    profile_id      TEXT,
    analysis_version INTEGER DEFAULT 1,
    status          TEXT DEFAULT 'parsed', -- parsed | analyzed | error
    analysis_status TEXT DEFAULT 'pending', -- pending | analyzed (layered analysis state machine)
    error_message   TEXT,
    parsed_at       TEXT,
    analyzed_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_replays_map ON replays(map_hash);
CREATE INDEX IF NOT EXISTS idx_replays_ts ON replays(timestamp DESC);

CREATE TABLE IF NOT EXISTS notes (
    replay_id       TEXT NOT NULL,
    idx             INTEGER NOT NULL,
    note_id         INTEGER,
    event_time      REAL,
    spawn_time      REAL,
    event_type      INTEGER,       -- effective type (bomb has been re-marked)
    saber           TEXT,          -- left/right
    scoring_type    INTEGER,
    line_index      INTEGER,
    layer           INTEGER,
    color_type      INTEGER,
    cut_direction   INTEGER,
    before_score    INTEGER,       -- 0..70
    center_score    INTEGER,       -- 0..15
    after_score     INTEGER,       -- 0..30
    note_score      INTEGER,       -- total of the three, 0..115
    cut_distance    REAL,          -- meters
    saber_speed     REAL,
    time_deviation  REAL,
    PRIMARY KEY (replay_id, idx)
);

CREATE TABLE IF NOT EXISTS metrics (
    replay_id   TEXT NOT NULL,
    scope       TEXT NOT NULL,     -- overall | left | right | fatigue
    name        TEXT NOT NULL,
    value       REAL,
    detail      TEXT,              -- optional JSON detail
    PRIMARY KEY (replay_id, scope, name)
);

CREATE TABLE IF NOT EXISTS windows (
    replay_id   TEXT NOT NULL,
    window_idx  INTEGER NOT NULL,
    t_start     REAL,
    t_end       REAL,
    t_ref       REAL,              -- median note event time within the window (timeline anchor, v1.4.1)
    metrics_json TEXT,             -- all metrics of this window
    PRIMARY KEY (replay_id, window_idx)
);

CREATE TABLE IF NOT EXISTS motion_series (
    replay_id   TEXT PRIMARY KEY,
    series_json TEXT               -- downsampled saber-speed/angular-velocity time series (for charts)
);

-- Official-convention per-block accuracy curve (2026-08: fixes the curve not matching the replay record)
-- Data source: analysis-time running_accuracy from compute_score (score/maxScore, including
-- bad/miss/bomb/wall penalties and multiplier — the notes table has no wall data so it cannot be rebuilt).
-- get_accuracy_curve reads this table first; historical replays not recomputed fall back to the good-only convention.
CREATE TABLE IF NOT EXISTS accuracy_curve (
    replay_id   TEXT PRIMARY KEY,
    curve_json  TEXT               -- {"t": [...], "acc": [...]} (raw; smoothing is done at read time)
);

CREATE TABLE IF NOT EXISTS profiles (
    profile_id  TEXT PRIMARY KEY,
    name        TEXT,
    created_at  TEXT,
    position_x  REAL, position_y REAL, position_z REAL,
    rotation_x  REAL, rotation_y REAL, rotation_z REAL,
    source      TEXT,              -- replay_metadata | manual
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS experiments (
    experiment_id       TEXT PRIMARY KEY,
    created_at          TEXT,
    hypothesis          TEXT,
    profile_id          TEXT,
    baseline_replay_id  TEXT,
    candidate_replay_id TEXT,
    status              TEXT DEFAULT 'open',   -- open | concluded
    conclusion          TEXT
);

CREATE TABLE IF NOT EXISTS ai_reports (
    report_id   TEXT PRIMARY KEY,
    replay_id   TEXT,
    created_at  TEXT,
    provider    TEXT,
    model       TEXT,
    status      TEXT,              -- ok | error | not_configured | rule_based
    context_json TEXT,
    report_md   TEXT,
    error       TEXT
);

-- Cloud-data cache with a platform dimension (2026-08, dual-source):
-- platform = 'scoresaber' | 'beatleader'; each platform keeps its own rows so
-- switching the active data source never touches the other platform's data.
CREATE TABLE IF NOT EXISTS scoresaber_cache (
    platform    TEXT NOT NULL DEFAULT 'scoresaber',
    player_id   TEXT NOT NULL,
    fetched_at  TEXT,
    profile_json TEXT,
    scores_json  TEXT,
    PRIMARY KEY (platform, player_id)
);

-- Per-player dynamic star palette (yellow baseline etc., 2026 spec):
-- computed from the player's own records on the ACTIVE platform (top-20 by pp),
-- stored so the palette works offline after one successful fetch. See
-- backend/analysis/player_palette.py.
CREATE TABLE IF NOT EXISTS player_palette_cache (
    platform      TEXT NOT NULL DEFAULT 'scoresaber',
    player_id     TEXT NOT NULL,   -- ScoreSaber ID (= Steam ID, parsed from BSOR; shared by both platforms)
    computed_at   TEXT,            -- last computation time (UTC)
    stage         TEXT,            -- 初级/休闲 | 进阶/高阶 | 竞技向
    max_single_pp REAL,
    fallback_stars REAL,
    yellow_stars  REAL,            -- the yellow baseline (star rating)
    sample_count  INTEGER,         -- records used (top-20 by pp, capped)
    method        TEXT,            -- top20 | blend8-19 | fallback | unknown
    valid_count   INTEGER,
    nf_excluded   INTEGER,
    PRIMARY KEY (platform, player_id)
);

CREATE TABLE IF NOT EXISTS scan_state (
    key   TEXT PRIMARY KEY,        -- replay directory path
    value TEXT                     -- JSON
);

-- (platform, map_hash, difficulty) -> player-score stars/pp index
-- (originally _tools/migrate_db_v2; platform added 2026-08)
CREATE TABLE IF NOT EXISTS map_ranked_cache (
    platform    TEXT NOT NULL DEFAULT 'scoresaber',
    map_hash    TEXT NOT NULL,
    difficulty  TEXT NOT NULL,
    stars       REAL,
    pp          REAL,
    ranked      INTEGER DEFAULT 0,
    fetched_at  TEXT,
    PRIMARY KEY (platform, map_hash, difficulty)
);

-- Map-rooted leaderboard cache: one map_hash maps to multiple difficulties/modes;
-- platform-scoped (ScoreSaber and BeatLeader keep separate star ratings).
-- leaderboard_id is TEXT: ScoreSaber ids are integers, BeatLeader ids are
-- short strings ("1232e71").
CREATE TABLE IF NOT EXISTS scoresaber_leaderboards (
    platform       TEXT NOT NULL DEFAULT 'scoresaber',
    leaderboard_id TEXT NOT NULL,
    map_hash       TEXT NOT NULL,
    difficulty_rank INTEGER,       -- 1/3/5/7/9
    difficulty_name TEXT,          -- Easy/Normal/Hard/Expert/ExpertPlus
    game_mode      TEXT,           -- SoloStandard / SoloOneSaber / ...
    difficulty_raw TEXT,           -- _Expert_SoloStandard
    song_name      TEXT,
    level_author   TEXT,
    stars          REAL,
    ranked         INTEGER,
    qualified      INTEGER,
    loved          INTEGER,
    max_pp         REAL,
    plays          INTEGER,
    last_synced    TEXT,
    PRIMARY KEY (platform, leaderboard_id)
);
CREATE INDEX IF NOT EXISTS idx_ssl_hash ON scoresaber_leaderboards(map_hash);
CREATE INDEX IF NOT EXISTS idx_ssl_hash_diff
    ON scoresaber_leaderboards(map_hash, difficulty_name);
-- idx_ssl_platform is created in repository._migrate: it depends on the
-- platform column, which pre-platform legacy DBs lack until rebuild.
"""
