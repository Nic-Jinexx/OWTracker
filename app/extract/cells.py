"""Stage 5 geometry: crop a stat cell and split it into glyphs.

Shared by the reader and by `tools/build_glyph_atlas.py`, which is the point —
the atlas is cut with exactly the code that will later match against it, so a
change to thresholding or segmentation cannot silently desynchronise the two.

Everything here works in canonical units. `crop` resamples straight out of the
source image through the localizer's transform, so one output pixel is one
canonical unit whatever the screenshot's resolution was.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .localize import COLUMN_NAMES, Layout, TeamBlock, _contiguous_runs

# Column centres sit 124 canonical units apart in the E/A/D panel and 226 apart
# in the DMG/H/MIT panel, so a cell can be taken as its centre plus a margin
# without ever reaching a neighbour's digits.
CELL_HALF_WIDTH = {"eliminations": 58.0, "assists": 58.0, "deaths": 58.0,
                   "damage": 105.0, "healing": 105.0, "mitigation": 105.0}
# Rows are 139-190 units tall and the digits are centred in them. Trimming the
# row's own padding keeps the separator lines out of the threshold.
CELL_VERTICAL_INSET = 0.30

# Glyphs are near-white on a saturated team background; a zero renders dimmed
# grey rather than white and must not be dropped (see updates.md → Findings).
# The gap between the dimmest glyph and the brightest background is wide, so
# this is a relative threshold within the cell rather than a fixed level.
GLYPH_RELATIVE_LEVEL = 0.45
GLYPH_MIN_CONTRAST = 25.0       # luminance units; below this the cell is blank

# Segmentation. A comma is small and sits below the baseline; a digit is tall.
GLYPH_MIN_WIDTH = 3
GLYPH_MIN_HEIGHT = 8
# Split on *any* blank column. Measured over 72 cells: the gap between adjacent
# glyphs gets as tight as one column, while no single glyph ever contains a
# blank column, so a larger tolerance only merges neighbours. Even at 1 a
# handful of pairs touch with no gap at all and cannot be split by projection —
# `segment` reports those rather than guessing, and the atlas builder skips
# them. The reader will need a sliding-window match to handle them.
GLYPH_COLUMN_GAP = 1
DIGIT_MIN_HEIGHT = 18.0         # anything shorter is punctuation, not a digit

# Splitting glyphs that touch. Measured digit widths are 18-22 canonical units
# ('1' is 9-10), and a merged pair comes out at 45. Estimating the count as
# round(width / 21) therefore only ever fires above 32 units, which leaves a
# 10-unit margin over the widest real digit — and when it does not fire the
# result is an unreadable cell, never a wrong one.
TYPICAL_DIGIT_WIDTH = 21.0
SPLIT_MIN_WIDTH_RATIO = 1.5     # below this, never attempt a split
SPLIT_SEARCH_FRACTION = 0.25    # of the expected part width, either side


@dataclass
class Glyph:
    """One connected mark cut from a cell, in canonical units."""
    image: np.ndarray           # uint8 mask, 0 or 255
    x0: float
    y0: float
    width: int
    height: int

    @property
    def is_digit_sized(self) -> bool:
        return self.height >= DIGIT_MIN_HEIGHT


def crop(image: np.ndarray, layout: Layout,
         x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    """Resample a canonical-space rectangle out of the source image.

    Uses one affine warp rather than slice-then-resize so the sampling is
    subpixel-accurate — at these scales a half-pixel shift visibly thickens a
    glyph stroke, which is exactly the kind of error template matching feels.
    """
    scale = layout.scale
    matrix = np.array([
        [scale, 0.0, -layout.board.x0 * scale - x0],
        [0.0, scale, -layout.board.y0 * scale - y0],
    ], dtype=np.float64)
    width, height = max(1, int(round(x1 - x0))), max(1, int(round(y1 - y0)))
    return cv2.warpAffine(image, matrix, (width, height),
                          flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


# -- the left of the row: role icon, portrait, nameplate ---------------------
#
# Everything below x=850 was uncharted until now. These are fixed canonical
# rectangles, exactly like the column centres, because per-row detection was
# tried and does not work: the white role icon and the nameplate's own graphic
# both read as low-saturation, so a "find the portrait by saturation" pass
# returns a different span on every row.
#
# All three are anchored to the TOP of the row, not its centre. Rows run 140-184
# canonical units depending on whether a title line hangs below the name, so a
# centred crop drifts downward exactly on the rows that have the most going on.
PORTRAIT_BOUNDS = (62.0, 8.0, 178.0, 130.0)      # x0, y0-from-row-top, x1, y1
ROLE_ICON_BOUNDS = (6.0, 30.0, 58.0, 100.0)
# Wide enough for a long name, stopping well short of the E column at x=850.
NAMEPLATE_BOUNDS = (188.0, 28.0, 520.0, 104.0)


def region_image(image: np.ndarray, layout: Layout, block: TeamBlock, row: int,
                 bounds: tuple[float, float, float, float]) -> np.ndarray:
    """Crop one of the fixed row regions above, in canonical units."""
    top, _ = layout.row_bounds(block, row)
    x0, y0, x1, y1 = bounds
    return crop(image, layout, x0, top + y0, x1, top + y1)


def portrait_image(image, layout, block, row) -> np.ndarray:
    return region_image(image, layout, block, row, PORTRAIT_BOUNDS)


def nameplate_image(image, layout, block, row) -> np.ndarray:
    return region_image(image, layout, block, row, NAMEPLATE_BOUNDS)


def cell_bounds(layout: Layout, block: TeamBlock, row: int,
                column: str) -> tuple[float, float, float, float]:
    """Canonical rectangle of one stat cell."""
    if column not in CELL_HALF_WIDTH:
        raise KeyError(f"Unknown stat column: {column}")
    top, bottom = layout.row_bounds(block, row)
    inset = (bottom - top) * CELL_VERTICAL_INSET / 2
    centre = layout.columns[column]
    half = CELL_HALF_WIDTH[column]
    return (centre - half, top + inset, centre + half, bottom - inset)


def cell_image(image: np.ndarray, layout: Layout, block: TeamBlock,
               row: int, column: str) -> np.ndarray:
    return crop(image, layout, *cell_bounds(layout, block, row, column))


def glyph_mask(cell: np.ndarray) -> np.ndarray | None:
    """Isolate the glyphs in a cell. None if the cell holds no text.

    The threshold is relative to the cell's own range because the background is
    a different colour in each block and each row, and because a zero is
    rendered dimmed. An absolute level tuned on white digits drops every zero.
    """
    grey = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY) if cell.ndim == 3 else cell
    grey = grey.astype(np.float32)
    floor, ceiling = float(np.percentile(grey, 5)), float(grey.max())
    if ceiling - floor < GLYPH_MIN_CONTRAST:
        return None
    level = floor + (ceiling - floor) * GLYPH_RELATIVE_LEVEL
    return ((grey > level).astype(np.uint8) * 255)


def segment(mask: np.ndarray) -> list[Glyph]:
    """Split a cell mask into glyphs, left to right.

    Splits on blank columns rather than running a connected-components pass:
    the scoreboard font never kerns two glyphs into overlapping columns, and a
    column projection cannot accidentally merge a comma into the digit beside
    it the way 8-connectivity can when antialiasing bridges them.
    """
    if mask is None or mask.size == 0:
        return []
    occupied = np.flatnonzero(mask.max(axis=0) > 0)
    glyphs: list[Glyph] = []
    for x0, x1 in _contiguous_runs(occupied, tolerance=GLYPH_COLUMN_GAP):
        if x1 - x0 + 1 < GLYPH_MIN_WIDTH:
            continue
        column = mask[:, x0:x1 + 1]
        rows = np.flatnonzero(column.max(axis=1) > 0)
        if rows.size < GLYPH_MIN_HEIGHT:
            continue
        y0, y1 = int(rows.min()), int(rows.max())
        glyphs.extend(_split_if_merged(
            Glyph(image=column[y0:y1 + 1], x0=float(x0), y0=float(y0),
                  width=x1 - x0 + 1, height=y1 - y0 + 1)))
    return glyphs


def _split_if_merged(glyph: Glyph) -> list[Glyph]:
    """Cut a glyph that is plainly several digits touching.

    Adjacent digits sometimes meet with no blank column between them, so the
    projection cannot separate them — but they still meet at a thin waist,
    2-5 ink pixels against a 27-pixel stroke height. This looks for that waist,
    and only near where a boundary would have to be if the glyph really is `n`
    digits wide. Searching the whole glyph instead would happily cut the thin
    leading diagonal off a '4'.
    """
    parts = int(round(glyph.width / TYPICAL_DIGIT_WIDTH))
    if parts < 2 or glyph.width < TYPICAL_DIGIT_WIDTH * SPLIT_MIN_WIDTH_RATIO:
        return [glyph]

    ink = (glyph.image > 0).sum(axis=0)
    span = glyph.width / parts
    margin = max(2, int(span * SPLIT_SEARCH_FRACTION))

    cuts: list[int] = []
    for index in range(1, parts):
        centre = int(round(span * index))
        low = max(cuts[-1] + GLYPH_MIN_WIDTH if cuts else 1, centre - margin)
        high = min(glyph.width - 1, centre + margin)
        if low >= high:
            return [glyph]
        cuts.append(low + int(np.argmin(ink[low:high])))

    pieces: list[Glyph] = []
    for start, end in zip([0] + cuts, cuts + [glyph.width]):
        if end - start < GLYPH_MIN_WIDTH:
            return [glyph]      # a split this lopsided is not a split
        block = glyph.image[:, start:end]
        rows = np.flatnonzero(block.max(axis=1) > 0)
        if rows.size < GLYPH_MIN_HEIGHT:
            return [glyph]
        top, bottom = int(rows.min()), int(rows.max())
        pieces.append(Glyph(image=block[top:bottom + 1],
                            x0=glyph.x0 + start, y0=glyph.y0 + top,
                            width=end - start, height=bottom - top + 1))
    return pieces


def cell_glyphs(image: np.ndarray, layout: Layout, block: TeamBlock,
                row: int, column: str) -> list[Glyph]:
    return segment(glyph_mask(cell_image(image, layout, block, row, column)))


def iter_cells(layout: Layout):
    """Every stat cell in the scoreboard, as (block, row_index, column)."""
    for block in layout.teams:
        for row in range(block.row_count):
            for column in COLUMN_NAMES:
                yield block, row, column
