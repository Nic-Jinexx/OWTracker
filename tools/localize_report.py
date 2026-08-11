"""Run the localizer over samples/ and write overlays to data/overlays/.

    .venv/Scripts/python.exe tools/localize_report.py [path ...]

Prints the canonical geometry per sample so drift between shots is visible as
numbers, and writes one PNG per sample for the eyeball check that milestone 2
actually gates on.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json                                            # noqa: E402

from app import paths                                  # noqa: E402
from app.extract.localize import localize              # noqa: E402
from app.extract.local import LocalExtractor           # noqa: E402
from app.extract.overlay import render                 # noqa: E402

OUT_DIR = paths.DATA_DIR / "overlays"


def endgame_samples() -> list[Path]:
    """Endgame reports only. An in-game Tab shot belongs to
    `tools/tab_report.py`; running it through here refuses correctly and then
    counts that refusal as a failure, which is a harness that cries wolf."""
    manifest = paths.SAMPLES_DIR / "manifest.json"
    if not manifest.exists():
        return sorted(paths.SAMPLES_DIR.glob("*.png"))
    entries = json.loads(manifest.read_text(encoding="utf-8"))["images"]
    return [paths.SAMPLES_DIR / e["file"] for e in entries
            if e.get("kind") == "endgame_report"
            and (paths.SAMPLES_DIR / e["file"]).exists()]


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv] or endgame_samples()
    if not targets:
        print(f"No images found in {paths.SAMPLES_DIR}")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0

    for path in targets:
        image = LocalExtractor._load(str(path))
        if image is None:
            print(f"{path.name}: could not decode")
            failures += 1
            continue

        result = localize(image)
        (OUT_DIR / f"{path.stem}.overlay.png").write_bytes(render(image, result))

        print(f"\n{path.name}  {image.shape[1]}x{image.shape[0]}")
        if not result.ok:
            print(f"  FAILED: {result.reason}")
            failures += 1
        else:
            layout = result.layout
            print(f"  board   {layout.board.width}x{layout.board.height} px at "
                  f"({layout.board.x0},{layout.board.y0})   scale {layout.scale:.4f}")
            print("  columns " + "  ".join(f"{k[:3]}={v:.1f}" for k, v in layout.columns.items()))
            for block in layout.teams:
                heights = [round(b - a) for a, b in
                           zip(block.row_edges, block.row_edges[1:])]
                print(f"  {block.team:<5} {block.row_count} rows  "
                      f"top={'y' if block.is_top else 'n'}  heights={heights}")
            if layout.vs_divider_y is not None:
                print(f"  vs      y={layout.vs_divider_y:.1f}")
        for warning in result.warnings:
            print(f"  ! {warning}")

    print(f"\nOverlays in {OUT_DIR}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
