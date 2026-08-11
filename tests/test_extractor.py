"""LocalExtractor end to end: screenshot in, draft fragment out.

Covers the seam between reading and the draft — the extractor's job is not only
to be right but to be right *in the shape the merge rules expect*, which is
where an accurate reader can still corrupt a match.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import draft as draft_module                          # noqa: E402
from app import paths                                          # noqa: E402
from app.extract.glyphs import load_atlas                      # noqa: E402
from app.extract.local import LocalExtractor                   # noqa: E402

GROUND_TRUTH = sorted(paths.SAMPLES_DIR.glob("*.expected.json"))
TAB_SHOT = paths.SAMPLES_DIR / "ingame_tab_2511x1006.png"
ATLAS_READY = load_atlas().ready


@unittest.skipIf(not (GROUND_TRUTH and ATLAS_READY), "samples or atlas missing")
class ExtractEndgameTest(unittest.TestCase):
    def setUp(self):
        self.expected = json.loads(GROUND_TRUTH[0].read_text("utf-8"))
        self.path = paths.SAMPLES_DIR / self.expected["source"]
        # Deliberately 5, while every sample is 6v6.
        self.extractor = LocalExtractor(team_size=5)
        self.result = self.extractor.extract(str(self.path), "endgame_report")

    def test_extraction_reports_success(self):
        self.assertTrue(self.result.ok, self.result.warnings)

    def test_row_count_comes_from_the_image_not_the_setting(self):
        """Invariant 6. The extractor was built with team_size=5."""
        rows = self.result.draft["rows"]
        self.assertEqual(len(rows), 12)
        self.assertEqual(self.result.draft["meta"]["team_size"]["value"], 6)
        for team in ("ally", "enemy"):
            self.assertEqual(len([r for r in rows if r["team"] == team]), 6)

    def test_a_team_size_disagreement_is_surfaced(self):
        self.assertTrue(any("6v6" in w for w in self.result.warnings),
                        self.result.warnings)

    def test_blue_block_becomes_the_ally_team(self):
        allies = [r for r in self.result.draft["rows"] if r["team"] == "ally"]
        truth = self.expected["teams"]["blue"]
        for row, want in zip(sorted(allies, key=lambda r: r["row_index"]), truth):
            self.assertEqual(row["eliminations"]["value"], want["eliminations"])

    def test_every_statistic_matches_the_ground_truth(self):
        for team_key, draft_team in (("blue", "ally"), ("red", "enemy")):
            truth = self.expected["teams"][team_key]
            rows = sorted((r for r in self.result.draft["rows"] if r["team"] == draft_team),
                          key=lambda r: r["row_index"])
            for index, (row, want) in enumerate(zip(rows, truth)):
                for column in draft_module.STAT_FIELDS:
                    with self.subTest(team=draft_team, row=index, column=column):
                        self.assertEqual(row[column]["value"], want[column])

    def test_envelopes_are_stamped_as_extracted(self):
        row = self.result.draft["rows"][0]
        envelope = row["damage"]
        self.assertEqual(envelope["source"], "template")
        self.assertEqual(envelope["origin"], "endgame_report")
        self.assertGreater(envelope["confidence"], 0.8)

    def test_fields_the_screenshot_cannot_show_stay_empty(self):
        """Map and result are the operator's assertions; an endgame report does
        not contain them and the extractor must not invent them from anything,
        least of all the filename."""
        for name in ("map_id", "result", "mode"):
            self.assertIsNone(self.result.draft["meta"][name]["value"])

    def test_extraction_merges_into_a_blank_draft(self):
        base = draft_module.empty_draft(5)
        merged = draft_module.merge_draft(base, self.result.draft)
        self.assertEqual(len(merged["rows"]), 12)
        self.assertEqual(merged["meta"]["team_size"]["value"], 6)
        self.assertEqual(merged["conflicts"], [])

    def test_extraction_never_silently_overwrites_a_typed_value(self):
        """Precedence rule: an operator edit that disagrees raises a conflict
        rather than losing."""
        base = draft_module.empty_draft(5)
        ally = next(r for r in base["rows"] if r["team"] == "ally" and r["row_index"] == 0)
        ally["damage"] = draft_module.field(999, source="manual")
        merged = draft_module.merge_draft(base, self.result.draft)
        self.assertEqual(merged["rows"][0]["damage"]["value"], 999)
        self.assertTrue(any("damage" in c["path"] for c in merged["conflicts"]))


@unittest.skipIf(not GROUND_TRUTH, "no samples")
class ExtractRefusalTest(unittest.TestCase):
    """Every path that must not produce a confident draft."""

    def test_a_tab_shot_is_not_read_as_an_endgame_report(self):
        if not TAB_SHOT.is_file():
            self.skipTest("no in-game sample")
        result = LocalExtractor(6).extract(str(TAB_SHOT), "in_game_scoreboard")
        self.assertFalse(result.ok)
        self.assertTrue(result.warnings)

    def test_a_tab_shot_declared_as_an_endgame_report_still_refuses(self):
        """The operator can mis-declare the kind, and the column signature is
        what catches it — the two boards print the same six labels above the
        same two coloured blocks, so their spacing is the only difference the
        anchors can see. Reading a Tab board as an endgame report would not
        fail; it would return a full set of plausible, wrong numbers."""
        if not TAB_SHOT.is_file():
            self.skipTest("no in-game sample")
        result = LocalExtractor(6).extract(str(TAB_SHOT), "endgame_report")
        self.assertFalse(result.ok)
        self.assertTrue(any("in-game" in w for w in result.warnings), result.warnings)

    def test_a_missing_file_produces_an_empty_draft_not_an_exception(self):
        result = LocalExtractor(5).extract(str(paths.SAMPLES_DIR / "nope.png"),
                                           "endgame_report")
        self.assertFalse(result.ok)
        self.assertTrue(result.warnings)

    def test_garbage_bytes_produce_an_empty_draft_not_an_exception(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            handle.write(b"this is not a png")
            name = handle.name
        try:
            result = LocalExtractor(5).extract(name, "endgame_report")
            self.assertFalse(result.ok)
            self.assertTrue(result.warnings)
        finally:
            Path(name).unlink(missing_ok=True)

    def test_a_noise_image_produces_an_empty_draft_not_an_exception(self):
        import tempfile

        import cv2

        rng = np.random.default_rng(3)
        noise = rng.integers(0, 255, (500, 700, 3), dtype=np.uint8)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            name = handle.name
        try:
            cv2.imwrite(name, noise)
            result = LocalExtractor(5).extract(name, "endgame_report")
            self.assertFalse(result.ok)
            self.assertTrue(result.warnings)
        finally:
            Path(name).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
