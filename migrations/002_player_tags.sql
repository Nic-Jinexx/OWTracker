-- Operator-assigned dots, and a timestamp for player notes.
--
-- Two sets of three:
--   'role' — orange / blue / green. The codes are literally 'tank', 'damage'
--            and 'support' so player_tags.tag_code JOINs against
--            match_players.role. That join is the whole reason this is a table
--            rather than six boolean columns on `players`: it answers "do the
--            people I marked as tanks actually play tank, and do I win with
--            them" for free.
--   'free' — red / pink / white. Deliberately meaningless to the application.
--            The operator decides what they mean; the app never interprets
--            them and must never grow a feature that does.
--
-- Tags are sparse: most players who appear on a scoreboard are strangers who
-- will never be tagged. Columns would write a zero for every one of them.

CREATE TABLE tags (
    code       TEXT    PRIMARY KEY,
    tag_set    TEXT    NOT NULL CHECK (tag_set IN ('role', 'free')),
    color      TEXT    NOT NULL,
    label      TEXT    NOT NULL,
    sort_order INTEGER NOT NULL
);

-- Seeded here, not in seed/*.json. seed/ exists because heroes and maps grow
-- with game patches; the dot vocabulary is a schema decision, not game data.
INSERT INTO tags (code, tag_set, color, label, sort_order) VALUES
    ('tank',    'role', 'orange', 'Tank',    1),
    ('damage',  'role', 'blue',   'Damage',  2),
    ('support', 'role', 'green',  'Support', 3),
    ('red',     'free', 'red',    'Red',     4),
    ('pink',    'free', 'pink',   'Pink',    5),
    ('white',   'free', 'white',  'White',   6);

CREATE TABLE player_tags (
    player_id  INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    tag_code   TEXT    NOT NULL REFERENCES tags(code),
    created_at TEXT    NOT NULL,
    PRIMARY KEY (player_id, tag_code)
);

CREATE INDEX idx_player_tags_tag ON player_tags(tag_code);

-- players.notes has existed since 001 and was never written. Record when it
-- was, so "recently annotated" needs no second table.
ALTER TABLE players ADD COLUMN notes_updated_at TEXT;
