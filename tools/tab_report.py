"""Run the Tab localizer over samples and write overlays to data/overlays/.

    .venv/Scripts/python.exe tools/tab_report.py [path ...]

With no arguments it runs every sample the manifest declares as an in-game
scoreboard. Prints the chrome geometry in canonical units so a second sample can
be compared against the first as numbers, and writes one PNG per sample for the
eyeball check — the chrome constants are fitted to a single screenshot, so
looking at the boxes is not optional.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import paths                                    # noqa: E402
from app.extract.local import LocalExtractor             # noqa: E402
from app.extract.overlay import render_tab               # noqa: E402
from app.extract.tab import localize_tab                 # noqa: E402

OUT_DIR = paths.DATA_DIR / "overlays"


def tab_samples() -> list[Path]:
    manifest = paths.SAMPLES_DIR / "manifest.json"
    if not manifest.exists():
        return []
    entries = json.loads(manifest.read_text(encoding="utf-8"))["images"]
    return [paths.SAMPLES_DIR / e["file"] for e in entries
            if e.get("kind") == "in_game_scoreboard"
            and (paths.SAMPLES_DIR / e["file"]).exists()]


def _box(layout, box) -> str:
    x0, y0, x1, y1 = layout.canonical_box(box)
    return f"x {x0:7.0f}..{x1:7.0f}  y {y0:6.0f}..{y1:6.0f}  ({x1-x0:.0f}x{y1-y0:.0f})"


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv] or tab_samples()
    if not targets:
        print("No in-game scoreboard samples declared in the manifest.")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0

    for path in targets:
        image = LocalExtractor._load(str(path))
        if image is None:
            print(f"{path.name}: could not decode")
            failures += 1
            continue

        result = localize_tab(image)
        (OUT_DIR / f"{path.stem}.tab.png").write_bytes(render_tab(image, result))

        print(f"\n{path.name}  {image.shape[1]}x{image.shape[0]}")
        if not result.ok:
            print(f"  FAILED: {result.reason}")
            failures += 1
        else:
            layout = result.layout
            print(f"  board   {layout.board.width}x{layout.board.height} px at "
                  f"({layout.board.x0},{layout.board.y0})   scale {layout.scale:.4f}")
            print(f"  screen  {layout.canonical_box(layout.screen)[2]:.0f} canonical units wide")
            print("  columns " + "  ".join(f"{k[:3]}={v:.0f}" for k, v in layout.columns.items()))
            for block in layout.teams:
                print(f"  {block.team:<5} {block.row_count} rows  "
                      f"top={'y' if block.is_top else 'n'}")
            chrome = layout.chrome
            for index, ban in enumerate(chrome.bans):
                print(f"  ban {index + 1}   {_box(layout, ban)}")
            for name, box in (("banicon", chrome.ban_icon), ("modeico", chrome.mode_icon),
                              ("modemap", chrome.mode_map), ("clock  ", chrome.clock)):
                if box is not None:
                    print(f"  {name} {_box(layout, box)}")
            for index, badge in enumerate(chrome.rank_badges):
                print(f"  rank {index + 1}  {_box(layout, badge)}")
        for warning in result.warnings:
            print(f"  ! {warning}")

    print(f"\nOverlays in {OUT_DIR}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
