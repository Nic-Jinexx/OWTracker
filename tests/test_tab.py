"""In-game Tab localizer tests.

Three things are being checked, and only the first is about the Tab board:

1. **The chrome is where a human says it is** — bans, mode/map, clock and rank
   range. Asserted structurally wherever a structural assertion is available
   (five equal squares on a constant pitch; the clock right of the map name)
   rather than as pixel coordinates, because there is exactly one in-game
   sample and coordinates copied out of it would only ever prove that the
   sample has not changed.
2. **The two localizers do not overlap** — every endgame report is refused by
   the Tab localizer and the Tab shot is refused by the endgame one. This is
   the test that matters most. Both boards carry the same six labels, so a
   silent mis-classification reads the wrong pixels with full confidence, and
   the numbers it invents look entirely plausible.
3. **The detectors reject near-misses** — regular-run and contiguous-block
   detection are checked on synthetic input, where a negative case can actually
   be constructed. The corpus has no counter-examples in it by definition.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import paths                                          # noqa: E402
from app.extract.heroes import portrait_signature              # noqa: E402
from app.extract.local import LocalExtractor                   # noqa: E402
from app.extract.localize import _find_team_blocks, localize   # noqa: E402
from app.extract.overlay import render_tab                     # noqa: E402
from app.extract.tab import (                                  # noqa: E402
    _longest_regular_run, localize_tab,
)

MANIFEST_PATH = paths.SAMPLES_DIR / "manifest.json"
MANIFEST = (json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["images"]
            if MANIFEST_PATH.exists() else [])
BY_FILE = {entry["file"]: entry for entry in MANIFEST}

ALL_IMAGES = sorted(paths.SAMPLES_DIR.glob("*.png")) if paths.SAMPLES_DIR.exists() else []
TAB_SAMPLES = [p for p in ALL_IMAGES
               if BY_FILE.get(p.name, {}).get("kind") == "in_game_scoreboard"]
ENDGAME_SAMPLES = [p for p in ALL_IMAGES
                   if BY_FILE.get(p.name, {}).get("kind") == "endgame_report"]


def load(path: Path) -> np.ndarray:
    image = LocalExtractor._load(str(path))
    assert image is not None, f"could not decode {path}"
    return image


@unittest.skipIf(not TAB_SAMPLES, "no in-game scoreboard samples")
class TabLocalizeTest(unittest.TestCase):

    def setUp(self) -> None:
        self.path = TAB_SAMPLES[0]
        self.image = load(self.path)
        self.result = localize_tab(self.image)
        self.assertTrue(self.result.ok, self.result.reason)
        self.layout = self.result.layout

    # -- the board ---------------------------------------------------------

    def test_the_board_is_found_and_the_transform_is_sane(self):
        layout = self.layout
        self.assertGreater(layout.scale, 0)
        self.assertEqual(layout.board.x0, layout.header.x0)
        self.assertEqual(layout.board.x1, layout.header.x1)
        # Canonical space is defined by the board: its left edge is the origin
        # and its width is 1920 by construction.
        self.assertAlmostEqual(layout.to_canonical(layout.board.x0, 0)[0], 0.0, places=6)
        self.assertAlmostEqual(layout.to_canonical(layout.board.x1 + 1, 0)[0], 1920.0, places=3)

    def test_the_full_team_block_reads_six_rows(self):
        """The blue block is entirely in frame; the red one is not. Six is the
        team size, and getting it from a Tab shot is the point of the roster
        half of this module."""
        blue = [b for b in self.layout.teams if b.team == "blue"]
        self.assertEqual(len(blue), 1)
        self.assertEqual(blue[0].row_count, 6)

    def test_a_clipped_block_is_reported_not_silently_short(self):
        """The red block runs off the bottom of this sample. Three rows is the
        truthful answer for what was captured, but a caller that took it for
        the roster would record a 6v3 match."""
        red = [b for b in self.layout.teams if b.team == "red"][0]
        self.assertLess(red.row_count, 6)
        self.assertTrue(
            any("runs off the bottom" in w for w in self.result.warnings),
            f"a clipped block must warn; got {self.result.warnings}")

    # -- chrome ------------------------------------------------------------

    def test_every_chrome_box_lies_above_the_board_and_inside_the_frame(self):
        height, width = self.image.shape[:2]
        chrome = self.layout.chrome
        boxes = list(chrome.bans) + list(chrome.rank_badges)
        boxes += [b for b in (chrome.ban_icon, chrome.mode_icon,
                              chrome.mode_map, chrome.clock) if b is not None]
        self.assertTrue(boxes)
        for box in boxes:
            with self.subTest(box=box):
                self.assertGreaterEqual(box.x0, 0)
                self.assertGreaterEqual(box.y0, 0)
                self.assertLess(box.x1, width)
                self.assertLess(box.y1, height)
                self.assertLess(box.y1, self.layout.board.y0,
                                "chrome sits above the board by definition")

    def test_the_bans_are_equal_squares_on_a_constant_pitch(self):
        """The structural claim the ban detector rests on. Asserting it here
        means a future sample with a different ban count still passes, while a
        detector that latched onto something irregular fails."""
        bans = self.layout.chrome.bans
        self.assertGreaterEqual(len(bans), 2)
        widths = [b.width for b in bans]
        heights = [b.height for b in bans]
        self.assertLess(max(widths) - min(widths), 0.15 * max(widths))
        self.assertLess(max(heights) - min(heights), 0.15 * max(heights))
        for box in bans:
            self.assertLess(abs(box.width - box.height), 0.4 * box.width,
                            "a ban portrait is square-ish")
        pitches = [b.x0 - a.x0 for a, b in zip(bans, bans[1:])]
        self.assertLess(max(pitches) - min(pitches), 0.15 * max(pitches))
        self.assertEqual(bans, sorted(bans, key=lambda b: b.x0), "left to right")

    def test_this_sample_has_five_bans_and_a_prohibition_icon(self):
        """Corpus-specific, and separate from the structural test above so that
        a second sample with a different ban count breaks only this one."""
        chrome = self.layout.chrome
        self.assertEqual(len(chrome.bans), 5)
        self.assertIsNotNone(chrome.ban_icon)
        self.assertLess(chrome.ban_icon.x1, chrome.bans[0].x0)

    def test_a_ban_portrait_crop_can_be_hashed(self):
        """The ban boxes exist to be fed to the hero matcher. A box that cannot
        produce a signature is not useful however well it is drawn."""
        for index, box in enumerate(self.layout.chrome.bans):
            with self.subTest(ban=index):
                crop = self.image[box.y0:box.y1 + 1, box.x0:box.x1 + 1]
                self.assertGreater(crop.size, 0)
                self.assertIsNotNone(portrait_signature(crop))

    def test_the_map_header_is_found_on_the_right_of_the_screen(self):
        chrome = self.layout.chrome
        self.assertIsNotNone(chrome.mode_map)
        centre = self.image.shape[1] / 2
        self.assertGreater((chrome.mode_map.x0 + chrome.mode_map.x1) / 2, centre)
        # A mode and a map name side by side are long. Anything short is a
        # fragment, and cropping a fragment would silently lose the map.
        self.assertGreater(chrome.mode_map.width * self.layout.scale, 400)

    def test_the_clock_is_amber_and_sits_right_of_the_map_name(self):
        chrome = self.layout.chrome
        self.assertIsNotNone(chrome.clock)
        self.assertGreater(chrome.clock.x0, chrome.mode_map.x1)
        crop = self.image[chrome.clock.y0:chrome.clock.y1 + 1,
                          chrome.clock.x0:chrome.clock.x1 + 1]
        blue, _, red = (crop[:, :, i].astype(float).mean() for i in range(3))
        self.assertGreater(red, blue * 1.5, "the match clock is amber, not white")

    def test_the_rank_range_is_exactly_two_badges(self):
        """One badge is a detection error and three is something else caught by
        mistake, so the pair is reported or nothing is."""
        badges = self.layout.chrome.rank_badges
        self.assertEqual(len(badges), 2)
        self.assertTrue(self.layout.chrome.rank_range_found)
        self.assertLess(badges[0].x1, badges[1].x0)
        for badge in badges:
            self.assertGreater(badge.y0, self.layout.chrome.mode_map.y1,
                               "the rank range sits below the map header")

    def test_the_overlay_renders(self):
        self.assertTrue(render_tab(self.image, self.result).startswith(b"\x89PNG"))


@unittest.skipIf(not (TAB_SAMPLES and ENDGAME_SAMPLES), "need both kinds of sample")
class LocalizersDoNotOverlapTest(unittest.TestCase):
    """Neither localizer may accept the other's screenshot.

    Both boards print E A D DMG H MIT on a grey strip above two team-coloured
    blocks, so nothing about the anchors tells them apart — only the spacing of
    the six labels does. If that check ever regresses, the failure is not a
    crash: it is a full set of confident, wrong statistics read out of the
    wrong columns.
    """

    def test_the_endgame_localizer_refuses_the_tab_shot(self):
        for path in TAB_SAMPLES:
            with self.subTest(sample=path.name):
                result = localize(load(path))
                self.assertFalse(result.ok)
                self.assertIn("in-game", result.reason.lower())

    def test_the_tab_localizer_refuses_every_endgame_report(self):
        for path in ENDGAME_SAMPLES:
            with self.subTest(sample=path.name):
                result = localize_tab(load(path))
                self.assertFalse(result.ok, "an endgame report is not a Tab shot")
                self.assertIn("localize.py", result.reason)

    def test_a_tab_refusal_still_carries_enough_to_draw(self):
        for path in ENDGAME_SAMPLES:
            with self.subTest(sample=path.name):
                result = localize_tab(load(path))
                self.assertIn("header", result.diagnostics)
                json.dumps(result.diagnostics)
                self.assertTrue(render_tab(load(path), result).startswith(b"\x89PNG"))


class RegularRunTest(unittest.TestCase):
    """The ban detector's one real idea, tested where counter-examples exist."""

    def test_evenly_spaced_equal_runs_are_kept(self):
        runs = [(0, 70), (83, 153), (166, 236), (249, 319)]
        self.assertEqual(_longest_regular_run(runs), runs)

    def test_a_lone_odd_run_is_dropped(self):
        """The prohibition icon before the ban row: narrower, and closer to the
        first ban than the bans are to each other."""
        runs = [(0, 41), (64, 134), (147, 217), (230, 300)]
        self.assertEqual(_longest_regular_run(runs), runs[1:])

    def test_squares_scattered_across_the_screen_are_rejected(self):
        """Equal widths are not enough. Two runs define exactly one pitch, and
        one pitch is trivially consistent with itself, so without the
        adjacency rule any pair of similar red marks would read as a ban row."""
        self.assertEqual(_longest_regular_run([(0, 70), (200, 270), (500, 570)]), [])

    def test_the_longest_regular_stretch_wins(self):
        """A stray square before the row does not disqualify the row."""
        runs = [(0, 70), (200, 270), (283, 353), (366, 436)]
        self.assertEqual(_longest_regular_run(runs), runs[1:])

    def test_a_single_run_is_not_a_row(self):
        self.assertEqual(_longest_regular_run([(0, 70)]), [])


class TeamBlockSpanTest(unittest.TestCase):
    """A team block is a solid bar, not everything team-coloured on its rows."""

    def _hsv_planes(self, image):
        import cv2
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        return (hsv[:, :, 0].astype(np.int16), hsv[:, :, 1].astype(np.int16),
                hsv[:, :, 2].astype(np.int16))

    def test_a_detached_blob_does_not_stretch_the_block(self):
        """What the in-game sample actually does: a hero portrait in the side
        panel is blue, shares the block's scanlines, and sits well to the right
        of it. Taken as part of the block it moved the right edge 467 canonical
        units and made the board look misaligned."""
        # BGR chosen to land at hue 100, mid-band for the blue mask.
        scoreboard_blue = (200, 133, 0)
        image = np.zeros((400, 1000, 3), np.uint8)
        image[100:300, 100:600] = scoreboard_blue   # the block
        image[150:250, 800:900] = scoreboard_blue   # a detached blob, same hue
        blocks = dict((team, box) for team, box in
                      _find_team_blocks(*self._hsv_planes(image)))
        self.assertIn("blue", blocks)
        self.assertEqual(blocks["blue"].x0, 100)
        self.assertEqual(blocks["blue"].x1, 599)


if __name__ == "__main__":
    unittest.main()
