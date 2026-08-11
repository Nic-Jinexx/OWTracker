"""Nameplate recognition.

The asymmetry drives every assertion here: failing to recognize someone costs
one typed name, while recognizing them as the *wrong* person silently files a
stranger's statistics under a teammate and nothing downstream would ever show
it. So `false positives == 0` is asserted absolutely, and recall is only
asserted as a floor.
"""

from __future__ import annotations

import itertools
import json
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import paths                                          # noqa: E402
from app.extract import cells, nameplates                      # noqa: E402
from app.extract.local import LocalExtractor                   # noqa: E402
from app.extract.localize import localize                      # noqa: E402

# Hand-identified appearances: (sample, team, row, player). Transcribed from
# the screenshots, which is the only ground truth a nameplate has.
APPEARANCES = [
    ("kingsrowloss.png", "blue", 0, "PLAYER_A"),
    ("kingsrowloss.png", "blue", 1, "PLAYER_B"),
    ("kingsrowloss.png", "red", 0, "PLAYER_C"),
    ("kingsrowloss.png", "red", 1, "PLAYER_D"),
    ("kingsrowloss.png", "red", 2, "PLAYER_E"),
    ("kingsrowloss.png", "red", 3, "PLAYER_F"),
    ("kingsrowloss.png", "red", 5, "PLAYER_G"),
    ("nepalloss.png", "blue", 0, "PLAYER_A"),
    ("nepalloss.png", "blue", 1, "PLAYER_B"),
    ("nepalloss.png", "blue", 5, "PLAYER_G"),
    ("nepalloss.png", "red", 0, "PLAYER_C"),
    ("nepalloss.png", "red", 1, "PLAYER_D"),
    ("nepalloss.png", "red", 2, "PLAYER_E"),
    ("nepalloss.png", "red", 3, "PLAYER_F"),
    ("route66Loss.png", "blue", 1, "PLAYER_B"),
    ("route66Loss.png", "blue", 4, "PLAYER_A"),
    ("route66Loss.png", "red", 2, "PLAYER_F"),
    ("route66Loss.png", "red", 3, "PLAYER_D"),
    ("route66Loss.png", "red", 4, "PLAYER_E"),
    ("blizzard worldLoss.png", "blue", 0, "PLAYER_B"),
    ("blizzard worldLoss.png", "blue", 2, "PLAYER_A"),
    ("blizzard worldLoss.png", "blue", 5, "PLAYER_G"),
    ("blizzard worldLoss.png", "red", 1, "PLAYER_D"),
    ("blizzard worldLoss.png", "red", 3, "PLAYER_E"),
]

MAX_DISTANCE = 35      # the shipped default


def _signatures():
    """(player, signature) for every appearance above."""
    collected = []
    for filename, team, row, who in APPEARANCES:
        path = paths.SAMPLES_DIR / filename
        if not path.is_file():
            continue
        image = LocalExtractor._load(str(path))
        result = localize(image)
        if not result.ok:
            continue
        block = next(b for b in result.layout.teams if b.team == team)
        signature = nameplates.row_signature(image, result.layout, block, row)
        if signature:
            collected.append((who, signature))
    return collected


SIGNATURES = _signatures()


@unittest.skipIf(len(SIGNATURES) < 10, "sample corpus missing")
class SignatureTest(unittest.TestCase):

    def test_every_appearance_produces_a_signature(self):
        self.assertEqual(len(SIGNATURES), len(APPEARANCES))

    def test_signatures_are_fixed_width_hex(self):
        width = nameplates.SIGNATURE_BITS // 4
        for who, signature in SIGNATURES:
            with self.subTest(player=who):
                self.assertEqual(len(signature), width)
                int(signature, 16)

    def test_a_blank_nameplate_has_no_signature(self):
        """Four samples have players with no nameplate at all. Hashing a blank
        would give every blank the same signature and match them to each
        other."""
        blank = np.zeros((80, 340, 3), dtype=np.uint8)
        self.assertIsNone(nameplates.signature(blank))

    def test_signature_survives_a_change_of_resolution(self):
        """The crop is taken through the canonical transform, so the same
        nameplate at a different screenshot size must hash the same."""
        path = paths.SAMPLES_DIR / "kingsrowloss.png"
        base = LocalExtractor._load(str(path))
        reference = None
        for factor in (1.0, 0.85, 1.3):
            image = base if factor == 1.0 else cv2.resize(
                base, None, fx=factor, fy=factor,
                interpolation=cv2.INTER_AREA if factor < 1 else cv2.INTER_CUBIC)
            layout = localize(image).layout
            block = next(b for b in layout.teams if b.team == "blue")
            signature = nameplates.row_signature(image, layout, block, 0)
            if reference is None:
                reference = signature
            else:
                with self.subTest(factor=factor):
                    self.assertLessEqual(nameplates.distance(reference, signature),
                                         MAX_DISTANCE)


@unittest.skipIf(len(SIGNATURES) < 10, "sample corpus missing")
class RecognitionTest(unittest.TestCase):
    """Leave-one-out over the corpus: hide one appearance, try to recognize it
    from the others. This is exactly the real workflow — the library is what
    previous matches taught it."""

    def _leave_one_out(self, max_distance=MAX_DISTANCE):
        correct = wrong = unmatched = 0
        mistakes = []
        players = sorted({who for who, _ in SIGNATURES})
        ids = {who: index + 1 for index, who in enumerate(players)}
        for held_out in range(len(SIGNATURES)):
            who, probe = SIGNATURES[held_out]
            library = [(ids[other], signature)
                       for index, (other, signature) in enumerate(SIGNATURES)
                       if index != held_out]
            hit = nameplates.match(probe, library, max_distance)
            if not hit.recognized:
                unmatched += 1
            elif hit.player_id == ids[who]:
                correct += 1
            else:
                wrong += 1
                mistakes.append(
                    f"{who} recognized as {players[hit.player_id - 1]} "
                    f"at {hit.distance} bits")
        return correct, wrong, unmatched, mistakes

    def test_never_recognizes_the_wrong_player(self):
        """The assertion that matters. A miss is free; a false positive files
        someone else's statistics under a teammate."""
        _, wrong, _, mistakes = self._leave_one_out()
        self.assertEqual(wrong, 0, "\n".join(mistakes))

    def test_recognizes_almost_everyone(self):
        correct, wrong, unmatched, _ = self._leave_one_out()
        total = correct + wrong + unmatched
        self.assertGreaterEqual(correct / total, 0.9,
                                f"{correct}/{total} recognized")

    def test_no_false_positives_at_any_plausible_tolerance(self):
        """The threshold sits in a range, not on a knife edge. If a future
        change narrows that range this fails before it can silently start
        misattributing anyone."""
        for tolerance in (20, 25, 30, 35, 40):
            with self.subTest(tolerance=tolerance):
                _, wrong, _, mistakes = self._leave_one_out(tolerance)
                self.assertEqual(wrong, 0, "\n".join(mistakes))

    def test_an_unknown_player_is_not_recognized_as_someone_else(self):
        """Everyone in a lobby who has never been typed in must come back
        blank, not as the nearest acquaintance."""
        target = "PLAYER_A"
        library = [(1, signature) for who, signature in SIGNATURES if who != target]
        for who, probe in SIGNATURES:
            if who != target:
                continue
            with self.subTest(player=who):
                hit = nameplates.match(probe, library, MAX_DISTANCE)
                self.assertFalse(hit.recognized,
                                 f"a stranger matched at {hit.distance} bits")

    def test_an_empty_library_recognizes_nobody(self):
        hit = nameplates.match(SIGNATURES[0][1], [], MAX_DISTANCE)
        self.assertFalse(hit.recognized)

    def test_confidence_falls_off_with_distance(self):
        self.assertEqual(nameplates.confidence_from_distance(0, 60), 1.0)
        self.assertAlmostEqual(nameplates.confidence_from_distance(60, 60), 0.5)
        self.assertEqual(nameplates.confidence_from_distance(61, 60), 0.0)


@unittest.skipIf(len(SIGNATURES) < 10, "sample corpus missing")
class CropGeometryTest(unittest.TestCase):

    def test_the_nameplate_crop_stays_clear_of_the_stat_columns(self):
        """The crop must not reach the E column, or a long name would hash
        together with somebody's elimination count."""
        self.assertLess(cells.NAMEPLATE_BOUNDS[2], 850.0)

    def test_the_portrait_crop_is_inside_the_board(self):
        x0, _, x1, _ = cells.PORTRAIT_BOUNDS
        self.assertGreater(x0, 0)
        self.assertLess(x1, cells.NAMEPLATE_BOUNDS[0])

    def test_crops_are_anchored_to_the_row_top(self):
        """Rows grow downward when a title line hangs below the name, so a
        crop measured from the top is stable and one measured from the centre
        is not."""
        path = paths.SAMPLES_DIR / "kingsrowloss.png"
        image = LocalExtractor._load(str(path))
        layout = localize(image).layout
        block = next(b for b in layout.teams if b.team == "blue")
        heights = {cells.nameplate_image(image, layout, block, r).shape[:2]
                   for r in range(block.row_count)}
        self.assertEqual(len(heights), 1,
                         "every nameplate crop must be the same size regardless "
                         "of whether its row carries a title line")


if __name__ == "__main__":
    unittest.main()
