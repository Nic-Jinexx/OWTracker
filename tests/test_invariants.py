"""Invariant tests.

Each of these corresponds to a numbered invariant in CLAUDE-OWTRACKER.md. They
are the tests that must never be allowed to fail, because each one guards a
property the whole design leans on.
"""

from __future__ import annotations

import sqlite3
import unittest

from app import draft as draft_module
from app.commit import CommitError, commit_draft
from helpers import DatabaseTestCase


class TestSchemaInvariants(DatabaseTestCase):

    def test_seeding_is_idempotent(self):
        """Re-running startup must never duplicate seed rows — that's what
        lets a newly released hero be appended to the JSON later."""
        from app import db

        before = self.conn.execute("SELECT COUNT(*) AS n FROM heroes").fetchone()["n"]
        db.seed(self.conn)
        db.seed(self.conn)
        after = self.conn.execute("SELECT COUNT(*) AS n FROM heroes").fetchone()["n"]
        self.assertEqual(before, after)
        self.assertGreater(after, 30)

    def test_ranks_are_globally_sortable(self):
        rows = self.conn.execute("SELECT ordinal, name FROM ranks ORDER BY ordinal").fetchall()
        self.assertEqual(len(rows), 40)
        self.assertEqual(rows[0]["name"], "Bronze 5")
        self.assertEqual(rows[-1]["name"], "Champion 1")
        self.assertEqual([r["ordinal"] for r in rows], list(range(1, 41)))

    def test_result_is_not_nullable(self):
        """Invariant 2."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO matches (result, created_at) VALUES (NULL, '2026-01-01')"
            )

    def test_result_is_constrained_to_three_values(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO matches (result, created_at) VALUES ('victory', '2026-01-01')"
            )

    def test_two_is_me_rows_are_impossible(self):
        """Invariant 11 — enforced by the schema, not by application code."""
        match_id = self._bare_match()
        self.conn.execute(
            "INSERT INTO match_players (match_id, team, is_me) VALUES (?, 'ally', 1)", (match_id,)
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO match_players (match_id, team, is_me) VALUES (?, 'ally', 1)",
                (match_id,),
            )

    def test_many_non_me_rows_are_fine(self):
        match_id = self._bare_match()
        for _ in range(9):
            self.conn.execute(
                "INSERT INTO match_players (match_id, team, is_me) VALUES (?, 'enemy', 0)",
                (match_id,),
            )
        count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM match_players WHERE match_id = ?", (match_id,)
        ).fetchone()["n"]
        self.assertEqual(count, 9)

    def test_player_id_is_nullable(self):
        """Invariant 4 — an anonymized enemy still records hero and stats."""
        match_id = self._bare_match()
        self.conn.execute(
            "INSERT INTO match_players (match_id, player_id, team, hero_id, eliminations) "
            "VALUES (?, NULL, 'enemy', ?, 14)",
            (match_id, self.hero_id("Reaper")),
        )
        row = self.conn.execute(
            "SELECT player_id, eliminations FROM match_players WHERE match_id = ?", (match_id,)
        ).fetchone()
        self.assertIsNone(row["player_id"])
        self.assertEqual(row["eliminations"], 14)

    def test_deleting_a_match_leaves_no_orphans(self):
        match_id = self._bare_match()
        self.conn.execute(
            "INSERT INTO match_players (match_id, team) VALUES (?, 'ally')", (match_id,)
        )
        self.conn.commit()
        self.conn.execute("DELETE FROM matches WHERE id = ?", (match_id,))
        self.conn.commit()
        orphans = self.conn.execute("SELECT COUNT(*) AS n FROM match_players").fetchone()["n"]
        self.assertEqual(orphans, 0)

    def _bare_match(self) -> int:
        cursor = self.conn.execute(
            "INSERT INTO matches (result, created_at) VALUES ('win', '2026-01-01')"
        )
        self.conn.commit()
        return int(cursor.lastrowid)


class TestCommitPath(DatabaseTestCase):
    """Invariants 1, 2, 9, 10, 11 as exercised through the only writer."""

    def test_result_only_match_commits(self):
        """The minimum viable match: a result and nothing else."""
        payload = draft_module.empty_draft(5)
        payload["meta"]["result"] = draft_module.field("loss")
        draft_id = self.insert_draft(payload)

        match_id = commit_draft(self.conn, draft_id)

        row = self.conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        self.assertEqual(row["result"], "loss")
        players = self.conn.execute(
            "SELECT COUNT(*) AS n FROM match_players WHERE match_id = ?", (match_id,)
        ).fetchone()["n"]
        self.assertEqual(players, 0)

    def test_commit_without_result_is_refused(self):
        draft_id = self.insert_draft(draft_module.empty_draft(5))
        with self.assertRaises(CommitError) as caught:
            commit_draft(self.conn, draft_id)
        self.assertTrue(any("Result is required" in p for p in caught.exception.problems))

    def test_rows_without_is_me_are_refused(self):
        """Populated roster but nobody marked as you — the mistake you'd
        actually make after typing ten rows."""
        payload = draft_module.empty_draft(5)
        payload["meta"]["result"] = draft_module.field("win")
        payload["rows"][0]["player_name"] = draft_module.field("SOMEONE")
        draft_id = self.insert_draft(payload)

        with self.assertRaises(CommitError) as caught:
            commit_draft(self.conn, draft_id)
        self.assertTrue(any("Mark which row is you" in p for p in caught.exception.problems))

    def test_full_roster_commits_and_creates_players(self):
        payload = draft_module.empty_draft(5)
        payload["meta"]["result"] = draft_module.field("win")
        payload["meta"]["map_id"] = draft_module.field(self.map_id("Ilios"))
        payload["rows"][0]["is_me"] = True
        payload["rows"][0]["player_name"] = draft_module.field("NICCS")
        payload["rows"][0]["hero_id"] = draft_module.field(self.hero_id("Ana"))
        payload["rows"][0]["eliminations"] = draft_module.field(12)
        payload["rows"][1]["player_name"] = draft_module.field("PLAYER_B")
        # An anonymized enemy: hero and stats, no name.
        payload["rows"][5]["hero_id"] = draft_module.field(self.hero_id("Reaper"))
        payload["rows"][5]["deaths"] = draft_module.field(9)
        draft_id = self.insert_draft(payload)

        match_id = commit_draft(self.conn, draft_id)

        # Which players were created, not what order they sort in — the latter
        # is a property of the names the test happens to pick.
        names = {r["display_name"] for r in self.conn.execute(
            "SELECT display_name FROM players")}
        self.assertEqual(names, {"PLAYER_B", "NICCS"})

        anonymous = self.conn.execute(
            "SELECT player_id, deaths FROM match_players "
            "WHERE match_id = ? AND team = 'enemy' AND hero_id IS NOT NULL",
            (match_id,),
        ).fetchone()
        self.assertIsNone(anonymous["player_id"])
        self.assertEqual(anonymous["deaths"], 9)

    def test_commit_is_atomic(self):
        """Invariant 9: a failure part-way leaves nothing behind."""
        payload = draft_module.empty_draft(5)
        payload["meta"]["result"] = draft_module.field("win")
        payload["rows"][0]["is_me"] = True
        payload["rows"][0]["player_name"] = draft_module.field("NICCS")
        # A hero id that violates the foreign key, discovered mid-transaction.
        payload["rows"][1]["hero_id"] = draft_module.field(999999)
        draft_id = self.insert_draft(payload)

        with self.assertRaises(sqlite3.IntegrityError):
            commit_draft(self.conn, draft_id)

        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"], 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM match_players").fetchone()["n"], 0)
        self.assertEqual(
            self.conn.execute("SELECT status FROM drafts WHERE id = ?", (draft_id,))
                .fetchone()["status"],
            "open",
        )

    def test_double_commit_is_refused(self):
        payload = draft_module.empty_draft(5)
        payload["meta"]["result"] = draft_module.field("draw")
        draft_id = self.insert_draft(payload)
        commit_draft(self.conn, draft_id)
        with self.assertRaises(CommitError):
            commit_draft(self.conn, draft_id)

    def test_deleting_a_match_releases_its_committed_draft(self):
        """`drafts.committed_match_id` used to have no ON DELETE, so deleting a
        match any committed draft pointed at raised a foreign-key error and the
        route just failed. It is ON DELETE SET NULL now: the match goes, the
        record that it was ingested stays, and the draft still cannot be
        re-committed — that would resurrect a match the operator deleted."""
        from app.routes.matches import delete_match

        payload = draft_module.empty_draft(5)
        payload["meta"]["result"] = draft_module.field("loss")
        draft_id = self.insert_draft(payload)
        match_id = commit_draft(self.conn, draft_id)
        self.conn.commit()

        delete_match(match_id)

        row = self.conn.execute(
            "SELECT status, committed_match_id FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        self.assertEqual(row["status"], "committed")
        self.assertIsNone(row["committed_match_id"])
        self.assertIsNone(
            self.conn.execute("SELECT 1 FROM matches WHERE id = ?", (match_id,)).fetchone())
        with self.assertRaises(CommitError):
            commit_draft(self.conn, draft_id)

    def test_repeat_player_is_reused_not_duplicated(self):
        for _ in range(3):
            payload = draft_module.empty_draft(5)
            payload["meta"]["result"] = draft_module.field("win")
            payload["rows"][0]["is_me"] = True
            payload["rows"][0]["player_name"] = draft_module.field("NICCS")
            payload["rows"][1]["player_name"] = draft_module.field("PLAYER_B")
            commit_draft(self.conn, self.insert_draft(payload))

        rows = self.conn.execute(
            "SELECT display_name, games_seen FROM players ORDER BY display_name").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["games_seen"] for r in rows}, {3})

    def test_provenance_records_template_fields_only(self):
        """Invariant 3: extracted values are auditable; manual ones are not
        noise in the provenance table."""
        payload = draft_module.empty_draft(5)
        payload["meta"]["result"] = draft_module.field("win")
        payload["rows"][0]["is_me"] = True
        payload["rows"][0]["player_name"] = draft_module.field("NICCS")
        payload["rows"][0]["eliminations"] = draft_module.field(
            14, source="template", origin="endgame_report", confidence=0.93)
        draft_id = self.insert_draft(payload)

        commit_draft(self.conn, draft_id)

        rows = self.conn.execute("SELECT * FROM field_provenance").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["column_name"], "eliminations")
        self.assertEqual(rows[0]["source"], "template")
        self.assertAlmostEqual(rows[0]["confidence"], 0.93)


class TestMergeRules(unittest.TestCase):
    """The precedence rules from CLAUDE-OWTRACKER.md → Ingestion."""

    def test_empty_field_is_filled(self):
        conflicts: list = []
        result, loser = draft_module.merge_field(
            draft_module.field(None),
            draft_module.field(14, source="template", origin="endgame_report", confidence=0.9),
            "rows[ally:0].eliminations", conflicts)
        self.assertEqual(result["value"], 14)
        self.assertEqual(conflicts, [])
        self.assertIsNone(loser)

    def test_endgame_beats_in_game_silently(self):
        conflicts: list = []
        result, loser = draft_module.merge_field(
            draft_module.field(6, source="template", origin="in_game_scoreboard", confidence=0.9),
            draft_module.field(14, source="template", origin="endgame_report", confidence=0.9),
            "rows[ally:0].eliminations", conflicts)
        self.assertEqual(result["value"], 14)
        self.assertEqual(conflicts, [])
        self.assertEqual(loser, "6")  # kept for provenance

    def test_in_game_never_overwrites_endgame(self):
        conflicts: list = []
        result, _ = draft_module.merge_field(
            draft_module.field(14, source="template", origin="endgame_report", confidence=0.9),
            draft_module.field(6, source="template", origin="in_game_scoreboard", confidence=0.9),
            "rows[ally:0].eliminations", conflicts)
        self.assertEqual(result["value"], 14)
        self.assertEqual(conflicts, [])

    def test_manual_edit_is_never_silently_overwritten(self):
        conflicts: list = []
        result, _ = draft_module.merge_field(
            draft_module.field(9),  # operator typed this
            draft_module.field(8, source="template", origin="endgame_report", confidence=0.95),
            "rows[ally:3].deaths", conflicts)
        self.assertEqual(result["value"], 9)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["mine"], 9)
        self.assertEqual(conflicts[0]["theirs"], 8)

    def test_agreement_is_not_a_conflict(self):
        conflicts: list = []
        draft_module.merge_field(
            draft_module.field(9),
            draft_module.field(9, source="template", origin="endgame_report", confidence=0.95),
            "rows[ally:3].deaths", conflicts)
        self.assertEqual(conflicts, [])

    def test_conflicts_block_commit_until_resolved(self):
        payload = draft_module.empty_draft(5)
        payload["meta"]["result"] = draft_module.field("win")
        payload["conflicts"] = [{"path": "rows[ally:3].deaths", "mine": 9, "theirs": 8}]
        problems = draft_module.validate_for_commit(payload)
        self.assertTrue(any("conflict" in p for p in problems))

        draft_module.resolve_conflict(payload, "rows[ally:3].deaths", "theirs")
        self.assertEqual(payload["conflicts"], [])
        # Taking 'theirs' writes a value into row 3, which makes the roster
        # populated — so the is_me requirement now applies. The conflict
        # itself is what must have cleared.
        problems = draft_module.validate_for_commit(payload)
        self.assertFalse(any("conflict" in p for p in problems))
        self.assertEqual(payload["rows"][3]["deaths"]["value"], 8)

    def test_resolving_as_mine_keeps_the_typed_value(self):
        payload = draft_module.empty_draft(5)
        payload["rows"][3]["deaths"] = draft_module.field(9)
        payload["conflicts"] = [
            {"path": "rows[ally:3].deaths", "mine": 9, "theirs": 8,
             "theirs_origin": "endgame_report", "theirs_confidence": 0.95}
        ]
        draft_module.resolve_conflict(payload, "rows[ally:3].deaths", "mine")
        self.assertEqual(payload["rows"][3]["deaths"]["value"], 9)

    def test_rows_merge_by_position_not_by_hero(self):
        """Invariant 7: mirror picks must not be confused for one another."""
        base = draft_module.empty_draft(5)
        incoming = draft_module.empty_draft(5)
        # Same hero on both teams — the classic mirror pick.
        incoming["rows"][0]["hero_id"] = draft_module.field(
            42, source="template", origin="endgame_report", confidence=0.99)
        incoming["rows"][5]["hero_id"] = draft_module.field(
            42, source="template", origin="endgame_report", confidence=0.99)

        merged = draft_module.merge_draft(base, incoming)
        ally = [r for r in merged["rows"] if r["team"] == "ally"][0]
        enemy = [r for r in merged["rows"] if r["team"] == "enemy"][0]
        self.assertEqual(ally["hero_id"]["value"], 42)
        self.assertEqual(enemy["hero_id"]["value"], 42)
        self.assertEqual(merged["conflicts"], [])


class TestNameplateLearning(DatabaseTestCase):
    """Commit teaches the matcher. This loop existed in commit.py from the
    start and was never covered, because nothing produced a signature to feed
    it until now."""

    def _commit(self, *, name=None, player_id=None, signature=None, width=None):
        payload = draft_module.empty_draft(5)
        payload["meta"]["result"] = draft_module.field("win")
        row = payload["rows"][0]
        row["is_me"] = True
        if name:
            row["player_name"] = draft_module.field(name)
        if player_id:
            row["player_id"] = player_id
        if signature:
            row["nameplate_phash"] = signature
            row["nameplate_width"] = width
        return commit_draft(self.conn, self.insert_draft(payload))

    def _plates(self):
        return self.conn.execute(
            "SELECT player_id, phash, width_px, times_matched FROM player_nameplates"
        ).fetchall()

    def test_a_typed_name_teaches_its_signature(self):
        self._commit(name="PLAYER_A", signature="ab" * 60, width=332)
        plates = self._plates()
        self.assertEqual(len(plates), 1)
        self.assertEqual(plates[0]["phash"], "ab" * 60)
        self.assertEqual(plates[0]["times_matched"], 1)

    def test_the_width_is_recorded(self):
        """`commit.py` has always read `nameplate_width`; nothing set it until
        the extractor did, so the column was silently always NULL."""
        self._commit(name="PLAYER_A", signature="cd" * 60, width=332)
        self.assertEqual(self._plates()[0]["width_px"], 332)

    def test_seeing_the_same_signature_again_counts_it_rather_than_duplicating(self):
        self._commit(name="PLAYER_A", signature="ab" * 60, width=332)
        player = self.conn.execute(
            "SELECT id FROM players WHERE display_name = 'PLAYER_A'").fetchone()["id"]
        self._commit(player_id=player, signature="ab" * 60, width=332)
        plates = self._plates()
        self.assertEqual(len(plates), 1)
        self.assertEqual(plates[0]["times_matched"], 2)

    def test_a_new_appearance_adds_a_second_signature(self):
        """The whole design: an animated nameplate accumulates coverage of its
        frames, so recall improves the more often someone is seen."""
        self._commit(name="PLAYER_A", signature="ab" * 60, width=332)
        player = self.conn.execute(
            "SELECT id FROM players WHERE display_name = 'PLAYER_A'").fetchone()["id"]
        self._commit(player_id=player, signature="ef" * 60, width=330)
        plates = self._plates()
        self.assertEqual(len(plates), 2)
        self.assertEqual({p["player_id"] for p in plates}, {player})

    def test_a_signature_with_no_player_is_not_stored(self):
        """An unrecognized, un-named row has nobody to attribute the plate to,
        and guessing would be how a stranger's signature ends up under a
        teammate."""
        self._commit(signature="ab" * 60, width=332)
        self.assertEqual(self._plates(), [])


if __name__ == "__main__":
    unittest.main()
