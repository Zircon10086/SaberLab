"""SQLite schema（设计文档 §17）。

原则：
- 原始 BSOR 保留文件不入库；SQLite 只存摘要与指标。
- replay 主键 = 文件内容 sha256（设计文档 §7.4）。
- 每次重新分析产生新的 analysis_version。
- 迁移史全部收敛到这里 + repository._migrate()：全新库 executescript(SCHEMA)
  即得到完整结构；旧库由 _migrate() 幂等补列/补表（不再依赖 _tools 脚本）。
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
    song_length     REAL,              -- 歌曲时长（秒），来自 info.dat
    version         TEXT,
    difficulties    TEXT,          -- JSON: [{characteristic, difficulty, label}]
    info_json       TEXT,          -- 完整 info.dat（压缩存储可选，先存原文）
    hash_source     TEXT,          -- songcore_cache | computed
    ranked_difficulty TEXT,        -- ranked 难度名称（如 "Expert"）
    stars           REAL,          -- 星级评分
    scoresaber_updated TEXT,      -- 最后更新时间
    beatmap_key     TEXT,          -- BeatSaver key（从文件夹名 "16633 (song)" 提取）
    nps_json        TEXT DEFAULT '{}',  -- JSON: {"Standard|Expert": nps, ...}
    last_scanned    TEXT
);

CREATE TABLE IF NOT EXISTS replays (
    replay_id       TEXT PRIMARY KEY,      -- sha256(file bytes)
    file_path       TEXT,
    file_name       TEXT,
    file_size       INTEGER,
    file_mtime      REAL,
    timestamp       INTEGER,               -- 游玩开始 unix 时间
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
    score           INTEGER,               -- Replay 记录的总分
    score_recomputed INTEGER,              -- 解析器独立重算的总分（校验用）
    score_effective INTEGER,               -- 有效分（NF/Fail 时减半，否则 = score）
    has_nf          INTEGER DEFAULT 0,     -- modifiers 含 NF（实际 Fail 过）
    jump_distance   REAL,
    left_handed     INTEGER,
    height          REAL,
    start_time      REAL,
    fail_time       REAL,
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
    accuracy        REAL,                  -- 重算 accuracy（BeatLeader 口径）
    max_combo       INTEGER,
    full_combo      INTEGER,
    completion_status TEXT DEFAULT 'completed',  -- completed | failed | incomplete | pending
    profile_id      TEXT,
    analysis_version INTEGER DEFAULT 1,
    status          TEXT DEFAULT 'parsed', -- parsed | analyzed | error
    analysis_status TEXT DEFAULT 'pending', -- pending | analyzed（分层分析状态机）
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
    event_type      INTEGER,       -- 有效类型（bomb 已重标记）
    saber           TEXT,          -- left/right
    scoring_type    INTEGER,
    line_index      INTEGER,
    layer           INTEGER,
    color_type      INTEGER,
    cut_direction   INTEGER,
    before_score    INTEGER,       -- 0..70
    center_score    INTEGER,       -- 0..15
    after_score     INTEGER,       -- 0..30
    note_score      INTEGER,       -- 三项合计 0..115
    cut_distance    REAL,          -- 米
    saber_speed     REAL,
    time_deviation  REAL,
    PRIMARY KEY (replay_id, idx)
);

CREATE TABLE IF NOT EXISTS metrics (
    replay_id   TEXT NOT NULL,
    scope       TEXT NOT NULL,     -- overall | left | right | fatigue
    name        TEXT NOT NULL,
    value       REAL,
    detail      TEXT,              -- 可选 JSON 细节
    PRIMARY KEY (replay_id, scope, name)
);

CREATE TABLE IF NOT EXISTS windows (
    replay_id   TEXT NOT NULL,
    window_idx  INTEGER NOT NULL,
    t_start     REAL,
    t_end       REAL,
    metrics_json TEXT,             -- 该窗口全部指标
    PRIMARY KEY (replay_id, window_idx)
);

CREATE TABLE IF NOT EXISTS motion_series (
    replay_id   TEXT PRIMARY KEY,
    series_json TEXT               -- 降采样后的手速/角速度时间序列（图表用）
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

CREATE TABLE IF NOT EXISTS scoresaber_cache (
    player_id   TEXT PRIMARY KEY,
    fetched_at  TEXT,
    profile_json TEXT,
    scores_json  TEXT
);

CREATE TABLE IF NOT EXISTS scan_state (
    key   TEXT PRIMARY KEY,        -- replay 目录路径
    value TEXT                     -- JSON
);

-- (map_hash, difficulty) -> 玩家成绩星级/pp 索引（原 _tools/migrate_db_v2）
CREATE TABLE IF NOT EXISTS map_ranked_cache (
    map_hash    TEXT NOT NULL,
    difficulty  TEXT NOT NULL,
    stars       REAL,
    pp          REAL,
    ranked      INTEGER DEFAULT 0,
    fetched_at  TEXT,
    PRIMARY KEY (map_hash, difficulty)
);

-- 以谱面为根的 ScoreSaber leaderboard 缓存：一个 map_hash 对应多个难度/模式
-- （原 _tools/migrate_db_v4；通过 get-difficulties/{hash} 枚举 + by-id 补齐星级）
CREATE TABLE IF NOT EXISTS scoresaber_leaderboards (
    leaderboard_id  INTEGER PRIMARY KEY,
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
    last_synced    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ssl_hash ON scoresaber_leaderboards(map_hash);
CREATE INDEX IF NOT EXISTS idx_ssl_hash_diff
    ON scoresaber_leaderboards(map_hash, difficulty_name);
"""
