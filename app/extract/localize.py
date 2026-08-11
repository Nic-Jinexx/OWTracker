"""Stage 1-4: find the scoreboard and build the canonical transform.

CLAUDE-OWTRACKER.md, Extraction → Stages 1-4. This module answers "where is
everything" and nothing else; it never reads a value. Downstream stages crop
in normalized space and stay ignorant of the source resolution.

**Anchors.** The spec names the column-header strip and the VS divider. Both
are found structurally — by colour and geometry — rather than by
`matchTemplate` against reference bitmaps:

- The header strip is the only wide, flat, desaturated bar in the shot. It
  measures a constant BGR (218, 212, 208) in every sample and its horizontal
  extent *is* the scoreboard's left and right edge, which is exactly the
  measurement the transform needs.
- The team blocks are the only large saturated blue and red regions. Splitting
  on hue rather than position is required by stage 2 anyway, and it locates the
  VS divider for free as the gap between them.

Template matching would need a reference bitmap per anchor per patch, and would
still have to search over scale. The structural route has no reference data,
no scale search, and fails loudly instead of returning a weak correlation peak.
It is a deliberate deviation from the letter of the spec's stage 1, not an
oversight; the contract (anchors in, canonical transform out) is unchanged.

**Canonical space.** The scoreboard, not the screen, defines the coordinate
system: its left edge is x=0, its width is `CANONICAL_WIDTH`, and the top of
the header strip is y=0. A crop and a full-screen capture of the same match
therefore normalize identically, which a screen-relative transform could not
manage. Vertical extent is *not* normalized to a constant — rows grow when a
player has a title line under their name, so the block height genuinely varies
between shots of the same resolution.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

import cv2
import numpy as np

# The scoreboard's own width, in canonical units. Everything downstream is
# expressed in these.
CANONICAL_WIDTH = 1920.0

# -- anchor thresholds -------------------------------------------------------
# The header strip: desaturated and bright. Real scoreboard content never is —
# even white digits sit on a saturated team-coloured background, and the
# surrounding menu backdrop is dark.
HEADER_MAX_SAT = 40
HEADER_MIN_VAL = 170
HEADER_MIN_ROW_COVERAGE = 0.30   # fraction of image width the strip must span
HEADER_MIN_HEIGHT = 8            # px, guards against a stray bright scanline

# Team blocks. OpenCV hue is 0-179; blue sits near 100, red wraps around 0.
BLUE_HUE = (92, 110)
RED_HUE_LOW = 8
RED_HUE_HIGH = 168
TEAM_MIN_SAT = 120
TEAM_MIN_VAL = 40
TEAM_MIN_ROW_COVERAGE = 0.15
# Largest gap inside a block's colour that still counts as the same block. The
# nameplate and portrait interrupt the field by a few px; nothing legitimately
# interrupts it by 8.
TEAM_COLUMN_GAP = 8

# The strip must line up with the blocks it heads, in canonical units.
EDGE_ALIGNMENT_TOLERANCE = 40.0

# -- column labels -----------------------------------------------------------
COLUMN_NAMES = ("eliminations", "assists", "deaths", "damage", "healing", "mitigation")
COLUMN_LABELS = ("E", "A", "D", "DMG", "H", "MIT")
# Three-glyph labels are several times wider than one-glyph labels. Checking the
# *pattern* of widths identifies the header without hard-coding any position.
WIDE_COLUMNS = (3, 5)
LABEL_DARK_MAX = 150            # dark text on the light strip
LABEL_GROUP_GAP = 12            # px between labels; intra-word gaps are ~2
LABEL_MIN_WIDTH = 4             # px

# Where the six labels land on an endgame report. The transform uses the
# centres actually detected; these are the signature the layout is checked
# against, because they identify *which* scoreboard this is.
EXPECTED_COLUMN_X = (908.0, 1032.0, 1156.0, 1354.0, 1580.0, 1806.0)
# Across the seven endgame samples — 1190px to 2502px wide, scales 1.86 to 2.02
# — no column drifts more than 1.6 units from these. The in-game Tab board
# packs the same six columns left to reserve its right edge for the mute
# buttons, and drifts up to 74.5. Anywhere in between is neither layout, and
# refusing beats reading the wrong pixels confidently.
COLUMN_SIGNATURE_TOLERANCE = 20.0

# -- rows --------------------------------------------------------------------
# A vertical band with no glyphs in it, so a luminance step across it can only
# be a row separator. Sits midway between the H and MIT column centres, which
# leaves ~110 canonical units of clearance either side — about twice the widest
# value the game can render in a stat cell.
ROW_PROBE_BAND = (1660.0, 1730.0)
# A separator shows up as a dark dip (54 -> 40 -> 54), a shading step
# (98 -> 76), or both. What is constant across resolutions is the *size* of
# that luminance change, not its steepness: upscaling spreads the same 14-unit
# change over more pixels, so a per-pixel derivative shrinks while the feature
# is unchanged. Detection therefore measures the peak-to-trough range inside a
# sliding window, in canonical units, which responds to dips and steps alike
# and does not care how many pixels the transition occupies.
ROW_EDGE_WINDOW = 16.0          # canonical units; wider than any separator,
                                # far narrower than any row
# Measured over the whole corpus at 0.5x-2.2x: the weakest separator scores
# 7.9 and the loudest row interior scores 0.44. 5.0 sits an order of magnitude
# above the noise and still well under the weakest real separator.
ROW_EDGE_MIN_RANGE = 5.0        # luminance units, 0-255
ROW_MIN_HEIGHT = 100.0          # canonical units; measured rows run 139-190


@dataclass
class Box:
    """A rectangle in source pixels. Inclusive of both bounds."""
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0 + 1

    @property
    def height(self) -> int:
        return self.y1 - self.y0 + 1


@dataclass
class TeamBlock:
    """One team's block of rows.

    `team` is assigned from the background hue, never from vertical position —
    stage 2 is explicit that position is not to be trusted. `is_top` records
    the order that was observed so a caller can notice if it ever inverts.
    """
    team: str                    # "blue" or "red"
    box: Box
    is_top: bool
    row_edges: list[float] = field(default_factory=list)   # canonical y, block-relative

    @property
    def row_count(self) -> int:
        return max(0, len(self.row_edges) - 1)


@dataclass
class Layout:
    """The canonical transform plus everything located in canonical space."""
    board: Box
    header: Box
    scale: float                 # source px -> canonical units
    columns: dict[str, float]    # canonical x centre per stat column
    teams: list[TeamBlock]
    vs_divider_y: float | None   # canonical y, or None if the gap was featureless

    # -- coordinate transform ------------------------------------------------

    def to_canonical(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.board.x0) * self.scale, (y - self.board.y0) * self.scale)

    def to_source(self, x: float, y: float) -> tuple[float, float]:
        return (x / self.scale + self.board.x0, y / self.scale + self.board.y0)

    def row_bounds(self, block: TeamBlock, index: int) -> tuple[float, float]:
        """Canonical y bounds of one row, absolute (not block-relative)."""
        top = self.to_canonical(0, block.box.y0)[1]
        return (top + block.row_edges[index], top + block.row_edges[index + 1])


@dataclass
class LocalizeResult:
    ok: bool
    layout: Layout | None = None
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    # Whatever was found before giving up, JSON-serializable, so the debug
    # overlay can render a partial failure instead of a blank image.
    diagnostics: dict = field(default_factory=dict)


def localize(image: np.ndarray) -> LocalizeResult:
    """Locate the scoreboard in an endgame report.

    Never raises on unexpected content: an image that is not a scoreboard comes
    back `ok=False` with a reason, which is what the extractor's no-crash
    contract needs.
    """
    try:
        return _localize(image)
    except (cv2.error, ValueError, IndexError) as error:  # pragma: no cover - defensive
        return LocalizeResult(ok=False, reason=f"Localization failed: {error}")


def _localize(image: np.ndarray) -> LocalizeResult:
    if image is None or image.ndim != 3 or image.size == 0:
        return LocalizeResult(ok=False, reason="Not a colour image.")

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(np.int16)
    sat = hsv[:, :, 1].astype(np.int16)
    val = hsv[:, :, 2].astype(np.int16)
    diagnostics: dict = {"image": {"width": image.shape[1], "height": image.shape[0]}}
    warnings: list[str] = []

    header = _find_header_strip(sat, val)
    if header is None:
        return LocalizeResult(
            ok=False, diagnostics=diagnostics,
            reason="No column-header strip found — this does not look like an endgame report.")
    diagnostics["header"] = asdict(header)

    blocks = _find_team_blocks(hue, sat, val)
    if len(blocks) < 2:
        found = ", ".join(t for t, _ in blocks) or "none"
        return LocalizeResult(
            ok=False, diagnostics=diagnostics,
            reason=f"Expected a blue and a red team block; found {found}.")
    diagnostics["blocks"] = {team: asdict(box) for team, box in blocks}

    board_width = header.width
    scale = CANONICAL_WIDTH / board_width

    # Identify *which* board this is before checking the geometry of the board
    # we assume we have. Both scoreboards carry the same six labels, so their
    # spacing is the only thing that separates them — and it separates them by
    # a factor of 45. Doing this first means a Tab shot is refused for what it
    # actually is, rather than for whichever sanity check it happens to trip:
    # its red mute buttons render darker than the blue ones, so the red mask
    # stops 84 units short of the strip and the alignment check would otherwise
    # fire first, reporting a mis-detected block instead of the wrong screen.
    columns, column_warnings = _find_columns(image, header, scale)
    warnings.extend(column_warnings)
    if columns is None:
        return LocalizeResult(
            ok=False, diagnostics=diagnostics, warnings=warnings,
            reason="Could not read the six column labels off the header strip.")
    diagnostics["columns"] = columns

    drift = column_drift(columns)
    diagnostics["column_drift"] = {k: round(v, 1) for k, v in drift.items()}
    worst = max(drift, key=lambda name: abs(drift[name]))
    if abs(drift[worst]) > COLUMN_SIGNATURE_TOLERANCE:
        return LocalizeResult(
            ok=False, diagnostics=diagnostics, warnings=warnings,
            reason=(
                f"Column '{worst}' sits {drift[worst]:+.0f} canonical units from where every "
                f"endgame report puts it, which is far outside the ±2 the corpus agrees "
                f"within. Columns packed leftward like this are the signature of the in-game "
                f"Tab scoreboard, which reserves its right edge for the per-player mute "
                f"buttons. A Tab shot holds the map, mode, bans and rank range rather than "
                f"final statistics — app/extract/tab.py is the localizer for it."))

    # The strip must head the blocks, not merely exist somewhere in the shot.
    for team, box in blocks:
        left = (box.x0 - header.x0) * scale
        right = (box.x1 - header.x1) * scale
        diagnostics.setdefault("block_alignment", {})[team] = {
            "left": round(left, 1), "right": round(right, 1)}
        if abs(left) <= EDGE_ALIGNMENT_TOLERANCE and abs(right) <= EDGE_ALIGNMENT_TOLERANCE:
            continue
        # Not the Tab check: the Tab board aligns with its header strip just as
        # tightly as an endgame report does (its extra columns are *inside* the
        # strip, not beyond it). Landing here means the coloured region found
        # is not a team block at all — a background render, a menu panel, or a
        # crop that cut the board off.
        return LocalizeResult(
            ok=False, diagnostics=diagnostics,
            reason=(f"The {team} block does not line up with the header strip "
                    f"({left:+.0f} left, {right:+.0f} right, in canonical units)."))
    if min(box.y0 for _, box in blocks) < header.y1:
        return LocalizeResult(
            ok=False, diagnostics=diagnostics,
            reason="The header strip is not above the team blocks.")

    board = Box(header.x0, header.y0, header.x1, max(box.y1 for _, box in blocks))
    diagnostics["board"] = asdict(board)
    diagnostics["scale"] = scale

    ordered = sorted(blocks, key=lambda item: item[1].y0)
    teams: list[TeamBlock] = []
    for position, (team, box) in enumerate(ordered):
        edges, row_warnings = _find_row_edges(image, box, board, scale)
        warnings.extend(f"{team} block: {message}" for message in row_warnings)
        teams.append(TeamBlock(team=team, box=box, is_top=(position == 0), row_edges=edges))

    if teams[0].team != "blue":
        warnings.append(
            "The red block is on top. Team assignment follows the hue, so the blue "
            "block is still treated as yours — check the review grid.")

    counts = {block.team: block.row_count for block in teams}
    diagnostics["row_counts"] = counts
    if len(set(counts.values())) > 1:
        warnings.append(
            f"Team sizes differ between blocks: {counts['blue']} blue, {counts['red']} red. "
            "One block may have a mis-detected separator.")

    divider = _find_vs_divider(image, teams[0].box, teams[1].box, board, scale)
    if divider is None:
        warnings.append("No VS divider found between the blocks; using the colour gap instead.")
    diagnostics["vs_divider_y"] = divider

    layout = Layout(board=board, header=header, scale=scale, columns=columns,
                    teams=teams, vs_divider_y=divider)
    diagnostics["teams"] = [
        {"team": block.team, "is_top": block.is_top, "rows": block.row_count,
         "box": asdict(block.box), "row_edges": [round(e, 1) for e in block.row_edges]}
        for block in teams
    ]
    return LocalizeResult(ok=True, layout=layout, warnings=warnings, diagnostics=diagnostics)


# -- anchors -----------------------------------------------------------------


def _find_header_strip(sat: np.ndarray, val: np.ndarray) -> Box | None:
    """The widest run of bright desaturated rows.

    Taking the *widest* run rather than the first is what keeps a bright sky in
    a background render from being mistaken for the strip.
    """
    mask = (sat < HEADER_MAX_SAT) & (val > HEADER_MIN_VAL)
    qualifying = np.flatnonzero(mask.mean(axis=1) > HEADER_MIN_ROW_COVERAGE)
    if qualifying.size == 0:
        return None

    best: Box | None = None
    for y0, y1 in _contiguous_runs(qualifying):
        if y1 - y0 + 1 < HEADER_MIN_HEIGHT:
            continue
        # The middle row avoids the antialiased top and bottom edges, and the
        # rounded corners that make the first and last rows narrower.
        columns = np.flatnonzero(mask[(y0 + y1) // 2])
        if columns.size == 0:
            continue
        candidate = Box(int(columns.min()), int(y0), int(columns.max()), int(y1))
        if best is None or candidate.width > best.width:
            best = candidate
    return best


def _find_team_blocks(hue, sat, val) -> list[tuple[str, Box]]:
    """Largest blue and red regions, by row coverage."""
    saturated = (sat > TEAM_MIN_SAT) & (val > TEAM_MIN_VAL)
    masks = {
        "blue": saturated & (hue > BLUE_HUE[0]) & (hue < BLUE_HUE[1]),
        "red": saturated & ((hue < RED_HUE_LOW) | (hue > RED_HUE_HIGH)),
    }
    found: list[tuple[str, Box]] = []
    for team, mask in masks.items():
        rows = np.flatnonzero(mask.mean(axis=1) > TEAM_MIN_ROW_COVERAGE)
        if rows.size == 0:
            continue
        runs = _contiguous_runs(rows, tolerance=4)
        y0, y1 = max(runs, key=lambda run: run[1] - run[0])
        columns = np.flatnonzero(mask[y0:y1 + 1].mean(axis=0) > 0.05)
        if columns.size == 0:
            continue
        # The widest *contiguous* run of columns, not min-to-max. A block is a
        # solid bar of colour; min-to-max also swallows anything else team-hued
        # that happens to share these rows. In the in-game Tab sample that is
        # Junker Queen's blue hair in the hero panel 140px to the right, which
        # stretched the blue block 467 canonical units past its real edge and
        # made the board look misaligned. Every endgame sample has exactly one
        # run, so this changes nothing there.
        spans = _contiguous_runs(columns, tolerance=TEAM_COLUMN_GAP)
        x0, x1 = max(spans, key=lambda run: run[1] - run[0])
        found.append((team, Box(int(x0), int(y0), int(x1), int(y1))))
    return found


def _find_columns(image, header: Box, scale: float) -> tuple[dict[str, float] | None, list[str]]:
    """Column x-centres, derived from the labels printed on the strip."""
    warnings: list[str] = []
    strip = image[header.y0:header.y1 + 1, header.x0:header.x1 + 1]
    grey = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
    ink = np.flatnonzero((grey < LABEL_DARK_MAX).any(axis=0))
    if ink.size == 0:
        return None, ["The header strip carries no text."]

    groups = [run for run in _contiguous_runs(ink, tolerance=LABEL_GROUP_GAP)
              # The strip's rounded corners leave a few dark pixels hard against
              # each edge. A real label never touches the boundary.
              if run[0] > 0 and run[1] < header.width - 1
              and run[1] - run[0] + 1 >= LABEL_MIN_WIDTH]

    if len(groups) != len(COLUMN_LABELS):
        return None, [f"Found {len(groups)} header labels, expected {len(COLUMN_LABELS)}."]

    widths = [run[1] - run[0] + 1 for run in groups]
    narrow = max(widths[i] for i in range(len(widths)) if i not in WIDE_COLUMNS)
    if not all(widths[i] > narrow for i in WIDE_COLUMNS):
        # DMG and MIT are three glyphs; E, A, D and H are one. If that pattern
        # is absent, whatever was measured is not the header row.
        return None, [f"Header label widths {widths} do not match E A D DMG H MIT."]

    centres = {name: (run[0] + run[1]) / 2 * scale
               for name, run in zip(COLUMN_NAMES, groups)}
    return centres, warnings


def column_drift(columns: dict[str, float]) -> dict[str, float]:
    """How far each column sits from the endgame signature, in canonical units."""
    return {name: columns[name] - expected
            for name, expected in zip(COLUMN_NAMES, EXPECTED_COLUMN_X)}


def _find_vs_divider(image, top: Box, bottom: Box, board: Box, scale: float) -> float | None:
    """The bright horizontal rule in the gap between the blocks.

    Used as a confirmation that the gap really is the VS divider and not, say,
    two unrelated coloured panels. The transform does not depend on it.
    """
    y0, y1 = top.y1 + 1, bottom.y0
    if y1 - y0 < 3:
        return None
    gap = cv2.cvtColor(image[y0:y1, board.x0:board.x1 + 1], cv2.COLOR_BGR2GRAY).astype(float)
    profile = gap.mean(axis=1)
    floor = float(np.median(profile))
    peak = int(np.argmax(profile))
    if profile[peak] - floor < 8.0:
        return None
    return (y0 + peak - board.y0) * scale


# -- rows --------------------------------------------------------------------


def _find_row_edges(image, block: Box, board: Box, scale: float,
                    probe_band: tuple[float, float] = ROW_PROBE_BAND
                    ) -> tuple[list[float], list[str]]:
    """Row boundaries within one block, as block-relative canonical y.

    Row heights are not uniform — a title line under a player's name makes that
    row taller — so this detects every separator rather than dividing the block
    by a row count.

    `probe_band` is the glyph-free vertical strip to read the luminance profile
    down. It is a parameter because the in-game Tab board packs its columns
    differently, so the band that is empty there is not the one that is empty
    here; see `app/extract/tab.py`.
    """
    warnings: list[str] = []
    height = block.height * scale
    x0 = board.x0 + int(probe_band[0] / scale)
    x1 = board.x0 + int(probe_band[1] / scale)
    x0, x1 = max(board.x0, x0), min(board.x1 + 1, x1)
    if x1 - x0 < 2:
        return [0.0, height], ["probe band fell outside the board."]

    band = cv2.cvtColor(image[block.y0:block.y1 + 1, x0:x1], cv2.COLOR_BGR2GRAY)
    profile = _to_canonical_profile(band.astype(float).mean(axis=1), scale)
    ranges = _sliding_range(profile, int(round(ROW_EDGE_WINDOW)))
    if ranges is None:
        return [0.0, height], ["block is too short to segment into rows."]

    # Each run of elevated range is one separator; the transition sits at its
    # centre for a dip and a step alike.
    edges = [(run[0] + run[1]) / 2
             for run in _contiguous_runs(np.flatnonzero(ranges > ROW_EDGE_MIN_RANGE))]

    boundaries = [0.0]
    for edge in sorted(edges):
        # A run hard against the block's own top or bottom is the block
        # boundary the colour mask already gave us, not a separator.
        if edge < ROW_MIN_HEIGHT or height - edge < ROW_MIN_HEIGHT:
            continue
        if edge - boundaries[-1] < ROW_MIN_HEIGHT:
            warnings.append(f"discarded a separator {edge - boundaries[-1]:.0f} units "
                            f"below the previous one — too close to be a row.")
            continue
        boundaries.append(edge)
    boundaries.append(height)
    return boundaries, warnings


def _to_canonical_profile(profile: np.ndarray, scale: float) -> np.ndarray:
    """Resample a per-source-row profile so one sample is one canonical unit.

    Doing this before detection is what lets the row thresholds be plain
    constants: after it, a separator is the same number of samples wide in a
    720p shot and a 4K one.
    """
    length = max(2, int(round(profile.size * scale)))
    return np.interp(np.arange(length), np.arange(profile.size) * scale, profile)


def _sliding_range(profile: np.ndarray, window: int) -> np.ndarray | None:
    """Peak-to-trough spread of `profile` within a centred sliding window."""
    if profile.size <= window or window < 2:
        return None
    pad = window // 2
    padded = np.pad(profile, (pad, window - pad - 1), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, window)
    return windows.max(axis=1) - windows.min(axis=1)


# -- helpers -----------------------------------------------------------------


def _contiguous_runs(indices: np.ndarray, tolerance: int = 1) -> list[tuple[int, int]]:
    """Split a sorted index array into (start, end) runs, inclusive.

    `tolerance` is the largest gap that still counts as the same run.
    """
    if indices.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(indices) > tolerance)
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [indices.size - 1]))
    return [(int(indices[s]), int(indices[e])) for s, e in zip(starts, ends)]
