-- Seasons: operator-defined windows of time to group matches into.
--
-- Deliberately not Blizzard's competitive seasons. Those get renumbered,
-- extended and reshuffled, and hardcoding a calendar would mean shipping an
-- update every time they changed it. A season here is whatever the operator
-- says it is: a name and a date range. "Season 18", "the month we all played
-- Open Queue", "before the rework" are all equally valid.
--
-- `ends_on` is nullable and means "still running". Exactly one open-ended
-- season is the normal state, and matches played today land in it.

CREATE TABLE seasons (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    starts_on  TEXT NOT NULL,          -- ISO date, inclusive
    ends_on    TEXT,                   -- ISO date, inclusive; NULL = ongoing
    notes      TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_seasons_range ON seasons(starts_on, ends_on);

-- ON DELETE SET NULL: deleting a season is a regrouping, not a reason to lose
-- six months of matches. They fall back to unassigned and can be regrouped.
-- (Migration 003 exists because the same reference on drafts was missing one.)
ALTER TABLE matches ADD COLUMN season_id INTEGER REFERENCES seasons(id) ON DELETE SET NULL;

CREATE INDEX idx_matches_season ON matches(season_id);
