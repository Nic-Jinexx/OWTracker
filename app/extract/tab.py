"""Stage 1-4 for the in-game Tab scoreboard.

`localize.py` handles the endgame report. This handles the other screenshot the
spec names: the scoreboard the player sees mid-match by holding Tab. The two
share a board — same grey header strip, same six stat labels, same two
team-coloured blocks — so the anchor-finding is reused wholesale. What this
module adds is the *chrome*: the strip of screen above the board that carries
the four things an endgame report cannot tell you.

    hero bans · game mode and map · the match clock · the match rank range

Those four are exactly why a Tab shot is worth ingesting at all. The endgame
report is the better source for every statistic and knows none of these.

**This module locates; it does not read.** Same contract as `localize`: boxes
come out, values do not. A ban portrait box is fed to `heroes.identify`, a rank
badge box is fed to a matcher that does not exist yet, and the map text waits
on either a word reader or the operator typing it. Keeping the split means the
geometry can be checked by eye — `tools/tab_report.py` — before anything
depends on what is inside the boxes.

**Chrome is screen-anchored, the board is not.** The map header sits at the top
right of the *screen*, so its offset from the board changes with aspect ratio;
the sample is 2.5:1 and puts it 2851 canonical units right of the board's left
edge, where a 16:9 shot would put it far closer. Nothing here may therefore key
off a fixed offset from the board. Every element is found structurally — by
colour, by regular spacing, by which side of the screen centre it falls on —
and only *reported* in canonical units. Positions outside the board are normal
and often negative.

**One sample.** `samples/ingame_tab_2511x1006.png` is the entire in-game corpus,
so unlike the endgame thresholds these constants are fitted to a single shot and
cannot yet be said to generalize. They are written as ratios and canonical
units rather than pixels so that a second sample tests them rather than
immediately breaking them, and every chrome element is optional: a missing one
is reported as absent, never guessed at. Quick Play has no bans and no rank
range, so absence is a real answer and not only a failure mode.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from .localize import (
    CANONICAL_WIDTH, COLUMN_NAMES, Box, Layout, TeamBlock,
    _contiguous_runs, _find_columns, _find_header_strip, _find_row_edges,
    _find_team_blocks, _find_vs_divider, column_drift,
)

__all__ = ["TabChrome", "TabLayout", "TabResult", "localize_tab"]

# -- the Tab board -----------------------------------------------------------
# Where the six labels land on a Tab board, measured on the one sample. The
# endgame board puts MIT at 1806; this one packs the columns left to reserve
# the right edge for the per-player mute buttons. Advisory: used to confirm
# this is the Tab layout, never to place a crop.
TAB_COLUMN_X = (917.0, 1030.0, 1142.0, 1321.0, 1526.0, 1732.0)
# Generous next to the endgame check. That one guards a corpus of seven shots
# agreeing within 1.6 units; this one has a single measurement behind it, and a
# tolerance tighter than the evidence would be false precision.
TAB_COLUMN_TOLERANCE = 60.0

# The glyph-free strip to detect row separators down, expressed as a fraction
# of the way from the healing column to the mitigation column. Derived from the
# columns actually found rather than fixed, because that is what makes the same
# rule work on a board whose spacing differs.
ROW_PROBE_SPAN = (0.35, 0.65)

# -- chrome ------------------------------------------------------------------
# Bright, unsaturated ink: the map name, the mode, "MATCH RANK RANGE", "TIME".
INK_MIN_VAL = 190
INK_MAX_SAT = 60
INK_GAP = 24.0                  # canonical units between separate words/groups
INK_MIN_WIDTH = 40.0            # canonical; narrower is a stray highlight

# The banned heroes: square portraits in a red frame, evenly spaced. Detection
# keys on that regularity, not on the count — bans come in different numbers by
# mode and season, and the prohibition icon that precedes them is neither the
# same width nor on the same pitch, so it drops out for free.
BAN_MIN_WIDTH = 90.0            # canonical; measured 139
BAN_MAX_WIDTH = 260.0
BAN_MAX_ASPECT = 1.7            # portraits are square-ish; measured 1.01
BAN_WIDTH_SPREAD = 0.25         # fraction, run to run
BAN_PITCH_SPREAD = 0.20
BAN_MIN_COUNT = 2               # one lone red square proves nothing
# Bans are laid out as a contiguous strip, so the step from one to the next is
# barely more than a portrait wide — measured 83px between 71px portraits, a
# ratio of 1.17. Without this, a pair of unrelated red squares at opposite ends
# of the screen satisfies "equal width, constant pitch" vacuously: two runs
# define exactly one pitch, and one pitch is always consistent with itself.
BAN_MAX_PITCH_RATIO = 1.6

# The clock. "3:46" is rendered in amber and is the only strongly saturated
# warm text in the chrome; the muted skin and costume tones inside the ban
# portraits sit well below this saturation.
CLOCK_HUE = (5, 35)
CLOCK_MIN_SAT = 170
CLOCK_MIN_VAL = 150

# Rank badges: two coloured insignia side by side under the map header. Their
# hue is the rank, so it cannot be thresholded — only "saturated and bright"
# can be. Exactly two, or the pair was not found.
BADGE_MIN_SAT = 90
BADGE_MIN_VAL = 90
BADGE_MIN_WIDTH = 60.0          # canonical; measured 137 and 177
BADGE_GAP = 30.0


@dataclass
class TabChrome:
    """Everything above the board, in source pixels.

    Source pixels rather than canonical units because every one of these is
    destined to be cropped and hashed. `TabLayout.to_canonical` converts when a
    position needs comparing across resolutions.
    """
    bans: list[Box] = field(default_factory=list)
    ban_icon: Box | None = None          # the prohibition glyph before the bans
    mode_map: Box | None = None          # "CONTROL | ANTARCTIC PENINSULA"
    mode_icon: Box | None = None         # the mode glyph left of that text
    clock: Box | None = None             # "3:46"
    rank_badges: list[Box] = field(default_factory=list)   # 0 or exactly 2

    @property
    def rank_range_found(self) -> bool:
        return len(self.rank_badges) == 2


@dataclass
class TabLayout(Layout):
    """A `Layout` plus the chrome. Subclasses so the transform, the column
    dictionary and the row helpers are literally the same code the endgame path
    uses — a Tab board is a scoreboard, it just lives on a busier screen."""
    chrome: TabChrome = field(default_factory=TabChrome)
    screen: Box | None = None            # the full frame, for chrome extent

    def canonical_box(self, box: Box) -> tuple[float, float, float, float]:
        x0, y0 = self.to_canonical(box.x0, box.y0)
        x1, y1 = self.to_canonical(box.x1, box.y1)
        return (x0, y0, x1, y1)


@dataclass
class TabResult:
    ok: bool
    layout: TabLayout | None = None
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)


def localize_tab(image: np.ndarray) -> TabResult:
    """Locate the board and the chrome in an in-game Tab screenshot.

    Never raises, for the same reason `localize` never does: a malformed image
    must produce a visible refusal, not a stack trace.
    """
    try:
        return _localize_tab(image)
    except (cv2.error, ValueError, IndexError) as error:  # pragma: no cover - defensive
        return TabResult(ok=False, reason=f"Tab localization failed: {error}")


def _localize_tab(image: np.ndarray) -> TabResult:
    if image is None or image.ndim != 3 or image.size == 0:
        return TabResult(ok=False, reason="Not a colour image.")

    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(np.int16)
    sat = hsv[:, :, 1].astype(np.int16)
    val = hsv[:, :, 2].astype(np.int16)
    diagnostics: dict = {"image": {"width": width, "height": height}}
    warnings: list[str] = []

    header = _find_header_strip(sat, val)
    if header is None:
        return TabResult(
            ok=False, diagnostics=diagnostics,
            reason="No column-header strip found — this does not look like a Tab scoreboard.")
    diagnostics["header"] = asdict(header)
    scale = CANONICAL_WIDTH / header.width

    # The Tab board occupies a smaller fraction of an ultrawide frame than any
    # endgame report does, and the strip detector needs the strip to span 30%
    # of the image width. The one sample clears that at 39%. Say so, because a
    # 32:9 monitor would not, and the failure would look like "no strip found"
    # rather than "your screen is too wide".
    coverage = header.width / width
    diagnostics["header_coverage"] = round(coverage, 3)
    if coverage < 0.35:
        warnings.append(
            f"The board spans {coverage:.0%} of the frame. The strip detector needs 30%, "
            f"so a wider screen than this would stop finding it at all.")

    columns, column_warnings = _find_columns(image, header, scale)
    warnings.extend(column_warnings)
    if columns is None:
        return TabResult(
            ok=False, diagnostics=diagnostics, warnings=warnings,
            reason="Could not read the six column labels off the header strip.")
    diagnostics["columns"] = columns
    diagnostics["endgame_column_drift"] = {
        k: round(v, 1) for k, v in column_drift(columns).items()}

    tab_drift = {name: columns[name] - expected
                 for name, expected in zip(COLUMN_NAMES, TAB_COLUMN_X)}
    diagnostics["tab_column_drift"] = {k: round(v, 1) for k, v in tab_drift.items()}
    worst = max(tab_drift, key=lambda name: abs(tab_drift[name]))
    if abs(tab_drift[worst]) > TAB_COLUMN_TOLERANCE:
        return TabResult(
            ok=False, diagnostics=diagnostics, warnings=warnings,
            reason=(
                f"Column '{worst}' sits {tab_drift[worst]:+.0f} canonical units from the "
                f"in-game Tab layout. If this is an endgame report, app/extract/localize.py "
                f"is the localizer for it."))

    blocks = _find_team_blocks(hue, sat, val)
    diagnostics["blocks"] = {team: asdict(box) for team, box in blocks}
    # Below the strip, not merely somewhere in the frame. The chrome is full of
    # saturated colour — ban frames, rank badges, the killcam widget — and a
    # "block" found up there would put the board's bottom edge above its top.
    blocks = [(team, box) for team, box in blocks if box.y0 >= header.y1]
    if not blocks:
        return TabResult(
            ok=False, diagnostics=diagnostics, warnings=warnings,
            reason="Found the Tab header strip but neither team block below it.")
    if len(blocks) < 2:
        # Not fatal. A Tab shot is worth ingesting for its chrome even when the
        # roster is unusable, which is the whole reason this module exists.
        warnings.append(
            f"Only the {blocks[0][0]} block was found. The chrome is still readable; "
            f"the roster is not.")

    board = Box(header.x0, header.y0, header.x1, max(box.y1 for _, box in blocks))
    diagnostics["board"] = asdict(board)
    diagnostics["scale"] = scale

    probe = _row_probe_band(columns)
    diagnostics["row_probe_band"] = [round(v, 1) for v in probe]

    teams: list[TeamBlock] = []
    for position, (team, box) in enumerate(sorted(blocks, key=lambda item: item[1].y0)):
        edges, row_warnings = _find_row_edges(image, box, board, scale, probe)
        warnings.extend(f"{team} block: {message}" for message in row_warnings)
        teams.append(TeamBlock(team=team, box=box, is_top=(position == 0), row_edges=edges))
        if box.y1 >= height - 2:
            warnings.append(
                f"The {team} block runs off the bottom of the frame, so it holds fewer "
                f"rows than the team does. Its {len(edges) - 1} rows are the ones captured, "
                f"not the roster.")

    divider = None
    if len(teams) == 2:
        divider = _find_vs_divider(image, teams[0].box, teams[1].box, board, scale)

    chrome = _find_chrome(image, hue, sat, val, header, scale)
    diagnostics["chrome"] = {
        "bans": [asdict(b) for b in chrome.bans],
        "ban_icon": asdict(chrome.ban_icon) if chrome.ban_icon else None,
        "mode_map": asdict(chrome.mode_map) if chrome.mode_map else None,
        "mode_icon": asdict(chrome.mode_icon) if chrome.mode_icon else None,
        "clock": asdict(chrome.clock) if chrome.clock else None,
        "rank_badges": [asdict(b) for b in chrome.rank_badges],
    }
    if chrome.mode_map is None:
        warnings.append(
            "No mode/map header found in the chrome. That is the one element every "
            "in-game shot has, so this may not be a Tab screenshot.")
    if not chrome.bans:
        warnings.append("No ban row found — normal outside Competitive.")
    if not chrome.rank_range_found:
        warnings.append("No rank range found — normal outside Competitive.")

    layout = TabLayout(board=board, header=header, scale=scale, columns=columns,
                       teams=teams, vs_divider_y=divider, chrome=chrome,
                       screen=Box(0, 0, width - 1, height - 1))
    diagnostics["teams"] = [
        {"team": block.team, "is_top": block.is_top, "rows": block.row_count,
         "box": asdict(block.box), "row_edges": [round(e, 1) for e in block.row_edges]}
        for block in teams
    ]
    return TabResult(ok=True, layout=layout, warnings=warnings, diagnostics=diagnostics)


def _row_probe_band(columns: dict[str, float]) -> tuple[float, float]:
    """A glyph-free vertical strip, as canonical x bounds.

    Taken between the healing and mitigation columns because that is the widest
    gap on the board. Derived from the detected centres rather than hardcoded,
    so the same rule lands correctly on either layout's spacing.
    """
    left, right = columns["healing"], columns["mitigation"]
    span = right - left
    return (left + span * ROW_PROBE_SPAN[0], left + span * ROW_PROBE_SPAN[1])


# -- chrome ------------------------------------------------------------------


def _find_chrome(image, hue, sat, val, header: Box, scale: float) -> TabChrome:
    """Everything in the band of screen above the board."""
    chrome = TabChrome()
    top = header.y0
    if top < 8:
        return chrome                    # a tight crop of the board alone

    centre_x = image.shape[1] // 2

    bans, ban_icon = _find_ban_row(hue, sat, val, top, scale)
    chrome.bans, chrome.ban_icon = bans, ban_icon

    ink = (val > INK_MIN_VAL) & (sat < INK_MAX_SAT)
    # The map header is the widest run of bright text on the right half of the
    # chrome. "Right of screen centre" is what separates it from the SCOREBOARD
    # tab and the ban portraits, both of which sit on the left, without
    # depending on any distance from the board.
    header_row = _find_ink_row(ink, 0, top, centre_x, scale)
    if header_row is not None:
        chrome.mode_map, chrome.mode_icon = header_row
        chrome.clock = _find_clock(hue, sat, val, chrome.mode_map, image.shape[1])
        # Start the rank band below the clock as well as the header text. The
        # clock is amber and saturated, so a band that includes its scanlines
        # hands the badge finder a third coloured blob and it merges with the
        # badge beneath it.
        below = max(chrome.mode_map.y1, chrome.clock.y1 if chrome.clock else 0) + 1
        chrome.rank_badges = _find_rank_badges(sat, val, below, top, centre_x, scale)
    return chrome


def _find_ban_row(hue, sat, val, top: int, scale: float) -> tuple[list[Box], Box | None]:
    """Red-framed hero portraits, evenly spaced, above the board.

    The regularity is the whole test. Anything else red up there — a killcam
    marker, a low-health vignette — is neither square nor on a constant pitch.
    """
    red = (sat > 120) & (val > 80) & ((hue < 8) | (hue > 168))
    band = red[:top]
    rows = np.flatnonzero(band.mean(axis=1) > 0.002)
    best: tuple[list[Box], Box | None] = ([], None)

    for y0, y1 in _contiguous_runs(rows, tolerance=3):
        strip_height = (y1 - y0 + 1) * scale
        columns = np.flatnonzero(band[y0:y1 + 1].mean(axis=0) > 0.05)
        if columns.size == 0:
            continue
        runs = [run for run in _contiguous_runs(columns, tolerance=6)
                if BAN_MIN_WIDTH <= (run[1] - run[0] + 1) * scale <= BAN_MAX_WIDTH]
        if len(runs) < BAN_MIN_COUNT:
            continue

        keep = _longest_regular_run(runs)
        if len(keep) < BAN_MIN_COUNT:
            continue
        aspect = strip_height / (((keep[0][1] - keep[0][0] + 1)) * scale)
        if not (1 / BAN_MAX_ASPECT) <= aspect <= BAN_MAX_ASPECT:
            continue
        if len(keep) <= len(best[0]):
            continue

        boxes = [Box(int(a), int(y0), int(b), int(y1)) for a, b in keep]
        # The prohibition glyph immediately left of the first ban, if the gap
        # to it is about one pitch. It marks the row as bans rather than, say,
        # a recently-played list, so it is worth reporting when present.
        icon = None
        pitch = keep[1][0] - keep[0][0]
        earlier = [run for run in _contiguous_runs(columns, tolerance=6)
                   if run[1] < keep[0][0] and keep[0][0] - run[1] < pitch]
        if earlier:
            icon = Box(int(earlier[-1][0]), int(y0), int(earlier[-1][1]), int(y1))
        best = (boxes, icon)
    return best


def _longest_regular_run(runs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The longest stretch of consecutive runs with a steady width and pitch."""
    if len(runs) < 2:
        return []
    best: list[tuple[int, int]] = []
    start = 0
    for index in range(1, len(runs)):
        previous, current = runs[index - 1], runs[index]
        width_a = previous[1] - previous[0] + 1
        width_b = current[1] - current[0] + 1
        pitch = current[0] - previous[0]
        steady = (abs(width_b - width_a) <= BAN_WIDTH_SPREAD * max(width_a, width_b)
                  and pitch <= BAN_MAX_PITCH_RATIO * max(width_a, width_b))
        if steady and start < index - 1:
            first_pitch = runs[start + 1][0] - runs[start][0]
            steady = abs(pitch - first_pitch) <= BAN_PITCH_SPREAD * first_pitch
        if not steady:
            if index - start > len(best):
                best = runs[start:index]
            start = index
    if len(runs) - start > len(best):
        best = runs[start:]
    return best if len(best) >= BAN_MIN_COUNT else []


def _find_ink_row(ink, y0: int, y1: int, centre_x: int,
                  scale: float) -> tuple[Box, Box | None] | None:
    """The widest bright-text group on the right half of a band, and the glyph
    immediately left of it.

    Widest, not topmost. A performance overlay — FPS, temperature, latency —
    sits above the map header on this sample and would win any rule that took
    the first row it met. Nothing else in the chrome is as long as a mode and a
    map name printed side by side, so width is the discriminator that survives
    an overlay being there or not.
    """
    band = ink[y0:y1]
    if band.size == 0:
        return None
    gap = max(2, int(round(INK_GAP / scale)))
    minimum = INK_MIN_WIDTH * 3 / scale     # a mode plus a map name, not a word

    best = None
    rows = np.flatnonzero(band.mean(axis=1) > 0.0008)
    for top, bottom in _contiguous_runs(rows, tolerance=6):
        columns = np.flatnonzero(band[top:bottom + 1].any(axis=0))
        if columns.size == 0:
            continue
        groups = [run for run in _contiguous_runs(columns, tolerance=gap)
                  if (run[0] + run[1]) / 2 > centre_x]
        if not groups:
            continue
        widest = max(groups, key=lambda run: run[1] - run[0])
        if widest[1] - widest[0] + 1 < minimum:
            continue
        if best is None or (widest[1] - widest[0]) > (best[1][1] - best[1][0]):
            best = (top, widest, bottom, columns)
    if best is None:
        return None

    top, widest, bottom, columns = best
    # Tighten vertically to the group itself; the row run spans the whole band
    # including whatever else shares those scanlines.
    filled = np.flatnonzero(band[top:bottom + 1, widest[0]:widest[1] + 1].any(axis=1))
    text = Box(int(widest[0]), int(y0 + top + filled.min()),
               int(widest[1]), int(y0 + top + filled.max()))
    icon = None
    left = [run for run in _contiguous_runs(columns, tolerance=gap)
            if run[1] < widest[0] and (widest[0] - run[1]) * scale < INK_GAP * 3]
    if left:
        icon = Box(int(left[-1][0]), text.y0, int(left[-1][1]), text.y1)
    return (text, icon)


def _find_clock(hue, sat, val, mode_map: Box, width: int) -> Box | None:
    """The amber match clock, beside the mode/map text and right of it.

    Searched over a band one text-height taller than the header text on each
    side, then tightened to its own pixels. The clock is set in a larger face
    than the map name and overhangs it top and bottom, so a band clipped to the
    text's exact scanlines cuts the digits off — and an undersized clock box
    lets the amber leak into whatever is looked for underneath it.
    """
    pad = mode_map.height
    y0 = max(0, mode_map.y0 - pad)
    y1 = min(hue.shape[0], mode_map.y1 + pad + 1)
    warm = ((sat > CLOCK_MIN_SAT) & (val > CLOCK_MIN_VAL)
            & (hue >= CLOCK_HUE[0]) & (hue <= CLOCK_HUE[1]))[y0:y1]
    columns = np.flatnonzero(warm.any(axis=0))
    columns = columns[columns > mode_map.x1]
    if columns.size == 0:
        return None
    runs = _contiguous_runs(columns, tolerance=20)
    x0, x1 = max(runs, key=lambda run: run[1] - run[0])
    rows = np.flatnonzero(warm[:, x0:x1 + 1].any(axis=1))
    return Box(int(x0), int(y0 + rows.min()), int(x1), int(y0 + rows.max()))


def _find_rank_badges(sat, val, y0: int, y1: int, centre_x: int,
                      scale: float) -> list[Box]:
    """The two rank insignia bounding the match rank range.

    Their colour *is* the rank, so only "saturated and bright" can be
    thresholded. Two is the answer or there is no answer: one badge is a
    detection error, and three means something else was caught.
    """
    if y1 - y0 < 4:
        return []
    band = ((sat > BADGE_MIN_SAT) & (val > BADGE_MIN_VAL))[y0:y1]
    columns = np.flatnonzero(band.mean(axis=0) > 0.03)
    columns = columns[columns > centre_x]
    if columns.size == 0:
        return []
    gap = max(2, int(round(BADGE_GAP / scale)))
    runs = [run for run in _contiguous_runs(columns, tolerance=gap)
            if (run[1] - run[0] + 1) * scale >= BADGE_MIN_WIDTH]
    if len(runs) != 2:
        return []
    boxes = []
    for x0, x1 in runs:
        rows = np.flatnonzero(band[:, x0:x1 + 1].any(axis=1))
        boxes.append(Box(int(x0), int(y0 + rows.min()), int(x1), int(y0 + rows.max())))
    return boxes
