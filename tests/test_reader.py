"""Digit reading: cell geometry, glyph segmentation, the atlas, and the reader.

The accuracy assertions here are the milestone-4 hard stop in test form —
"every stat cell must read correctly or report low confidence, never a
confident wrong number". `wrong` is therefore asserted at exactly zero, and is
a stricter requirement than the read rate.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import paths                                          # noqa: E402
from app.extract import cells, glyphs as glyph_module          # noqa: E402
from app.extract.local import LocalExtractor                   # noqa: E402
from app.extract.localize import COLUMN_NAMES, localize        # noqa: E402
from app.extract.reader import read_cell, read_row             # noqa: E402

MANIFEST_PATH = paths.SAMPLES_DIR / "manifest.json"
MANIFEST = (json.loads(MANIFEST_PATH.read_text("utf-8"))["images"]
            if MANIFEST_PATH.exists() else [])
ATLAS_SOURCES = {e["file"] for e in MANIFEST if e.get("atlas_source")}

GROUND_TRUTH = sorted(paths.SAMPLES_DIR.glob("*.expected.json"))
HELD_OUT = [p for p in GROUND_TRUTH
            if json.loads(p.read_text("utf-8"))["source"] not in ATLAS_SOURCES]

ATLAS = glyph_module.load_atlas()


def load(path: Path) -> np.ndarray:
    image = LocalExtractor._load(str(path))
    assert image is not None, f"could not decode {path}"
    return image


def truth_cells(expected_path: Path):
    """Yield (image, layout, block, row, column, expected value)."""
    expected = json.loads(expected_path.read_text("utf-8"))
    image = load(paths.SAMPLES_DIR / expected["source"])
    layout = localize(image).layout
    for block in layout.teams:
        rows = expected["teams"].get(block.team) or []
        for row in range(min(block.row_count, len(rows))):
            for column in COLUMN_NAMES:
                value = rows[row].get(column)
                if value is not None:
                    yield image, layout, block, row, column, value


@unittest.skipIf(not ATLAS, "seed/glyphs is not built")
class AtlasTest(unittest.TestCase):
    def test_atlas_has_every_digit(self):
        self.assertTrue(ATLAS.ready, f"only has {ATLAS.characters}")

    def test_atlas_records_what_it_was_built_from(self):
        """The atlas must name its sources. Whether those files are *present*
        is only checkable where the corpus is — it is withheld from the
        published repository because the screenshots are of real players."""
        self.assertTrue(ATLAS.built_from)
        if not any(paths.SAMPLES_DIR.glob("*.png")):
            self.skipTest("no corpus to check the recorded sources against")
        for source in ATLAS.built_from:
            self.assertTrue((paths.SAMPLES_DIR / source).is_file())

    def test_atlas_is_not_built_from_the_held_out_set(self):
        """The accuracy numbers are only worth reading if this holds."""
        for path in HELD_OUT:
            source = json.loads(path.read_text("utf-8"))["source"]
            self.assertNotIn(source, ATLAS.built_from)

    def test_templates_are_distinguishable_from_each_other(self):
        """Two digits that correlate near-perfectly would make every match a
        coin flip no matter how good the segmentation is."""
        worst, pair = 1.0, None
        for a in glyph_module.DIGITS:
            for b in glyph_module.DIGITS:
                if a >= b:
                    continue
                first = ATLAS.templates[a]
                second = cv2.resize(ATLAS.templates[b], (first.shape[1], first.shape[0]),
                                    interpolation=cv2.INTER_AREA)
                score = glyph_module._correlate(glyph_module._zero_mean(first),
                                                glyph_module._zero_mean(second))
                if score < worst:
                    worst, pair = score, (a, b)
                self.assertLess(score, glyph_module.MIN_SCORE,
                                f"templates {a!r} and {b!r} correlate at {score:.3f}")
        self.assertIsNotNone(pair)

    def test_match_rejects_noise_rather_than_naming_it(self):
        rng = np.random.default_rng(11)
        noise = rng.integers(0, 255, (28, 20), dtype=np.uint8)
        result = glyph_module.match(noise, ATLAS, only=glyph_module.DIGITS)
        self.assertFalse(result.confident)

    def test_match_on_an_empty_atlas_is_not_a_crash(self):
        empty = glyph_module.Atlas(templates={})
        self.assertFalse(glyph_module.match(np.zeros((28, 20), np.uint8), empty).confident)


@unittest.skipIf(not GROUND_TRUTH, "no ground truth in samples/")
class SegmentationTest(unittest.TestCase):
    def test_every_cell_segments_into_the_right_number_of_glyphs(self):
        for path in GROUND_TRUTH:
            for image, layout, block, row, column, value in truth_cells(path):
                with self.subTest(sample=path.stem, team=block.team, row=row, column=column):
                    found = cells.cell_glyphs(image, layout, block, row, column)
                    self.assertEqual(len(found), len(f"{value:,}"),
                                     f"expected {value:,}")

    def test_digits_and_separators_are_separated_by_size(self):
        """The reader classifies rather than matches the comma, so the height
        gap it relies on has to be real and wide."""
        digit_heights, separator_heights = [], []
        for path in GROUND_TRUTH:
            for image, layout, block, row, column, value in truth_cells(path):
                text = f"{value:,}"
                for character, glyph in zip(text, cells.cell_glyphs(
                        image, layout, block, row, column)):
                    (separator_heights if character == "," else digit_heights).append(
                        glyph.height)
        self.assertTrue(digit_heights and separator_heights)
        self.assertGreater(min(digit_heights), max(separator_heights) + 8,
                           "digit and separator heights are converging")
        self.assertGreater(min(digit_heights), cells.DIGIT_MIN_HEIGHT)
        self.assertLess(max(separator_heights), cells.DIGIT_MIN_HEIGHT)

    def test_a_normal_width_glyph_is_never_split(self):
        glyph = cells.Glyph(image=np.ones((28, 22), np.uint8) * 255,
                            x0=0.0, y0=0.0, width=22, height=28)
        self.assertEqual(len(cells._split_if_merged(glyph)), 1)

    def test_a_double_width_glyph_is_split_at_its_waist(self):
        block = np.zeros((28, 44), np.uint8)
        block[:, 2:20] = 255
        block[:, 24:42] = 255
        block[13:15, 20:24] = 255            # a thin bridge joining the two
        glyph = cells.Glyph(image=block, x0=0.0, y0=0.0, width=44, height=28)
        pieces = cells._split_if_merged(glyph)
        self.assertEqual(len(pieces), 2)
        self.assertLess(abs(pieces[0].width - pieces[1].width), 8)


@unittest.skipIf(not (ATLAS and GROUND_TRUTH), "atlas or ground truth missing")
class ReadAccuracyTest(unittest.TestCase):
    def _score(self, paths_to_score):
        correct = unread = wrong = 0
        errors = []
        for path in paths_to_score:
            for image, layout, block, row, column, want in truth_cells(path):
                reading = read_cell(image, layout, block, row, column, ATLAS)
                if reading.value == want:
                    correct += 1
                elif reading.value is None:
                    unread += 1
                    errors.append(f"unread {path.stem} {block.team} r{row} {column}: "
                                  f"want {want:,} ({reading.problem})")
                else:
                    wrong += 1
                    errors.append(f"WRONG {path.stem} {block.team} r{row} {column}: "
                                  f"want {want:,} got {reading.value:,}")
        return correct, unread, wrong, errors

    def test_never_reads_a_wrong_number(self):
        """The milestone-4 hard stop. Refusing is fine; lying is not."""
        _, _, wrong, errors = self._score(GROUND_TRUTH)
        self.assertEqual(wrong, 0, "\n".join(e for e in errors if e.startswith("WRONG")))

    def test_reads_the_held_out_sample(self):
        if not HELD_OUT:
            self.skipTest("every ground-truth sample feeds the atlas")
        correct, unread, wrong, errors = self._score(HELD_OUT)
        total = correct + unread + wrong
        self.assertEqual(wrong, 0)
        self.assertGreaterEqual(correct / total, 0.95,
                                f"{correct}/{total} on held-out data\n" + "\n".join(errors))

    def test_reads_the_whole_corpus(self):
        correct, unread, wrong, errors = self._score(GROUND_TRUTH)
        total = correct + unread + wrong
        self.assertEqual(wrong, 0)
        self.assertGreaterEqual(correct / total, 0.95,
                                f"{correct}/{total}\n" + "\n".join(errors))

    def test_reading_survives_a_change_of_resolution(self):
        """The atlas is in canonical units and cells are cropped through the
        canonical transform, so a resampled screenshot must read the same. This
        is the payoff for normalizing in stage 1 rather than downstream."""
        path = GROUND_TRUTH[0]
        expected = json.loads(path.read_text("utf-8"))
        base = load(paths.SAMPLES_DIR / expected["source"])
        for factor in (0.8, 1.4):
            with self.subTest(factor=factor):
                scaled = cv2.resize(
                    base, None, fx=factor, fy=factor,
                    interpolation=cv2.INTER_AREA if factor < 1 else cv2.INTER_CUBIC)
                layout = localize(scaled).layout
                correct = total = 0
                for block in layout.teams:
                    rows = expected["teams"][block.team]
                    for row in range(block.row_count):
                        reading = read_row(scaled, layout, block, row, ATLAS)
                        for column in COLUMN_NAMES:
                            total += 1
                            want = rows[row][column]
                            got = reading[column].value
                            self.assertIn(got, (want, None),
                                          f"{block.team} r{row} {column} at {factor}x: "
                                          f"read {got}, neither {want} nor refused")
                            if got == want:
                                correct += 1
                self.assertGreaterEqual(correct / total, 0.90,
                                        f"{correct}/{total} at {factor}x")


@unittest.skipIf(not ATLAS, "seed/glyphs is not built")
@unittest.skipIf(not GROUND_TRUTH, "no hand-written ground truth in samples/")
class ReaderGuardTest(unittest.TestCase):
    """The reader's refusal paths, which are what keep `wrong` at zero.

    Needs a real screenshot to build a layout from, so it skips wherever the
    corpus is withheld — see the note in samples/README.md.
    """

    def _layout_and_image(self):
        path = paths.SAMPLES_DIR / json.loads(
            GROUND_TRUTH[0].read_text("utf-8"))["source"]
        image = load(path)
        return image, localize(image).layout

    def test_an_empty_cell_is_refused_not_read_as_zero(self):
        """A blank cell and a cell holding 0 are different facts, and the game
        does render a real 0 — dimmed, but there."""
        image, layout = self._layout_and_image()
        blank = np.zeros_like(image)
        reading = read_cell(blank, layout, layout.teams[0], 0, "damage", ATLAS)
        self.assertIsNone(reading.value)
        self.assertTrue(reading.problem)

    def test_a_separator_in_the_wrong_place_is_refused(self):
        from app.extract.reader import _separator_problem

        def glyph(x, height):
            return cells.Glyph(image=np.zeros((height, 4), np.uint8),
                               x0=float(x), y0=0.0, width=4, height=height)

        digits = [glyph(x, 28) for x in (0, 20, 40, 60)]
        # '1,234': the comma sits after the first digit, three to its right.
        self.assertIsNone(_separator_problem(digits, [glyph(10, 10)]))
        # One digit to its right is not a thousands boundary.
        self.assertIsNotNone(_separator_problem(digits, [glyph(45, 10)]))
        # Nor are two.
        self.assertIsNotNone(_separator_problem(digits, [glyph(25, 10)]))
        # A leading comma has no digits to its left at all.
        self.assertIsNotNone(_separator_problem(digits, [glyph(-5, 10)]))

    def test_a_dropped_digit_cannot_pass_the_separator_check(self):
        """If a digit were mis-sized into punctuation and discarded, the commas
        left behind would no longer sit on thousands boundaries."""
        from app.extract.reader import _separator_problem

        def glyph(x, height):
            return cells.Glyph(image=np.zeros((height, 4), np.uint8),
                               x0=float(x), y0=0.0, width=4, height=height)

        # '16,095' with the '1' lost: three digits left of a comma that should
        # have four, and only two remaining to its right.
        digits = [glyph(x, 28) for x in (20, 40, 70, 90)]
        self.assertIsNotNone(_separator_problem(digits, [glyph(55, 10), glyph(100, 10)]))


if __name__ == "__main__":
    unittest.main()
