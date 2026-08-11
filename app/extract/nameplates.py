"""Stage 7: recognize a nameplate, so a name is typed once and never again.

The spec's plan was a perceptual hash of the nameplate crop. **Measured over the
sample corpus, that does not work.** Some players have an animated nameplate —
a coloured glow that trails off to the right and is caught at a different frame
in every screenshot — and it dominates a 64-bit DCT hash completely:

    same player, worst case      24 bits apart
    different players, best case 16 bits apart

The distributions overlap, so no threshold separates them. Normalized
cross-correlation on the raw crop is worse still, and *inverted*: two glowing
plates both reduce to a bright blob, so different players correlate at 0.67
while one player against himself manages 0.15.

What works is to throw the glow away and keep the letters. The name is rendered
in near-white; the glow is coloured. Masking on "bright and unsaturated" leaves
the text, and a fixed-geometry downsample of that mask is a stable signature:

    same player, worst case      34-84 bits of 480
    different players, best case 44 bits of 480

Those still overlap pairwise — but classification is nearest-neighbour, not
thresholding, and the *nearest* stored signature for a player is always their
own:

    nearest signature, same player       0-35 bits (23 of 24 appearances)
    nearest signature, different player  never closer than 44

The threshold lives in that gap. It has to be chosen against **strangers**, not
against leave-one-out accuracy: leave-one-out always has the right answer
somewhere in the library, so it tolerates a loose threshold happily. Most people
in any lobby have never been typed in, and at 60 bits one of them matched the
nearest acquaintance at 49. At 35 nothing does, and 23 of 24 known appearances
are still recognized.

Two consequences shape the rest of the design:

- **Several signatures per player, accumulating.** Every confirmed name stores
  the signature of that appearance, so an animated plate gradually gets covered
  at several frames and recall improves with use. `player_nameplates` was always
  one-to-many; this is what it is for.
- **A miss is cheap, a false positive is not.** Failing to recognize someone
  costs one typed name. Recognizing them as the wrong person silently attributes
  a stranger's statistics to a teammate, and nothing downstream would ever
  reveal it. The threshold is set to make the second outcome vanishingly
  unlikely and the operator always sees the crop beside the suggestion.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import cells
from .localize import Layout, TeamBlock

# The signature grid. 48x10 = 480 bits, stored as 120 hex characters in
# `player_nameplates.phash`.
GRID_WIDTH = 48
GRID_HEIGHT = 10
SIGNATURE_BITS = GRID_WIDTH * GRID_HEIGHT

# "Bright and unsaturated" is the name; anything coloured is the plate or its
# glow. Measured on the corpus: text sits above V=200 at S<50, the glow is
# strongly saturated, and the plate body is mid-value.
TEXT_MIN_VALUE = 190
TEXT_MAX_SATURATION = 70

# A crop with almost no bright text in it is an empty nameplate, which is
# normal — four of the seven samples have at least one player with no plate at
# all. Hashing a blank would give every blank the same signature and merrily
# match them all to each other.
MIN_INK_FRACTION = 0.02


@dataclass(frozen=True)
class NameplateMatch:
    player_id: int | None
    distance: int
    confidence: float
    runner_up_distance: int | None = None

    @property
    def recognized(self) -> bool:
        return self.player_id is not None


def signature(crop_bgr: np.ndarray) -> str | None:
    """A 480-bit white-text signature as hex, or None for a blank nameplate."""
    if crop_bgr is None or crop_bgr.size == 0:
        return None
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    value = hsv[:, :, 2].astype(np.int16)
    saturation = hsv[:, :, 1].astype(np.int16)
    mask = ((value > TEXT_MIN_VALUE) & (saturation < TEXT_MAX_SATURATION)).astype(np.float32)
    if float(mask.mean()) < MIN_INK_FRACTION:
        return None

    grid = cv2.resize(mask, (GRID_WIDTH, GRID_HEIGHT), interpolation=cv2.INTER_AREA)
    bits = (grid > grid.mean()).ravel()

    value_out = 0
    for bit in bits:
        value_out = (value_out << 1) | int(bit)
    return f"{value_out:0{SIGNATURE_BITS // 4}x}"


def row_signature(image, layout: Layout, block: TeamBlock, row: int) -> str | None:
    return signature(cells.nameplate_image(image, layout, block, row))


def distance(left: str, right: str) -> int:
    """Bit distance between two hex signatures of equal length."""
    return bin(int(left, 16) ^ int(right, 16)).count("1")


def match(candidate: str | None, library: list[tuple[int, str]],
          max_distance: int) -> NameplateMatch:
    """Nearest stored signature, if it is close enough.

    `library` is (player_id, signature) and deliberately holds several rows per
    player. Nearest-neighbour rather than a per-player average: averaging two
    frames of an animated plate produces a signature that matches neither.
    """
    if not candidate or not library:
        return NameplateMatch(None, SIGNATURE_BITS, 0.0)

    best_id, best = None, SIGNATURE_BITS + 1
    runner_up = None
    for player_id, known in library:
        if len(known) != len(candidate):
            continue        # a signature from an older grid size
        gap = distance(candidate, known)
        if gap < best:
            if best_id is not None and best_id != player_id:
                runner_up = best
            best_id, best = player_id, gap
        elif player_id != best_id and (runner_up is None or gap < runner_up):
            runner_up = gap

    if best_id is None or best > max_distance:
        return NameplateMatch(None, best, 0.0, runner_up)
    return NameplateMatch(best_id, best, confidence_from_distance(best, max_distance),
                          runner_up)


def confidence_from_distance(gap: int, max_distance: int) -> float:
    """0 bits is 1.0; sitting on the tolerance limit is 0.5, which renders amber
    under any sensible `confidence_threshold`."""
    if max_distance <= 0:
        return 1.0 if gap == 0 else 0.0
    if gap > max_distance:
        return 0.0
    return max(0.0, 1.0 - (gap / float(max_distance)) * 0.5)


def load_library(conn) -> list[tuple[int, str]]:
    """Every stored signature. Small enough to scan; the phash index only helps
    exact lookups, and this is a Hamming search."""
    return [(row["player_id"], row["phash"])
            for row in conn.execute(
                "SELECT player_id, phash FROM player_nameplates")]
