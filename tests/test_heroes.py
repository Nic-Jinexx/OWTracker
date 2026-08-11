"""Hero identification from portraits.

Same asymmetry as nameplates and the digit reader: unknown is a fine answer,
wrong is not. A misidentified hero silently corrupts every hero win rate, every
comp, and the map-hero cross-tab, and nothing downstream would show it.

The library here is bootstrapped by labelling the sample corpus, so it covers
only the heroes that have appeared and been confirmed. Every test is written to
pass with a partial library, because a partial library is the normal state.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import paths                                          # noqa: E402
from app.extract import cells, heroes                          # noqa: E402
from app.extract.local import LocalExtractor                   # noqa: E402
from app.extract.localize import localize                      # noqa: E402

LIBRARY = heroes.load_hero_library()
MAX_DISTANCE = 8        # the shipped default

SAMPLES = sorted(paths.SAMPLES_DIR.glob("endgame_*.png")) + \
    [paths.SAMPLES_DIR / n for n in ("kingsrowloss.png", "nepalloss.png", "route66Loss.png")]
SAMPLES = [p for p in SAMPLES if p.is_file()]


def identifications():
    """(sample, team, row) -> hero name or None, over the whole corpus."""
    out = {}
    for path in SAMPLES:
        image = LocalExtractor._load(str(path))
        result = localize(image)
        if not result.ok:
            continue
        for block in result.layout.teams:
            for row in range(block.row_count):
                match = heroes.identify_portrait(image, result.layout, block, row,
                                                 LIBRARY, MAX_DISTANCE)
                out[(path.name, block.team, row)] = match.name
    return out


@unittest.skipIf(not LIBRARY, "seed/hero_hashes.json is not built")
class LibraryTest(unittest.TestCase):

    def test_every_name_is_a_real_hero(self):
        import json
        known = {h["name"] for h in json.loads(
            (paths.SEED_DIR / "heroes.json").read_text(encoding="utf-8"))}
        for name in LIBRARY:
            with self.subTest(hero=name):
                self.assertIn(name, known)

    def test_hashes_are_sixteen_hex_characters(self):
        for name, hashes in LIBRARY.items():
            for value in hashes:
                with self.subTest(hero=name):
                    self.assertEqual(len(value), 16)
                    int(value, 16)

    def test_no_hash_is_claimed_by_two_heroes(self):
        """The same bits under two names means the labelling merged two heroes,
        and every later identification between them is a coin flip."""
        seen = {}
        for name, hashes in LIBRARY.items():
            for value in hashes:
                self.assertNotIn(value, seen,
                                 f"{name} and {seen.get(value)} share a hash")
                seen[value] = name

    def test_heroes_are_far_enough_apart_to_tell_apart(self):
        """Two heroes within the tolerance of each other cannot be separated no
        matter how good the crop is."""
        names = sorted(LIBRARY)
        for i, first in enumerate(names):
            for second in names[i + 1:]:
                gap = min(heroes.hamming(a, b)
                          for a in LIBRARY[first] for b in LIBRARY[second])
                with self.subTest(pair=(first, second)):
                    self.assertGreater(gap, MAX_DISTANCE,
                                       f"{first} and {second} are {gap} bits apart")


@unittest.skipIf(not LIBRARY, "seed/hero_hashes.json is not built")
@unittest.skipIf(not SAMPLES, "no screenshots in samples/")
class IdentificationTest(unittest.TestCase):
    """Needs both halves: a library *and* a corpus to run it against. The
    published repository ships the library but withholds the screenshots, which
    are of real players, so these skip on a fresh clone rather than failing."""

    def setUp(self):
        self.results = identifications()

    def test_something_is_identified(self):
        named = [v for v in self.results.values() if v]
        self.assertTrue(named, "the library covers nothing in the corpus")

    def test_mirror_picks_resolve_on_both_teams(self):
        """The milestone-4 hard stop names this case explicitly. The row
        background is strongly team-coloured, so a crop that leaked too much of
        it would identify the *team* rather than the hero and the same portrait
        would resolve differently on blue and red."""
        by_team = {"blue": set(), "red": set()}
        for (_, team, _), name in self.results.items():
            if name:
                by_team[team].add(name)
        both = by_team["blue"] & by_team["red"]
        self.assertTrue(both,
                        "no hero appears identified on both sides — either the "
                        "corpus has no mirror picks or the crop is reading the "
                        "team colour")

    def test_the_same_portrait_always_resolves_the_same_way(self):
        """Consistency is checkable without full ground truth: identical
        portraits must not disagree between screenshots."""
        by_hash = {}
        for path in SAMPLES:
            image = LocalExtractor._load(str(path))
            result = localize(image)
            if not result.ok:
                continue
            for block in result.layout.teams:
                for row in range(block.row_count):
                    crop = cells.portrait_image(image, result.layout, block, row)
                    signature = heroes.portrait_signature(crop)
                    match = heroes.identify(signature, LIBRARY, MAX_DISTANCE)
                    if signature and match.name:
                        previous = by_hash.setdefault(signature, match.name)
                        self.assertEqual(previous, match.name)

    def test_an_unknown_portrait_is_unknown_not_the_nearest_hero(self):
        rng = np.random.default_rng(5)
        noise = rng.integers(0, 255, (122, 116, 3), dtype=np.uint8)
        match = heroes.identify(heroes.portrait_signature(noise), LIBRARY, MAX_DISTANCE)
        self.assertFalse(match.identified)

    def test_a_hero_missing_from_the_library_is_left_blank(self):
        """Drop a hero and every appearance of it must go blank, not slide to
        the next-nearest hero."""
        target = max(LIBRARY, key=lambda k: len(LIBRARY[k]))
        reduced = {k: v for k, v in LIBRARY.items() if k != target}
        for path in SAMPLES:
            image = LocalExtractor._load(str(path))
            result = localize(image)
            if not result.ok:
                continue
            for block in result.layout.teams:
                for row in range(block.row_count):
                    full = heroes.identify_portrait(image, result.layout, block, row,
                                                    LIBRARY, MAX_DISTANCE)
                    if full.name != target:
                        continue
                    without = heroes.identify_portrait(image, result.layout, block, row,
                                                       reduced, MAX_DISTANCE)
                    with self.subTest(sample=path.name, team=block.team, row=row):
                        self.assertIsNone(without.name,
                                          f"{target} became {without.name} once removed")

    def test_an_empty_library_identifies_nothing(self):
        self.assertFalse(heroes.identify("0" * 16, {}, MAX_DISTANCE).identified)

    def test_a_tie_is_refused(self):
        """Two heroes within MIN_MARGIN_BITS of the probe is a coin flip, and
        resolving one anyway is the confident-wrong-answer the hard stop
        forbids."""
        probe = "0000000000000000"
        library = {"A": ["0000000000000001"], "B": ["0000000000000003"]}
        self.assertFalse(heroes.identify(probe, library, MAX_DISTANCE).identified)


@unittest.skipIf(not SAMPLES, "sample corpus missing")
class PortraitGeometryTest(unittest.TestCase):

    def test_the_portrait_crop_is_stable_across_resolutions(self):
        base = LocalExtractor._load(str(paths.SAMPLES_DIR / "kingsrowloss.png"))
        reference = None
        for factor in (1.0, 0.85, 1.3):
            image = base if factor == 1.0 else cv2.resize(
                base, None, fx=factor, fy=factor,
                interpolation=cv2.INTER_AREA if factor < 1 else cv2.INTER_CUBIC)
            layout = localize(image).layout
            block = next(b for b in layout.teams if b.team == "blue")
            signature = heroes.portrait_signature(
                cells.portrait_image(image, layout, block, 0))
            if reference is None:
                reference = signature
            else:
                with self.subTest(factor=factor):
                    self.assertLessEqual(heroes.hamming(reference, signature),
                                         MAX_DISTANCE)

    def test_every_portrait_crop_is_the_same_size(self):
        image = LocalExtractor._load(str(paths.SAMPLES_DIR / "kingsrowloss.png"))
        layout = localize(image).layout
        sizes = {cells.portrait_image(image, layout, block, row).shape[:2]
                 for block in layout.teams for row in range(block.row_count)}
        self.assertEqual(len(sizes), 1)


if __name__ == "__main__":
    unittest.main()
