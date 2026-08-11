"""Stage 5: read one stat cell as a number.

Sits on top of `cells` (geometry and segmentation) and `glyphs` (the atlas), and
is the only place that turns marks into a value.

**Separators are classified, not recognized.** A thousands comma carries no
information and gets stripped anyway, and it is a 6x10 mark whose correlation
score is inherently noisy — measured on held-out data it scores 0.651-0.799
where every digit scores 0.839-0.916. Matching it against a template therefore
does nothing but manufacture doubt about cells that were read perfectly well.
Glyph height separates the two cleanly and resolution-independently (digits
26-29 canonical units, separators 9-11), so the comma is identified by size and
its *position* is spent on a checksum instead.

That checksum matters more than it looks. Classifying by size means a digit
that somehow segmented short would be silently discarded as punctuation — and a
silently dropped digit is exactly the confident-wrong-number the milestone-4
hard stop forbids. Requiring every separator to have a multiple of three digits
to its right, and at least one to its left, catches that.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import cells
from .glyphs import Atlas, DIGITS, cached_atlas, match
from .localize import Layout, TeamBlock


@dataclass(frozen=True)
class CellReading:
    """One cell's outcome. `value is None` always means 'not read', never 0."""
    value: int | None
    confidence: float           # the weakest glyph score behind the value
    text: str = ""              # what was assembled, for the overlay and logs
    problem: str | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None


def read_cell(image: np.ndarray, layout: Layout, block: TeamBlock,
              row: int, column: str, atlas: Atlas | None = None) -> CellReading:
    """Read a single stat cell.

    Never guesses: any doubt anywhere in the cell produces `value=None` with a
    reason, which the review UI shows as an amber cell for the operator to type.
    """
    atlas = atlas if atlas is not None else cached_atlas()
    found = cells.cell_glyphs(image, layout, block, row, column)
    if not found:
        return CellReading(None, 0.0, problem="cell is empty")

    digits = [glyph for glyph in found if glyph.is_digit_sized]
    separators = [glyph for glyph in found if not glyph.is_digit_sized]
    if not digits:
        return CellReading(None, 0.0, problem="no digit-sized glyphs in cell")

    text, lowest = "", 1.0
    for glyph in digits:
        result = match(glyph.image, atlas, only=DIGITS)
        if not result.confident:
            return CellReading(
                None, result.score, text,
                f"glyph {len(text) + 1} of {len(digits)} matched "
                f"{result.character or 'nothing'} at only {result.score:.3f}")
        text += result.character
        lowest = min(lowest, result.score)

    problem = _separator_problem(digits, separators)
    if problem:
        return CellReading(None, lowest, text, problem)
    return CellReading(int(text), lowest, _grouped(text))


def _separator_problem(digits, separators) -> str | None:
    """Check the commas agree with the digits they claim to group."""
    expected = (len(digits) - 1) // 3
    if len(separators) != expected:
        return (f"{len(separators)} separator(s) for {len(digits)} digits, "
                f"expected {expected} — a glyph was probably mis-segmented")
    for separator in separators:
        right = sum(1 for digit in digits if digit.x0 > separator.x0)
        left = len(digits) - right
        if left < 1 or right == 0 or right % 3 != 0:
            return (f"a separator has {left} digit(s) left and {right} right, "
                    f"which is not a thousands boundary")
    return None


def _grouped(digits: str) -> str:
    return f"{int(digits):,}" if digits else ""


def read_row(image: np.ndarray, layout: Layout, block: TeamBlock,
             row: int, atlas: Atlas | None = None) -> dict[str, CellReading]:
    atlas = atlas if atlas is not None else cached_atlas()
    return {column: read_cell(image, layout, block, row, column, atlas)
            for column in cells.COLUMN_NAMES}
