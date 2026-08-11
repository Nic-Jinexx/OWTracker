"""Aggregate and player-page behavior.

The load-bearing rule here is the small-sample one: a win rate computed from
fewer than five games must never be presented as a bare percentage.
"""

from __future__ import annotations

import unittest

from fastapi import HTTPException

from app import analysis
from app import draft as draft_module
from app.commit import commit_draft
from app.routes import players as players_module
from app.routes import stats as stats_module
from helpers import DatabaseTestCase


class AnalysisTestCase(DatabaseTestCase):

    def log(self, result, *, map_name=None, my_hero=None, teammates=(), enemies=(),
            bans=(), ally_heroes=(), enemy_heroes=(), ally_roles=(), enemy_roles=(),
            team_size=5, played_at=None, mode=None):
        """Commit one match through the real commit path.

        `result` is always the OPERATOR's result, because that is what the
        column means. A test asserting what an enemy-side player saw should log
        the operator's outcome and expect the inverse — anything else would be
        testing the fixture instead of the code.

        Every argument beyond `result` is optional and keyword-only, so the
        tests written before this grew arguments still read the same way.
        """
        payload = draft_module.empty_draft(team_size)
        payload["meta"]["result"] = draft_module.field(result)
        payload["meta"]["team_size"] = draft_module.field(team_size)
        if map_name:
            payload["meta"]["map_id"] = draft_module.field(self.map_id(map_name))
        if mode:
            payload["meta"]["mode"] = draft_module.field(mode)
        if played_at:
            payload["meta"]["played_at"] = draft_module.field(played_at)

        payload["rows"][0]["is_me"] = True
        payload["rows"][0]["player_name"] = draft_module.field("NICCS")
        if my_hero:
            payload["rows"][0]["hero_id"] = draft_module.field(self.hero_id(my_hero))

        for offset, name in enumerate(teammates, start=1):
            payload["rows"][offset]["player_name"] = draft_module.field(name)
        for offset, name in enumerate(enemies, start=team_size):
            payload["rows"][offset]["player_name"] = draft_module.field(name)

        # Ally hero 0 is the operator's own; ally_heroes fills from row 0 so a
        # test can describe a whole team composition in one argument.
        for offset, hero in enumerate(ally_heroes):
            if hero:
                payload["rows"][offset]["hero_id"] = draft_module.field(self.hero_id(hero))
        for offset, hero in enumerate(enemy_heroes):
            if hero:
                payload["rows"][team_size + offset]["hero_id"] = \
                    draft_module.field(self.hero_id(hero))
        for offset, role in enumerate(ally_roles):
            if role:
                payload["rows"][offset]["role"] = draft_module.field(role)
        for offset, role in enumerate(enemy_roles):
            if role:
                payload["rows"][team_size + offset]["role"] = draft_module.field(role)

        payload["bans"] = [{"hero_id": self.hero_id(h), "slot_index": i}
                           for i, h in enumerate(bans)]
        return commit_draft(self.conn, self.insert_draft(payload))

    def player_id(self, display_name: str) -> int:
        row = self.conn.execute(
            "SELECT id FROM players WHERE display_name = ?", (display_name,)).fetchone()
        assert row is not None, f"no player named {display_name!r}"
        return int(row["id"])


class TestSmallSampleRule(AnalysisTestCase):

    def test_four_games_is_not_reliable(self):
        for _ in range(4):
            self.log("win", teammates=["FRIEND"])
        friend = self.conn.execute(
            "SELECT id FROM players WHERE display_name = 'FRIEND'").fetchone()["id"]
        data = players_module.get_player(friend)
        self.assertEqual(data["with_me"]["games"], 4)
        self.assertEqual(data["with_me"]["win_rate"], 1.0)
        self.assertFalse(data["with_me"]["reliable"],
                         "4 games must be flagged as too small to trust")

    def test_five_games_is_reliable(self):
        for _ in range(5):
            self.log("win", teammates=["FRIEND"])
        friend = self.conn.execute(
            "SELECT id FROM players WHERE display_name = 'FRIEND'").fetchone()["id"]
        data = players_module.get_player(friend)
        self.assertTrue(data["with_me"]["reliable"])

    def test_zero_games_has_no_rate(self):
        data = stats_module.aggregates()
        self.assertEqual(data["overall"]["games"], 0)
        self.assertIsNone(data["overall"]["win_rate"],
                          "an empty sample must not produce a percentage")


class TestWithAndAgainst(AnalysisTestCase):

    def test_same_person_counted_on_the_correct_side(self):
        """'With' and 'against' are defined relative to my own row."""
        self.log("win", teammates=["RIVAL"])
        self.log("loss", enemies=["RIVAL"])
        self.log("loss", enemies=["RIVAL"])

        rival = self.conn.execute(
            "SELECT id FROM players WHERE display_name = 'RIVAL'").fetchone()["id"]
        data = players_module.get_player(rival)

        self.assertEqual(data["with_me"]["games"], 1)
        self.assertEqual(data["with_me"]["wins"], 1)
        self.assertEqual(data["against_me"]["games"], 2)
        self.assertEqual(data["against_me"]["wins"], 0)
        self.assertEqual(data["player"]["games_seen"], 3)


class TestFilters(AnalysisTestCase):

    def setUp(self):
        super().setUp()
        self.log("win",  map_name="Ilios",   my_hero="Ana",   teammates=["FRIEND"])
        self.log("loss", map_name="Ilios",   my_hero="Ana")
        self.log("win",  map_name="Nepal",   my_hero="Lucio", teammates=["FRIEND"])
        self.log("draw", map_name="Nepal",   my_hero="Ana")

    def test_unfiltered_counts_everything(self):
        data = stats_module.aggregates()
        self.assertEqual(data["overall"]["games"], 4)
        self.assertEqual(data["overall"]["wins"], 2)
        self.assertEqual(data["overall"]["draws"], 1)

    def test_hero_filter_is_from_my_perspective(self):
        ana = self.hero_id("Ana")
        data = stats_module.aggregates(hero_id=ana)
        self.assertEqual(data["overall"]["games"], 3)
        self.assertEqual(data["overall"]["wins"], 1)

    def test_map_filter(self):
        data = stats_module.aggregates(map_id=self.map_id("Ilios"))
        self.assertEqual(data["overall"]["games"], 2)

    def test_teammate_filter(self):
        friend = self.conn.execute(
            "SELECT id FROM players WHERE display_name = 'FRIEND'").fetchone()["id"]
        data = stats_module.aggregates(teammate_id=friend)
        self.assertEqual(data["overall"]["games"], 2)
        self.assertEqual(data["overall"]["wins"], 2)

    def test_filters_compose(self):
        friend = self.conn.execute(
            "SELECT id FROM players WHERE display_name = 'FRIEND'").fetchone()["id"]
        data = stats_module.aggregates(teammate_id=friend, hero_id=self.hero_id("Ana"))
        self.assertEqual(data["overall"]["games"], 1)

    def test_breakdown_totals_match_the_filtered_set(self):
        data = stats_module.aggregates()
        self.assertEqual(sum(r["games"] for r in data["by_map"]), 4)
        self.assertEqual(sum(r["games"] for r in data["by_hero"]), 4)


class TestExport(AnalysisTestCase):

    def test_table_whitelist_rejects_anything_else(self):
        from fastapi import HTTPException

        from app.routes import export

        with self.assertRaises(HTTPException):
            export.export_table("sqlite_master")
        with self.assertRaises(HTTPException):
            export.export_table("matches; DROP TABLE matches")

    def test_backup_is_a_readable_database(self):
        import sqlite3

        from app.routes import export

        self.log("win", map_name="Ilios")
        result = export.backup_database()

        from pathlib import Path

        from app import paths

        copy = Path(result["path"])
        if not copy.is_absolute():
            copy = paths.ROOT / copy
        self.assertTrue(copy.exists())
        conn = sqlite3.connect(copy)
        try:
            count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)


class TestSubjectPerspective(AnalysisTestCase):
    """Whose win is it?

    `matches.result` records what the OPERATOR did. Every aggregate about
    anyone else has to remap that, and getting it wrong inverts every
    enemy-side player's page in a way that looks entirely plausible.
    """

    def _subject(self, name):
        return analysis.Subject(player_id=self.player_id(name))

    def test_enemy_result_is_inverted(self):
        """The single most important assertion in this feature."""
        self.log("win", enemies=["RIVAL"])
        record = analysis.overall(self.conn, self._subject("RIVAL"), analysis.Filters())
        self.assertEqual(record["games"], 1)
        self.assertEqual(record["wins"], 0)
        self.assertEqual(record["losses"], 1, "I won, so they lost")

    def test_ally_result_is_not_inverted(self):
        self.log("win", teammates=["FRIEND"])
        record = analysis.overall(self.conn, self._subject("FRIEND"), analysis.Filters())
        self.assertEqual(record["wins"], 1)

    def test_a_draw_stays_a_draw_on_both_sides(self):
        self.log("draw", teammates=["FRIEND"], enemies=["RIVAL"])
        for name in ("FRIEND", "RIVAL"):
            with self.subTest(player=name):
                record = analysis.overall(self.conn, self._subject(name), analysis.Filters())
                self.assertEqual(record["draws"], 1)
                self.assertEqual(record["wins"], 0)
                self.assertEqual(record["losses"], 0)

    def test_someone_seen_on_both_sides_is_counted_from_each_side(self):
        self.log("win", teammates=["RIVAL"])       # they won with me
        self.log("loss", enemies=["RIVAL"])        # I lost, so they won
        self.log("win", enemies=["RIVAL"])         # I won, so they lost
        record = analysis.overall(self.conn, self._subject("RIVAL"), analysis.Filters())
        self.assertEqual(record["games"], 3)
        self.assertEqual(record["wins"], 2)
        self.assertEqual(record["losses"], 1)

    def test_the_operator_is_an_ally_of_other_subjects(self):
        """`by_teammate` used to exclude the operator with `is_me = 0`, which is
        only right when the subject is the operator."""
        self.log("win", teammates=["FRIEND"])
        allies = analysis.by_ally(self.conn, self._subject("FRIEND"), analysis.Filters())
        self.assertIn("NICCS", [a["name"] for a in allies])

    def test_the_operator_is_not_their_own_ally(self):
        self.log("win", teammates=["FRIEND"])
        allies = analysis.by_ally(self.conn, analysis.Subject(), analysis.Filters())
        self.assertEqual([a["name"] for a in allies], ["FRIEND"])

    def test_default_subject_matches_the_legacy_endpoint(self):
        """The generalisation must be a strict no-op for the operator."""
        self.log("win", map_name="Ilios", my_hero="Ana", teammates=["FRIEND"])
        self.log("loss", map_name="Nepal", my_hero="Ana", enemies=["RIVAL"])
        legacy = stats_module.aggregates()
        direct = analysis.overall(self.conn, analysis.Subject(), analysis.Filters())
        self.assertEqual(legacy["overall"], direct)

    def test_an_enemy_subject_sees_their_own_side_as_allies(self):
        self.log("loss", enemies=["RIVAL", "RIVALFRIEND"])
        allies = analysis.by_ally(self.conn, self._subject("RIVAL"), analysis.Filters())
        self.assertEqual([a["name"] for a in allies], ["RIVALFRIEND"])
        opponents = analysis.by_opponent(self.conn, self._subject("RIVAL"),
                                         analysis.Filters())
        self.assertIn("NICCS", [o["name"] for o in opponents])

    def test_recent_matches_show_their_result_not_mine(self):
        self.log("win", enemies=["RIVAL"])
        recent = players_module.get_player(self.player_id("RIVAL"))["recent"][0]
        self.assertEqual(recent["result"], "loss")
        self.assertEqual(recent["my_result"], "win")


class TestSubjectFilters(AnalysisTestCase):

    def test_team_size_filter(self):
        self.log("win", team_size=5)
        self.log("win", team_size=6)
        self.log("loss", team_size=6)
        six = analysis.overall(self.conn, analysis.Subject(),
                               analysis.Filters(team_size=6))
        self.assertEqual(six["games"], 2)
        five = analysis.overall(self.conn, analysis.Subject(),
                                analysis.Filters(team_size=5))
        self.assertEqual(five["games"], 1)

    def test_by_team_size_breakdown(self):
        self.log("win", team_size=6)
        self.log("loss", team_size=5)
        rows = {r["name"]: r for r in
                analysis.by_team_size(self.conn, analysis.Subject(), analysis.Filters())}
        self.assertEqual(rows["6v6"]["wins"], 1)
        self.assertEqual(rows["5v5"]["losses"], 1)

    def test_mode_override_beats_the_maps_default(self):
        """A match can record a mode the map does not default to."""
        self.log("win", map_name="Ilios", mode="clash")
        rows = {r["mode"]: r for r in
                analysis.by_mode(self.conn, analysis.Subject(), analysis.Filters())}
        self.assertIn("clash", rows)

    def test_totals_report_the_coverage_gap(self):
        self.log("win", map_name="Ilios")
        self.log("win")                                  # no map
        counters = analysis.totals(self.conn, analysis.Subject(), analysis.Filters())
        by_map = analysis.by_map(self.conn, analysis.Subject(), analysis.Filters())
        self.assertEqual(counters["matches"], 2)
        self.assertEqual(counters["matches_with_map"], 1)
        self.assertEqual(sum(r["games"] for r in by_map), 1)


class TestBans(AnalysisTestCase):

    def test_bans_are_counted_per_match_not_per_row(self):
        match_id = self.log("win", bans=["Sombra", "Widowmaker"])
        # A duplicated ban row must not double-count the hero.
        self.conn.execute(
            "INSERT INTO match_bans (match_id, hero_id, slot_index) VALUES (?, ?, 9)",
            (match_id, self.hero_id("Sombra")))
        self.conn.commit()
        heroes = {h["name"]: h for h in
                  analysis.bans(self.conn, analysis.Subject(), analysis.Filters())["heroes"]}
        self.assertEqual(heroes["Sombra"]["games"], 1)

    def test_ban_rate_denominator_excludes_matches_with_no_ban_data(self):
        """Otherwise every rate reads low because of missing data, not bans."""
        self.log("win", bans=["Sombra"])
        self.log("loss")                       # nobody typed the bans in
        data = analysis.bans(self.conn, analysis.Subject(), analysis.Filters())
        self.assertEqual(data["matches_with_bans"], 1)
        self.assertEqual(data["heroes"][0]["ban_rate"], 1.0)

    def test_ban_record_follows_the_subject(self):
        self.log("win", bans=["Sombra"], enemies=["RIVAL"])
        rival = analysis.Subject(player_id=self.player_id("RIVAL"))
        heroes = analysis.bans(self.conn, rival, analysis.Filters())["heroes"]
        self.assertEqual(heroes[0]["losses"], 1, "I won that one, so they lost it")


class TestComps(AnalysisTestCase):

    def test_role_shape_is_grouped_and_counted(self):
        for _ in range(2):
            self.log("win", team_size=5,
                     ally_roles=["tank", "damage", "damage", "support", "support"])
        shapes = analysis.comp_shapes(self.conn, analysis.Subject(),
                                      analysis.Filters())["shapes"]
        self.assertEqual([s["name"] for s in shapes], ["1-2-2"])
        self.assertEqual(shapes[0]["games"], 2)

    def test_open_queue_shape_is_valid(self):
        """3 tank / 1 damage / 2 support is a real composition, not an error."""
        self.log("win", team_size=6,
                 ally_roles=["tank", "tank", "tank", "damage", "support", "support"])
        shapes = analysis.comp_shapes(self.conn, analysis.Subject(),
                                      analysis.Filters())["shapes"]
        self.assertEqual([s["name"] for s in shapes], ["3-1-2"])

    def test_stored_role_beats_the_heros_role(self):
        """`match_players.role` exists precisely so a hero played off-role, or a
        legible role icon over an unknown hero, still classifies correctly."""
        self.log("win", team_size=5,
                 ally_heroes=["Ana"] * 5,
                 ally_roles=["tank", "damage", "damage", "support", "support"])
        shapes = analysis.comp_shapes(self.conn, analysis.Subject(),
                                      analysis.Filters())["shapes"]
        self.assertEqual([s["name"] for s in shapes], ["1-2-2"])

    def test_an_incomplete_roster_is_unclassified_not_mislabelled(self):
        self.log("win", team_size=5, ally_roles=["tank", "damage", "damage", "support"])
        data = analysis.comp_shapes(self.conn, analysis.Subject(), analysis.Filters())
        self.assertEqual(data["shapes"], [])
        self.assertEqual(data["unclassified_games"], 1,
                         "a roster missing a role must be counted, not shaped")

    def test_hero_pairs_need_two_games(self):
        self.log("win", team_size=5, ally_heroes=["Ana", "Reinhardt"])
        pairs = analysis.hero_pairs(self.conn, analysis.Subject(), analysis.Filters())
        self.assertEqual(pairs, [])
        self.log("win", team_size=5, ally_heroes=["Ana", "Reinhardt"])
        pairs = analysis.hero_pairs(self.conn, analysis.Subject(), analysis.Filters())
        self.assertEqual([p["name"] for p in pairs], ["Ana + Reinhardt"])

    def test_exact_comp_needs_every_hero(self):
        self.log("win", team_size=5, ally_heroes=["Ana", "Reinhardt", "Tracer"])
        self.assertEqual(
            analysis.exact_comps(self.conn, analysis.Subject(), analysis.Filters()), [])


class TestStreaks(AnalysisTestCase):

    def test_current_streak_is_the_tail(self):
        for result in ("loss", "win", "win", "win"):
            self.log(result)
        data = analysis.streaks(self.conn, analysis.Subject(), analysis.Filters())
        self.assertEqual(data["current"], {"outcome": "win", "length": 3})
        self.assertEqual(data["longest_win"]["length"], 3)
        self.assertEqual(data["longest_loss"]["length"], 1)

    def test_a_draw_breaks_a_streak(self):
        for result in ("win", "win", "draw", "win"):
            self.log(result)
        data = analysis.streaks(self.conn, analysis.Subject(), analysis.Filters())
        self.assertEqual(data["current"], {"outcome": "win", "length": 1})
        self.assertEqual(data["longest_win"]["length"], 2)

    def test_ordering_is_by_played_at_not_insertion(self):
        self.log("loss", played_at="2026-03-01T00:00:00+00:00")
        self.log("win",  played_at="2026-01-01T00:00:00+00:00")
        self.log("win",  played_at="2026-02-01T00:00:00+00:00")
        data = analysis.streaks(self.conn, analysis.Subject(), analysis.Filters())
        self.assertEqual(data["current"], {"outcome": "loss", "length": 1})
        self.assertEqual(data["longest_win"]["length"], 2)

    def test_no_matches_is_not_a_streak(self):
        data = analysis.streaks(self.conn, analysis.Subject(), analysis.Filters())
        self.assertEqual(data["current"]["length"], 0)


class TestWilson(AnalysisTestCase):
    """The ranking rule, pinned. These literals are the requirement."""

    def test_a_small_perfect_record_loses_to_a_large_good_one(self):
        two_of_two = analysis.wilson_lower(2, 0)
        twenty_seven = analysis.wilson_lower(27, 13)
        self.assertAlmostEqual(two_of_two, 0.342, places=3)
        self.assertAlmostEqual(twenty_seven, 0.520, places=3)
        self.assertLess(two_of_two, twenty_seven,
                        "68% over 40 games must outrank 100% over 2")

    def test_no_games_scores_zero(self):
        self.assertEqual(analysis.wilson_lower(0, 0), 0.0)
        self.assertEqual(analysis.wilson_upper(0, 0), 1.0)

    def test_more_evidence_tightens_the_interval(self):
        narrow = analysis.wilson_upper(5, 5) - analysis.wilson_lower(5, 5)
        wide = analysis.wilson_upper(50, 50) - analysis.wilson_lower(50, 50)
        self.assertLess(wide, narrow)

    def test_highlights_gate_on_min_games(self):
        for _ in range(3):
            self.log("win", map_name="Ilios")
        best = analysis.highlights(self.conn, analysis.Subject(),
                                   analysis.Filters())["best"]
        self.assertEqual(best, [], "3 games is below the 5-game gate")
        for _ in range(2):
            self.log("win", map_name="Ilios")
        best = analysis.highlights(self.conn, analysis.Subject(),
                                   analysis.Filters())["best"]
        self.assertIn("Ilios", [b["name"] for b in best])

    def test_highlights_are_sorted_by_score_descending(self):
        for _ in range(6):
            self.log("win", map_name="Ilios")
        for _ in range(3):
            self.log("win", map_name="Nepal")
        for _ in range(3):
            self.log("loss", map_name="Nepal")
        best = analysis.highlights(self.conn, analysis.Subject(),
                                   analysis.Filters())["best"]
        scores = [b["score"] for b in best]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_draws_are_excluded_from_the_score_denominator(self):
        """`win_rate` counts draws, Wilson does not — so `decisive` rides along
        to make the difference visible instead of mysterious."""
        for _ in range(4):
            self.log("win", map_name="Ilios")
        self.log("draw", map_name="Ilios")
        entry = next(b for b in analysis.highlights(
            self.conn, analysis.Subject(), analysis.Filters())["best"]
            if b["name"] == "Ilios")
        self.assertEqual(entry["games"], 5)
        self.assertEqual(entry["decisive"], 4)
        self.assertAlmostEqual(entry["win_rate"], 0.8)
        self.assertAlmostEqual(entry["score"], analysis.wilson_lower(4, 0), places=9)

    def test_delta_is_measured_against_the_subjects_own_rate(self):
        for _ in range(5):
            self.log("win", map_name="Ilios")
        for _ in range(5):
            self.log("loss", map_name="Nepal")
        data = analysis.highlights(self.conn, analysis.Subject(), analysis.Filters())
        self.assertAlmostEqual(data["baseline"], 0.5)
        for entry in data["best"]:
            self.assertAlmostEqual(entry["delta"], entry["score"] - 0.5, places=9)

    def test_most_banned_is_a_frequency_list_not_a_rate_list(self):
        for _ in range(5):
            self.log("loss", bans=["Sombra"])
        self.log("win", bans=["Widowmaker"])
        banned = analysis.highlights(self.conn, analysis.Subject(),
                                     analysis.Filters())["most_banned"]
        self.assertEqual(banned[0]["name"], "Sombra",
                         "ranked by how often, not by how well it went")


class TestNotesAndTags(AnalysisTestCase):
    """Per-player notes and the endorsement dots."""

    def setUp(self):
        super().setUp()
        self.log("win", teammates=["FRIEND"])
        self.friend = self.player_id("FRIEND")

    def test_a_new_player_has_no_note_and_no_tags(self):
        data = players_module.get_player(self.friend)
        self.assertIsNone(data["player"]["notes"])
        self.assertEqual(data["tags"], [])

    def test_notes_round_trip(self):
        players_module.update_player(self.friend, {"notes": "good comms in VC"})
        self.assertEqual(players_module.get_player(self.friend)["player"]["notes"],
                         "good comms in VC")

    def test_blank_and_whitespace_notes_become_null(self):
        """One representation of 'no note', so `notes IS NOT NULL` is the only
        check anything needs."""
        players_module.update_player(self.friend, {"notes": "temp"})
        for blank in ("", "   ", "\n\t "):
            with self.subTest(blank=repr(blank)):
                players_module.update_player(self.friend, {"notes": blank})
                self.assertIsNone(players_module.get_player(self.friend)["player"]["notes"])

    def test_writing_a_note_stamps_when(self):
        players_module.update_player(self.friend, {"notes": "flex player"})
        self.assertIsNotNone(
            players_module.get_player(self.friend)["player"]["notes_updated_at"])

    def test_notes_survive_another_match(self):
        """The whole point: a note is about the person, not the game."""
        players_module.update_player(self.friend, {"notes": "plays Rein"})
        self.log("loss", teammates=["FRIEND"])
        self.assertEqual(players_module.get_player(self.friend)["player"]["notes"],
                         "plays Rein")

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            players_module.update_player(self.friend, {"display_name": "HACKED"})
        self.assertEqual(caught.exception.status_code, 400)

    def test_notes_on_a_missing_player_is_a_404(self):
        with self.assertRaises(HTTPException) as caught:
            players_module.update_player(999999, {"notes": "x"})
        self.assertEqual(caught.exception.status_code, 404)

    def test_tags_replace_wholesale(self):
        players_module.set_tags(self.friend, {"tags": ["tank", "red"]})
        self.assertEqual(players_module.get_player(self.friend)["tags"], ["tank", "red"])
        players_module.set_tags(self.friend, {"tags": ["support"]})
        self.assertEqual(players_module.get_player(self.friend)["tags"], ["support"])
        players_module.set_tags(self.friend, {"tags": []})
        self.assertEqual(players_module.get_player(self.friend)["tags"], [])

    def test_tags_come_back_in_vocabulary_order(self):
        players_module.set_tags(self.friend, {"tags": ["white", "tank", "pink"]})
        self.assertEqual(players_module.get_player(self.friend)["tags"],
                         ["tank", "pink", "white"])

    def test_setting_the_same_tags_twice_is_idempotent(self):
        players_module.set_tags(self.friend, {"tags": ["damage"]})
        players_module.set_tags(self.friend, {"tags": ["damage"]})
        self.assertEqual(players_module.get_player(self.friend)["tags"], ["damage"])

    def test_unknown_tag_is_rejected_by_name(self):
        with self.assertRaises(HTTPException) as caught:
            players_module.set_tags(self.friend, {"tags": ["chartreuse"]})
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("chartreuse", str(caught.exception.detail))

    def test_a_rejected_tag_set_changes_nothing(self):
        players_module.set_tags(self.friend, {"tags": ["tank"]})
        with self.assertRaises(HTTPException):
            players_module.set_tags(self.friend, {"tags": ["support", "nonsense"]})
        self.assertEqual(players_module.get_player(self.friend)["tags"], ["tank"])

    def test_role_tag_codes_match_the_role_column(self):
        """The reason tags are a table: `player_tags.tag_code` has to join
        against `match_players.role`. If these vocabularies ever drift, that
        join silently returns nothing."""
        codes = {r["code"] for r in self.conn.execute(
            "SELECT code FROM tags WHERE tag_set = 'role'")}
        self.assertEqual(codes, {"tank", "damage", "support"})

    def test_the_two_sets_have_three_dots_each(self):
        counts = dict(self.conn.execute(
            "SELECT tag_set, COUNT(*) FROM tags GROUP BY tag_set").fetchall())
        self.assertEqual(counts, {"role": 3, "free": 3})

    def test_deleting_a_player_takes_their_tags(self):
        """`player_tags` cascades. Tested on a player with no matches, because
        `match_players.player_id` deliberately pins anyone who has played —
        deleting them out from under a scoreboard row is not allowed."""
        cursor = self.conn.execute(
            "INSERT INTO players (display_name, first_seen, last_seen, games_seen) "
            "VALUES ('STRANGER', '2026-01-01', '2026-01-01', 0)")
        stranger = int(cursor.lastrowid)
        self.conn.commit()      # the route opens its own connection
        players_module.set_tags(stranger, {"tags": ["pink"]})
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("DELETE FROM players WHERE id = ?", (stranger,))
        self.conn.commit()
        left = self.conn.execute(
            "SELECT COUNT(*) n FROM player_tags WHERE player_id = ?", (stranger,)
        ).fetchone()["n"]
        self.assertEqual(left, 0)

    def test_player_list_carries_tags_and_note_flag(self):
        players_module.set_tags(self.friend, {"tags": ["tank"]})
        players_module.update_player(self.friend, {"notes": "shotcaller"})
        listed = {p["display_name"]: p for p in players_module.list_players()}
        self.assertEqual(listed["FRIEND"]["tags"], ["tank"])
        self.assertTrue(listed["FRIEND"]["has_notes"])
        self.assertFalse(listed["FRIEND"]["is_me"])
        self.assertTrue(listed["NICCS"]["is_me"])
        # The list must not haul the note text around for a column that only
        # renders a marker.
        self.assertNotIn("notes", listed["FRIEND"])


class TestSchemaVersionTwo(AnalysisTestCase):

    def test_schema_version_matches_the_last_migration(self):
        """Derived from the directory, not hardcoded: a literal here fails on
        every new migration, which teaches you to bump the number rather than
        to ask whether the migration ran. What matters is that startup applied
        everything on disk."""
        from app import paths

        latest = max(int(p.name.split("_", 1)[0]) for p in paths.MIGRATIONS_DIR.glob("*.sql"))
        version = self.conn.execute(
            "SELECT value FROM settings WHERE key = 'schema_version'").fetchone()["value"]
        self.assertEqual(int(version), latest)

    def test_default_team_size_is_six(self):
        """6v6 is what this tracker is for."""
        from app import settings as settings_module
        self.assertEqual(settings_module.get(self.conn, "default_team_size"), 6)

    def test_five_v_five_still_works(self):
        """Not deprecated — just not the default."""
        match_id = self.log("win", team_size=5, teammates=["FRIEND"])
        size = self.conn.execute(
            "SELECT team_size FROM matches WHERE id = ?", (match_id,)).fetchone()["team_size"]
        self.assertEqual(size, 5)


if __name__ == "__main__":
    unittest.main()
