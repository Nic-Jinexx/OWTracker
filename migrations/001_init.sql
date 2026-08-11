-- OWTracker initial schema.
--
-- Design notes that are easy to lose:
--   * match_players.player_id is NULLABLE by design (invariant 4). An
--     anonymized or unreadable enemy row still records team, hero, and stats;
--     it just isn't attributed to anyone. The players table therefore only
--     ever contains names actually observed on a scoreboard.
--   * matches.result is NOT NULL and is the ONLY required field (invariant 2).
--     Everything else tolerates nulls so a sparse match is still a valid one.
--   * Drafts live in their own table as a JSON payload, so these tables only
--     ever hold committed data and no browse/aggregate query needs a status
--     filter.

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE heroes (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('tank', 'damage', 'support'))
);

CREATE TABLE maps (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL CHECK (mode IN ('control', 'escort', 'hybrid', 'push', 'flashpoint', 'clash'))
);

-- Sortable rank lookup. Comparisons use `ordinal`; display uses `name`.
CREATE TABLE ranks (
    id       INTEGER PRIMARY KEY,
    tier     TEXT    NOT NULL,
    division INTEGER NOT NULL,
    ordinal  INTEGER NOT NULL UNIQUE,
    name     TEXT    NOT NULL UNIQUE
);

CREATE TABLE players (
    id           INTEGER PRIMARY KEY,
    display_name TEXT    NOT NULL UNIQUE,
    first_seen   TEXT    NOT NULL,
    last_seen    TEXT    NOT NULL,
    games_seen   INTEGER NOT NULL DEFAULT 0,
    notes        TEXT
);

-- Names are recognized, not read. One row per observed nameplate crop; a
-- single person legitimately produces several (title lines, capture
-- resolutions, cosmetics) and each resolves back to the same player.
CREATE TABLE player_nameplates (
    id            INTEGER PRIMARY KEY,
    player_id     INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    phash         TEXT    NOT NULL,
    width_px      INTEGER,
    first_seen    TEXT    NOT NULL,
    times_matched INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_nameplates_player ON player_nameplates(player_id);
CREATE INDEX idx_nameplates_phash  ON player_nameplates(phash);

CREATE TABLE matches (
    id               INTEGER PRIMARY KEY,
    played_at        TEXT,
    map_id           INTEGER REFERENCES maps(id),
    mode             TEXT,
    result           TEXT NOT NULL CHECK (result IN ('win', 'loss', 'draw')),
    team_size        INTEGER,
    duration_seconds INTEGER,
    rank_range_low   INTEGER REFERENCES ranks(id),
    rank_range_high  INTEGER REFERENCES ranks(id),
    notes            TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX idx_matches_played_at ON matches(played_at);
CREATE INDEX idx_matches_map       ON matches(map_id);

CREATE TABLE match_players (
    id           INTEGER PRIMARY KEY,
    match_id     INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    player_id    INTEGER REFERENCES players(id),
    team         TEXT    NOT NULL CHECK (team IN ('ally', 'enemy')),
    is_me        INTEGER NOT NULL DEFAULT 0 CHECK (is_me IN (0, 1)),
    role         TEXT CHECK (role IS NULL OR role IN ('tank', 'damage', 'support')),
    hero_id      INTEGER REFERENCES heroes(id),
    eliminations INTEGER,
    assists      INTEGER,
    deaths       INTEGER,
    damage       INTEGER,
    healing      INTEGER,
    mitigation   INTEGER,
    row_index    INTEGER
);

-- Invariant 11: two is_me rows in one match are structurally impossible.
-- The "exactly one when rows exist" half is enforced at commit time.
CREATE UNIQUE INDEX one_me_per_match ON match_players(match_id) WHERE is_me = 1;
CREATE INDEX idx_mp_match  ON match_players(match_id);
CREATE INDEX idx_mp_player ON match_players(player_id);
CREATE INDEX idx_mp_hero   ON match_players(hero_id);

-- Ban attribution is not shown in game, so bans are match-level only.
CREATE TABLE match_bans (
    id         INTEGER PRIMARY KEY,
    match_id   INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    hero_id    INTEGER NOT NULL REFERENCES heroes(id),
    slot_index INTEGER
);
CREATE INDEX idx_bans_match ON match_bans(match_id);

CREATE TABLE match_sources (
    id          INTEGER PRIMARY KEY,
    match_id    INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    file_path   TEXT    NOT NULL,
    file_sha256 TEXT    NOT NULL,
    kind        TEXT    NOT NULL CHECK (kind IN ('endgame_report', 'in_game_scoreboard')),
    ingested_at TEXT    NOT NULL
);
CREATE INDEX idx_sources_match  ON match_sources(match_id);
CREATE INDEX idx_sources_sha256 ON match_sources(file_sha256);

-- Written on commit for every non-manual field. `superseded_value` records
-- the losing side of a source disagreement so a silent auto-merge stays
-- auditable.
CREATE TABLE field_provenance (
    id               INTEGER PRIMARY KEY,
    table_name       TEXT NOT NULL,
    row_id           INTEGER NOT NULL,
    column_name      TEXT NOT NULL,
    source           TEXT NOT NULL CHECK (source IN ('template', 'manual')),
    confidence       REAL,
    superseded_value TEXT
);
CREATE INDEX idx_prov_row ON field_provenance(table_name, row_id);

CREATE TABLE drafts (
    id                  INTEGER PRIMARY KEY,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open', 'committed', 'abandoned')),
    payload             TEXT NOT NULL,
    committed_match_id  INTEGER REFERENCES matches(id)
);
CREATE INDEX idx_drafts_status ON drafts(status);
