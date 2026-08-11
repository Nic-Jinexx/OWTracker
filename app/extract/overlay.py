"""Debug overlay: draw what the localizer thinks it found, over the source.

The milestone-2 hard stop is "the overlay is correct across several
resolutions", and the only way to judge that is to look at it. This renders
boxes, column centre lines and row separators onto a copy of the screenshot and
hands back PNG bytes.

It also renders *failures* — whatever partial geometry survived in the
diagnostics is drawn in red with the reason burned into the corner, because a
blank error page tells you nothing about why localization gave up.
"""

from __future__ import annotations

import cv2
import numpy as np

from . import cells
from .localize import LocalizeResult, localize

# BGR. Deliberately garish; this is a diagnostic, not the product.
BOARD = (0, 255, 255)
HEADER = (255, 255, 0)
COLUMN = (0, 255, 0)
ROW = (255, 128, 255)
DIVIDER = (0, 165, 255)
TEAM = {"blue": (255, 160, 0), "red": (60, 60, 255)}
PORTRAIT = (180, 255, 120)
NAMEPLATE = (255, 220, 120)
FAILURE = (0, 0, 255)
BAN = (80, 80, 255)
MODE = (255, 255, 255)
CLOCK = (0, 200, 255)
RANK = (255, 100, 200)

FONT = cv2.FONT_HERSHEY_SIMPLEX


def render(image: np.ndarray, result: LocalizeResult | None = None) -> bytes:
    """Overlay a localization result on the image and encode it as PNG."""
    if result is None:
        result = localize(image)

    canvas = image.copy()
    if result.layout is None:
        _draw_failure(canvas, result)
    else:
        _draw_layout(canvas, result)

    ok, buffer = cv2.imencode(".png", canvas)
    if not ok:  # pragma: no cover - imencode only fails on an invalid array
        raise ValueError("Could not encode the overlay.")
    return buffer.tobytes()


def _draw_layout(canvas: np.ndarray, result: LocalizeResult) -> None:
    layout = result.layout
    scale_hint = max(0.4, min(1.0, layout.board.width / 1400))

    cv2.rectangle(canvas, (layout.board.x0, layout.board.y0),
                  (layout.board.x1, layout.board.y1), BOARD, 2)
    cv2.rectangle(canvas, (layout.header.x0, layout.header.y0),
                  (layout.header.x1, layout.header.y1), HEADER, 2)

    for name, centre in layout.columns.items():
        x = int(round(layout.to_source(centre, 0)[0]))
        cv2.line(canvas, (x, layout.header.y0), (x, layout.board.y1), COLUMN, 1)
        _label(canvas, name[:3].upper(), (x + 3, layout.header.y0 - 4), COLUMN, scale_hint)

    if layout.vs_divider_y is not None:
        y = int(round(layout.to_source(0, layout.vs_divider_y)[1]))
        cv2.line(canvas, (layout.board.x0, y), (layout.board.x1, y), DIVIDER, 2)
        _label(canvas, "VS", (layout.board.x0 + 6, y - 6), DIVIDER, scale_hint)

    for block in layout.teams:
        colour = TEAM[block.team]
        cv2.rectangle(canvas, (block.box.x0, block.box.y0),
                      (block.box.x1, block.box.y1), colour, 2)
        _label(canvas, f"{block.team} x{block.row_count}",
               (block.box.x0 + 6, block.box.y0 + 20), colour, scale_hint)
        for index in range(block.row_count):
            top, bottom = layout.row_bounds(block, index)
            y = int(round(layout.to_source(0, bottom)[1]))
            if index < block.row_count - 1:
                cv2.line(canvas, (block.box.x0, y), (block.box.x1, y), ROW, 1)
            y_top = int(round(layout.to_source(0, top)[1]))
            _label(canvas, str(index + 1), (block.box.x0 + 4, y_top + 16), ROW, scale_hint * 0.8)
            # The two crops the hero and nameplate matchers actually see. Both
            # are fixed rectangles anchored to the row top, so drawing them is
            # the only way to notice a row where they have drifted off target.
            for bounds, colour in ((cells.PORTRAIT_BOUNDS, PORTRAIT),
                                   (cells.NAMEPLATE_BOUNDS, NAMEPLATE)):
                x0, dy0, x1, dy1 = bounds
                a = layout.to_source(x0, top + dy0)
                b = layout.to_source(x1, top + dy1)
                cv2.rectangle(canvas, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])),
                              colour, 1)

    lines = [f"scale {layout.scale:.3f}  board {layout.board.width}x{layout.board.height}px"]
    lines += [f"! {warning}" for warning in result.warnings]
    _caption(canvas, lines, (255, 255, 255))


def render_tab(image: np.ndarray, result=None) -> bytes:
    """Overlay a Tab localization on the image and encode it as PNG.

    Separate from `render` because the endgame drawing puts the portrait and
    nameplate crop rectangles on every row, and those bounds are fitted to the
    endgame board. Drawn over a Tab board they would land on the wrong columns
    and look like a localization bug rather than a reused constant.
    """
    from .tab import localize_tab

    if result is None:
        result = localize_tab(image)

    canvas = image.copy()
    if result.layout is None:
        _draw_failure(canvas, result)
    else:
        _draw_tab(canvas, result)

    ok, buffer = cv2.imencode(".png", canvas)
    if not ok:  # pragma: no cover - imencode only fails on an invalid array
        raise ValueError("Could not encode the overlay.")
    return buffer.tobytes()


def _draw_tab(canvas: np.ndarray, result) -> None:
    layout = result.layout
    scale_hint = max(0.4, min(1.0, layout.board.width / 1400))

    cv2.rectangle(canvas, (layout.board.x0, layout.board.y0),
                  (layout.board.x1, layout.board.y1), BOARD, 2)
    cv2.rectangle(canvas, (layout.header.x0, layout.header.y0),
                  (layout.header.x1, layout.header.y1), HEADER, 2)
    for name, centre in layout.columns.items():
        x = int(round(layout.to_source(centre, 0)[0]))
        cv2.line(canvas, (x, layout.header.y0), (x, layout.board.y1), COLUMN, 1)
        _label(canvas, name[:3].upper(), (x + 3, layout.header.y0 - 4), COLUMN, scale_hint)

    for block in layout.teams:
        colour = TEAM[block.team]
        cv2.rectangle(canvas, (block.box.x0, block.box.y0),
                      (block.box.x1, block.box.y1), colour, 2)
        _label(canvas, f"{block.team} x{block.row_count}",
               (block.box.x0 + 6, block.box.y0 + 20), colour, scale_hint)
        for index in range(block.row_count - 1):
            y = int(round(layout.to_source(0, layout.row_bounds(block, index)[1])[1]))
            cv2.line(canvas, (block.box.x0, y), (block.box.x1, y), ROW, 1)

    chrome = layout.chrome
    boxes = [(f"BAN {i + 1}", box, BAN) for i, box in enumerate(chrome.bans)]
    boxes += [(f"RANK {i + 1}", box, RANK) for i, box in enumerate(chrome.rank_badges)]
    boxes += [(name, box, colour) for name, box, colour in
              (("BANS", chrome.ban_icon, BAN), ("MODE", chrome.mode_icon, MODE),
               ("MODE / MAP", chrome.mode_map, MODE), ("CLOCK", chrome.clock, CLOCK))
              if box is not None]
    for name, box, colour in boxes:
        cv2.rectangle(canvas, (box.x0, box.y0), (box.x1, box.y1), colour, 2)
        _label(canvas, name, (box.x0, box.y0 - 5), colour, scale_hint)

    lines = [f"TAB  scale {layout.scale:.3f}  board {layout.board.width}x"
             f"{layout.board.height}px  bans {len(chrome.bans)}  "
             f"rank {'yes' if chrome.rank_range_found else 'no'}"]
    lines += [f"! {warning}" for warning in result.warnings]
    _caption(canvas, lines, (255, 255, 255))


def _draw_failure(canvas: np.ndarray, result: LocalizeResult) -> None:
    """Draw whatever geometry the localizer got to before it gave up."""
    for key, colour in (("header", HEADER), ("board", BOARD)):
        box = result.diagnostics.get(key)
        if box:
            cv2.rectangle(canvas, (box["x0"], box["y0"]), (box["x1"], box["y1"]), colour, 2)
    for team, box in (result.diagnostics.get("blocks") or {}).items():
        cv2.rectangle(canvas, (box["x0"], box["y0"]), (box["x1"], box["y1"]),
                      TEAM.get(team, FAILURE), 2)
    _caption(canvas, ["LOCALIZATION FAILED", result.reason or "unknown"] +
             [f"! {warning}" for warning in result.warnings], FAILURE)


def _label(canvas, text: str, origin, colour, scale: float) -> None:
    cv2.putText(canvas, text, origin, FONT, 0.45 * scale + 0.15, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(canvas, text, origin, FONT, 0.45 * scale + 0.15, colour, 1, cv2.LINE_AA)


def _caption(canvas, lines: list[str], colour) -> None:
    """Bottom-left caption block, on a dimmed strip so it stays readable."""
    height, width = canvas.shape[:2]
    line_height = 20
    top = height - line_height * len(lines) - 12
    if top < 0:
        return
    panel = canvas[top:height, 0:width]
    canvas[top:height, 0:width] = (panel * 0.35).astype(panel.dtype)
    for index, text in enumerate(lines):
        cv2.putText(canvas, text[:150], (10, top + line_height * (index + 1)),
                    FONT, 0.5, colour, 1, cv2.LINE_AA)
