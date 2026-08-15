-- Learned hero portraits.
--
-- The shipped library (seed/hero_hashes.json) holds ten heroes of the forty-odd
-- in the game: the ones that happened to appear in the sample corpus and were
-- labelled by hand. Every other portrait came back blank forever, because the
-- only way to add one was to re-run tools/build_hero_hashes.py against a corpus
-- the operator does not have and cannot build without the screenshots.
--
-- This is the trick `player_nameplates` already plays for names, applied to
-- heroes: picking a hero in the review grid stores the hash of the portrait it
-- was picked for, so the next screenshot recognizes it unaided. Each hero is
-- named once, ever, and the library converges on the operator's actual hero
-- pool instead of on whatever the corpus contained.
--
-- One row per observed hash and several per hero by design. The row background
-- is strongly team-coloured, so the same hero on the blue side and the red side
-- hashes a few bits apart and both are worth holding — the same reason
-- `load_hero_library` keys a *list* per hero.

CREATE TABLE hero_portraits (
    id            INTEGER PRIMARY KEY,
    hero_id       INTEGER NOT NULL REFERENCES heroes(id) ON DELETE CASCADE,
    phash         TEXT    NOT NULL,
    first_seen    TEXT    NOT NULL,
    times_matched INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_portraits_hero ON hero_portraits(hero_id);

-- Unique per hero, deliberately NOT globally unique. A global constraint would
-- abort the whole commit transaction the first time a hash taught to the wrong
-- hero was re-taught to the right one — exactly the moment correcting it has to
-- be easy. Commit refuses that insert in Python instead, where it can leave the
-- old claim standing and say so, rather than failing the match.
CREATE UNIQUE INDEX one_hash_per_hero ON hero_portraits(hero_id, phash);
