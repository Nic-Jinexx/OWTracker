"""Localizer tests, run against the real sample corpus.

Two things are being checked, and they are not the same thing:

1. **Correctness on the corpus** — the transform, the six column centres, and
   the row separators land where a human says they do.
2. **Scale invariance** — the canonical geometry does not move when the same
   screenshot is resampled to a different size. This stands in for the
   milestone-2 "3+ resolutions" gate but does not satisfy it: resampling proves
   the maths is scale-free, whereas a native capture at another resolution
   would also prove the *game* lays the scoreboard out the same way. See
   samples/README.md.
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
from app.extract.local import LocalExtractor                   # noqa: E402
from app.extract.localize import (                             # noqa: E402
    CANONICAL_WIDTH, COLUMN_NAMES, ROW_EDGE_MIN_RANGE, ROW_EDGE_WINDOW,
    ROW_PROBE_BAND, _sliding_range, _to_canonical_profile, localize,
)
from app.extract.overlay import render                         # noqa: E402

MANIFEST_PATH = paths.SAMPLES_DIR / "manifest.json"
MANIFEST = (json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["images"]
            if MANIFEST_PATH.exists() else [])
BY_FILE = {entry["file"]: entry for entry in MANIFEST}

ALL_IMAGES = sorted(paths.SAMPLES_DIR.glob("*.png")) if paths.SAMPLES_DIR.exists() else []
# The endgame report is the only kind the localizer handles today. Asserting
# six-row endgame geometry against an in-game Tab shot would be asserting the
# wrong thing, so the corpus is split by declared kind rather than by glob.
SAMPLES = [p for p in ALL_IMAGES
           if BY_FILE.get(p.name, {}).get("kind") == "endgame_report"
           and BY_FILE.get(p.name, {}).get("localizes", True)]
REFUSED = [p for p in ALL_IMAGES if BY_FILE.get(p.name, {}).get("localizes") is False]

# Measured across the corpus; see tools/localize_report.py for the numbers.
EXPECTED_COLUMNS = {
    "eliminations": 908.0, "assists": 1032.0, "deaths": 1156.0,
    "damage": 1354.0, "healing": 1580.0, "mitigation": 1806.0,
}
COLUMN_TOLERANCE = 8.0          # canonical units, out of 1920
HEADER_HEIGHT = 77.0            # canonical; the strip is a fixed UI element
HEADER_TOLERANCE = 4.0
EXPECTED_ROWS = 6               # every sample is 6v6


def load(path: Path) -> np.ndarray:
    image = LocalExtractor._load(str(path))
    assert image is not None, f"could not decode {path}"
    return image


@unittest.skipIf(not ALL_IMAGES, "no screenshots in samples/")
class ManifestTest(unittest.TestCase):
    """The corpus documents itself or the suite fails."""

    def test_every_image_is_documented(self):
        for path in ALL_IMAGES:
            with self.subTest(sample=path.name):
                self.assertIn(path.name, BY_FILE,
                              "add an entry to samples/manifest.json saying what this proves")

    def test_manifest_does_not_describe_missing_files(self):
        for entry in MANIFEST:
            with self.subTest(sample=entry["file"]):
                self.assertTrue((paths.SAMPLES_DIR / entry["file"]).is_file())

    def test_manifest_sizes_and_kinds_are_accurate(self):
        for entry in MANIFEST:
            with self.subTest(sample=entry["file"]):
                self.assertIn(entry["kind"], ("endgame_report", "in_game_scoreboard"))
                self.assertIn(entry["capture"], ("crop", "fullscreen"))
                image = load(paths.SAMPLES_DIR / entry["file"])
                self.assertEqual(list(image.shape[1::-1]), entry["size"])

    def test_the_corpus_spans_several_capture_geometries(self):
        """The milestone-2 gate. Crops alone cannot prove the game lays the
        scoreboard out proportionally; native full-screen captures at distinct
        resolutions can."""
        fullscreen = {tuple(e["size"]) for e in MANIFEST
                      if e["kind"] == "endgame_report" and e["capture"] == "fullscreen"}
        self.assertGreaterEqual(len(fullscreen), 3,
                                "need 3+ native full-screen endgame captures at distinct sizes")

    def test_board_widths_actually_differ(self):
        """A gate that passed because three 'different' shots happened to share
        a board width would prove nothing."""
        widths = {e["board_width_px"] for e in MANIFEST
                  if e["kind"] == "endgame_report" and "board_width_px" in e}
        self.assertGreaterEqual(len(widths), 4)


@unittest.skipIf(not REFUSED, "no images the localizer is expected to refuse")
class RefusedKindTest(unittest.TestCase):
    """Screenshots the localizer does not handle must fail loudly and usefully.

    Silence would be worse than the refusal: an in-game Tab shot that localized
    'successfully' would feed mid-match statistics into the endgame report's
    slot, which the merge rules treat as authoritative.
    """

    def test_refused_images_fail_with_a_useful_reason(self):
        for path in REFUSED:
            with self.subTest(sample=path.name):
                entry = BY_FILE[path.name]
                result = localize(load(path))
                self.assertFalse(result.ok, "this image was expected to be refused")
                self.assertTrue(result.reason)
                needle = entry.get("refusal_contains")
                if needle:
                    self.assertIn(needle.lower(), result.reason.lower())

    def test_a_refusal_still_carries_enough_to_draw(self):
        """The overlay renders partial geometry on failure, so the diagnostics
        have to survive the give-up path."""
        for path in REFUSED:
            with self.subTest(sample=path.name):
                result = localize(load(path))
                self.assertIn("header", result.diagnostics)
                self.assertIn("blocks", result.diagnostics)
                json.dumps(result.diagnostics)
                self.assertTrue(render(load(path), result).startswith(b"\x89PNG"))


@unittest.skipIf(not SAMPLES, "no screenshots in samples/")
class LocalizeCorpusTest(unittest.TestCase):
    """Every sample must localize, and agree with every other sample."""

    def test_every_sample_localizes(self):
        for path in SAMPLES:
            with self.subTest(sample=path.name):
                result = localize(load(path))
                self.assertTrue(result.ok, f"{path.name}: {result.reason}")

    def test_column_centres_agree_across_samples(self):
        for path in SAMPLES:
            with self.subTest(sample=path.name):
                layout = localize(load(path)).layout
                self.assertEqual(list(layout.columns), list(COLUMN_NAMES))
                for name, expected in EXPECTED_COLUMNS.items():
                    self.assertAlmostEqual(
                        layout.columns[name], expected, delta=COLUMN_TOLERANCE,
                        msg=f"{path.name}: column {name}")

    def test_columns_are_ordered_and_inside_the_board(self):
        for path in SAMPLES:
            with self.subTest(sample=path.name):
                centres = list(localize(load(path)).layout.columns.values())
                self.assertEqual(centres, sorted(centres))
                self.assertGreater(centres[0], 0)
                self.assertLess(centres[-1], CANONICAL_WIDTH)

    def test_header_strip_has_a_constant_canonical_height(self):
        """A cross-check on the transform itself: the strip is a fixed piece of
        UI, so if the scale is right its normalized height cannot drift."""
        for path in SAMPLES:
            with self.subTest(sample=path.name):
                layout = localize(load(path)).layout
                self.assertAlmostEqual(layout.header.height * layout.scale,
                                       HEADER_HEIGHT, delta=HEADER_TOLERANCE)

    def test_both_blocks_found_with_blue_on_top(self):
        for path in SAMPLES:
            with self.subTest(sample=path.name):
                layout = localize(load(path)).layout
                self.assertEqual([b.team for b in layout.teams], ["blue", "red"])
                self.assertTrue(layout.teams[0].is_top)
                self.assertLess(layout.teams[0].box.y1, layout.teams[1].box.y0)

    def test_six_rows_per_block(self):
        """Invariant 6: the row count is read from the image. Every sample is
        6v6 while `default_team_size` is 5, so a localizer that assumed the
        setting would be wrong on all of them."""
        for path in SAMPLES:
            with self.subTest(sample=path.name):
                for block in localize(load(path)).layout.teams:
                    self.assertEqual(block.row_count, EXPECTED_ROWS,
                                     f"{path.name}: {block.team}")

    def test_rows_tile_the_block_without_gaps_or_overlap(self):
        for path in SAMPLES:
            with self.subTest(sample=path.name):
                layout = localize(load(path)).layout
                for block in layout.teams:
                    edges = block.row_edges
                    self.assertEqual(edges[0], 0.0)
                    self.assertAlmostEqual(edges[-1], block.box.height * layout.scale, places=6)
                    self.assertEqual(edges, sorted(edges))

    def test_no_row_is_double_another(self):
        """The signature of a missed separator: two rows swallowed into one."""
        for path in SAMPLES:
            with self.subTest(sample=path.name):
                for block in localize(load(path)).layout.teams:
                    heights = [b - a for a, b in zip(block.row_edges, block.row_edges[1:])]
                    self.assertLess(max(heights), 2 * min(heights),
                                    f"{block.team} rows {[round(h) for h in heights]}")

    def test_title_lines_are_folded_into_their_row(self):
        """A title line under a player's name makes that row taller, and it
        must not become a seventh player.

        Asserted over the corpus rather than per block: a block where nobody
        has a title legitimately has uniform rows, so demanding mixed heights
        everywhere would fail on a correct result. What matters is that where
        titles *do* occur the row simply grows, and the count stays at six.
        """
        spreads = []
        for path in SAMPLES:
            for block in localize(load(path)).layout.teams:
                heights = [b - a for a, b in zip(block.row_edges, block.row_edges[1:])]
                spreads.append(max(heights) - min(heights))
                self.assertEqual(len(heights), EXPECTED_ROWS)
        self.assertGreater(max(spreads), 20,
                           "no block in the corpus shows the taller title-line row — "
                           "either the samples changed or rows are being split on the title")

    def test_vs_divider_sits_between_the_blocks(self):
        for path in SAMPLES:
            with self.subTest(sample=path.name):
                layout = localize(load(path)).layout
                self.assertIsNotNone(layout.vs_divider_y, "VS divider not found")
                y = layout.to_source(0, layout.vs_divider_y)[1]
                self.assertGreater(y, layout.teams[0].box.y1)
                self.assertLess(y, layout.teams[1].box.y0)

    def test_transform_round_trips(self):
        layout = localize(load(SAMPLES[0])).layout
        for point in ((0, 0), (137.5, 902.25), (1919, 3000)):
            x, y = layout.to_canonical(*layout.to_source(*point))
            self.assertAlmostEqual(x, point[0], places=6)
            self.assertAlmostEqual(y, point[1], places=6)


@unittest.skipIf(not SAMPLES, "no screenshots in samples/")
class ScaleInvarianceTest(unittest.TestCase):
    """The canonical geometry must survive resampling and repositioning.

    Not a substitute for a native capture at another resolution — see the
    module docstring — but it does catch any constant that is secretly in
    source pixels rather than canonical units.
    """

    FACTORS = (0.62, 0.8, 1.35, 1.75)

    def _geometry(self, image) -> tuple[list[float], list[int]]:
        result = localize(image)
        self.assertTrue(result.ok, result.reason)
        return ([round(v, 1) for v in result.layout.columns.values()],
                [b.row_count for b in result.layout.teams])

    def test_resampled_copies_localize_identically(self):
        for path in SAMPLES:
            image = load(path)
            columns, rows = self._geometry(image)
            for factor in self.FACTORS:
                with self.subTest(sample=path.name, factor=factor):
                    interpolation = cv2.INTER_AREA if factor < 1 else cv2.INTER_CUBIC
                    scaled = cv2.resize(image, None, fx=factor, fy=factor,
                                        interpolation=interpolation)
                    scaled_columns, scaled_rows = self._geometry(scaled)
                    self.assertEqual(scaled_rows, rows)
                    for got, want, name in zip(scaled_columns, columns, COLUMN_NAMES):
                        self.assertAlmostEqual(got, want, delta=COLUMN_TOLERANCE,
                                               msg=f"{path.name} @{factor}: {name}")

    def test_separator_signal_stays_clear_of_the_row_interior_noise(self):
        """Lock in the margin the row threshold was chosen from.

        `ROW_EDGE_MIN_RANGE` is a bare constant, so the thing worth defending
        is not the constant but the gap it sits in: the weakest separator must
        stay comfortably louder than the loudest flat stretch of row.
        """
        weakest_separator, loudest_interior = 1e9, 0.0

        for path in SAMPLES:
            base = load(path)
            for factor in (1.0,) + self.FACTORS:
                image = base if factor == 1.0 else cv2.resize(
                    base, None, fx=factor, fy=factor,
                    interpolation=cv2.INTER_AREA if factor < 1 else cv2.INTER_CUBIC)
                layout = localize(image).layout
                for block in layout.teams:
                    ranges = self._separator_signal(image, layout, block)
                    separators = [round(e) for e in block.row_edges[1:-1]]
                    near = np.zeros(ranges.size, bool)
                    for edge in separators:
                        near[max(0, edge - 30):edge + 31] = True
                    interior = ~near
                    interior[:100] = False
                    interior[-100:] = False

                    weakest_separator = min(
                        weakest_separator,
                        min(ranges[max(0, e - 30):e + 31].max() for e in separators))
                    if interior.any():
                        loudest_interior = max(loudest_interior, float(ranges[interior].max()))

        self.assertLess(loudest_interior, ROW_EDGE_MIN_RANGE / 3,
                        "row interiors are getting noisy relative to the threshold")
        self.assertGreater(weakest_separator, ROW_EDGE_MIN_RANGE * 1.4,
                           "the weakest separator is closing in on the threshold")

    @staticmethod
    def _separator_signal(image, layout, block):
        x0 = layout.board.x0 + int(ROW_PROBE_BAND[0] / layout.scale)
        x1 = layout.board.x0 + int(ROW_PROBE_BAND[1] / layout.scale)
        band = cv2.cvtColor(image[block.box.y0:block.box.y1 + 1, x0:x1], cv2.COLOR_BGR2GRAY)
        profile = _to_canonical_profile(band.astype(float).mean(axis=1), layout.scale)
        return _sliding_range(profile, int(round(ROW_EDGE_WINDOW)))

    def test_too_small_to_read_fails_loudly(self):
        """Below roughly 0.6x the header glyphs stop being legible. The
        localizer must refuse rather than return a confident wrong layout."""
        for path in SAMPLES:
            with self.subTest(sample=path.name):
                tiny = cv2.resize(load(path), None, fx=0.35, fy=0.35,
                                  interpolation=cv2.INTER_AREA)
                result = localize(tiny)
                if not result.ok:
                    self.assertTrue(result.reason)
                else:
                    # If it does localize, it may not be wrong about the rows.
                    for block in result.layout.teams:
                        self.assertEqual(block.row_count, EXPECTED_ROWS)

    def test_padding_into_a_larger_frame_does_not_move_anything(self):
        """A full-screen capture is a crop plus background. Padding the sample
        with dark background is the closest stand-in available."""
        for path in SAMPLES:
            image = load(path)
            columns, rows = self._geometry(image)
            height, width = image.shape[:2]
            for pad_x, pad_y in ((240, 60), (60, 300)):
                with self.subTest(sample=path.name, pad=(pad_x, pad_y)):
                    frame = np.full((height + 2 * pad_y, width + 2 * pad_x, 3),
                                    (24, 18, 14), dtype=np.uint8)
                    frame[pad_y:pad_y + height, pad_x:pad_x + width] = image
                    padded_columns, padded_rows = self._geometry(frame)
                    self.assertEqual(padded_rows, rows)
                    for got, want in zip(padded_columns, columns):
                        self.assertAlmostEqual(got, want, delta=COLUMN_TOLERANCE)


@unittest.skipIf(not SAMPLES, "no screenshots in samples/")
class ExpectedFileTest(unittest.TestCase):
    """The hand-written ground truth must stay in step with the localizer.

    Only the parts the localizer produces are checked here; the stat values
    wait for the digit reader in milestone 4.
    """

    def test_expected_files_match_the_localizer(self):
        expected_files = list(paths.SAMPLES_DIR.glob("*.expected.json"))
        self.assertTrue(expected_files, "no expected-output file in samples/")

        for path in expected_files:
            with self.subTest(expected=path.name):
                expected = json.loads(path.read_text(encoding="utf-8"))
                image = load(paths.SAMPLES_DIR / expected["source"])
                layout = localize(image).layout

                self.assertEqual(list(image.shape[1::-1]), expected["layout"]["image_size"])
                for block, want in zip(layout.teams, expected["layout"]["blocks"]):
                    self.assertEqual(block.team, want["team"])
                    self.assertEqual(block.is_top, want["is_top"])
                    self.assertEqual(block.row_count, want["rows"])
                for name, want in expected["layout"]["columns_canonical"].items():
                    self.assertAlmostEqual(layout.columns[name], want, delta=COLUMN_TOLERANCE)

    def test_expected_files_assert_only_what_the_image_shows(self):
        """Guards rule 1 in samples/README.md: map and result are the
        operator's assertions and must never be ground truth here, or a future
        extractor could 'pass' by reading the filename."""
        for path in paths.SAMPLES_DIR.glob("*.expected.json"):
            with self.subTest(expected=path.name):
                expected = json.loads(path.read_text(encoding="utf-8"))
                not_asserted = expected.get("not_asserted", {})
                for forbidden in ("map", "result", "mode"):
                    self.assertNotIn(forbidden, expected,
                                     f"{forbidden} is asserted by the operator, not extracted")
                for forbidden in ("map", "result"):
                    self.assertIn(forbidden, not_asserted,
                                  f"{forbidden} should be in not_asserted, with a reason")


class MalformedInputTest(unittest.TestCase):
    """The server never crashes on bad input (Quality Bar)."""

    def test_noise_is_rejected_with_a_reason(self):
        rng = np.random.default_rng(7)
        noise = rng.integers(0, 255, (400, 600, 3), dtype=np.uint8)
        result = localize(noise)
        self.assertFalse(result.ok)
        self.assertTrue(result.reason)

    def test_flat_and_degenerate_images_are_rejected(self):
        for name, image in (
            ("black", np.zeros((300, 300, 3), dtype=np.uint8)),
            ("white", np.full((300, 300, 3), 255, dtype=np.uint8)),
            ("one pixel", np.zeros((1, 1, 3), dtype=np.uint8)),
            ("greyscale", np.zeros((300, 300), dtype=np.uint8)),
            ("empty", np.zeros((0, 0, 3), dtype=np.uint8)),
        ):
            with self.subTest(image=name):
                result = localize(image)
                self.assertFalse(result.ok)
                self.assertTrue(result.reason)

    def test_a_white_page_is_not_mistaken_for_a_header_strip(self):
        """The strip is found by brightness, so the obvious false positive is a
        mostly-white image. It must fail on the missing team blocks."""
        image = np.full((600, 900, 3), 240, dtype=np.uint8)
        result = localize(image)
        self.assertFalse(result.ok)
        self.assertIn("block", result.reason)


class OverlayTest(unittest.TestCase):
    def test_overlay_renders_a_png_for_a_sample(self):
        if not SAMPLES:
            self.skipTest("no screenshots in samples/")
        data = render(load(SAMPLES[0]))
        self.assertTrue(data.startswith(b"\x89PNG"))
        decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(decoded.shape, load(SAMPLES[0]).shape)

    def test_overlay_renders_the_failure_case_too(self):
        """A blank error page would say nothing about why localization gave up."""
        data = render(np.zeros((300, 400, 3), dtype=np.uint8))
        self.assertTrue(data.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
