-- drafts.committed_match_id gains ON DELETE SET NULL.
--
-- 001 declared the reference without a delete action, so SQLite's default
-- (NO ACTION) applied: deleting a match that any committed draft pointed at
-- raised a foreign-key error and DELETE /api/matches/{id} simply failed. The
-- match rows themselves all cascade — match_players, match_bans,
-- match_sources — so the draft was the one thing standing in the way.
--
-- SET NULL rather than CASCADE. A committed draft is the record of an ingest
-- that happened: its payload holds the per-field sources and confidences that
-- produced the match, and status='committed' stays true even after the match
-- is gone. Cascading would delete that history as a side effect of a delete
-- the operator aimed at something else. A NULL id reads correctly as "the
-- match this produced no longer exists", and still blocks a re-commit.
--
-- SQLite cannot alter a foreign key in place, so the table is rebuilt. Nothing
-- references drafts, which makes this a plain child-table swap rather than the
-- full 12-step dance.

CREATE TABLE drafts_new (
    id                  INTEGER PRIMARY KEY,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open', 'committed', 'abandoned')),
    payload             TEXT NOT NULL,
    committed_match_id  INTEGER REFERENCES matches(id) ON DELETE SET NULL
);

INSERT INTO drafts_new (id, created_at, updated_at, status, payload, committed_match_id)
    SELECT id, created_at, updated_at, status, payload, committed_match_id FROM drafts;

DROP INDEX IF EXISTS idx_drafts_status;
DROP TABLE drafts;
ALTER TABLE drafts_new RENAME TO drafts;

CREATE INDEX idx_drafts_status ON drafts(status);
