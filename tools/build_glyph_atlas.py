"""Cut the digit atlas out of the sample corpus.

    .venv/Scripts/python.exe tools/build_glyph_atlas.py [--write] [--verify]

Labels come from the hand-written `samples/*.expected.json` files, not from the
operator: for a cell whose true value is 16,095, the six glyphs left to right
*are* '1','6',',','0','9','5'. That makes the atlas a product of the ground
truth rather than a second, parallel source of it — and any cell whose glyph
count disagrees with its expected text is skipped rather than force-fitted,
because a misaligned pairing would poison a template silently.

Templates are greyscale, not binary. The antialiasing on a stroke edge is real
signal for cross-correlation, and throwing it away costs accuracy on the pairs
of digits that look alike at this size.

Rebuild only when a game patch changes the scoreboard font.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import paths                                          # noqa: E402
from app.extract import cells, glyphs as glyph_module          # noqa: E402
from app.extract.local import LocalExtractor                   # noqa: E402
from app.extract.localize import COLUMN_NAMES, localize        # noqa: E402

MIN_INSTANCES = 3       # below this a template is one screenshot's opinion


def atlas_sources() -> set[str]:
    """Images the manifest marks as atlas sources.

    Opt-in, so a newly transcribed sample stays held out until someone
    deliberately promotes it. Silently training on the validation set is the
    easiest way to make an accuracy number that means nothing.
    """
    manifest_path = paths.SAMPLES_DIR / "manifest.json"
    if not manifest_path.exists():
        return set()
    manifest = json.loads(manifest_path.read_text("utf-8"))
    return {entry["file"] for entry in manifest["images"] if entry.get("atlas_source")}


def collect(verbose: bool = True) -> tuple[dict[str, list[np.ndarray]], dict]:
    """Every labelled glyph instance from the designated atlas sources."""
    instances: dict[str, list[np.ndarray]] = defaultdict(list)
    report = {"sources": [], "cells_used": 0, "cells_skipped": 0, "skips": []}
    sources = atlas_sources()
    if not sources:
        print("  ! no image in samples/manifest.json has atlas_source: true")

    for expected_path in sorted(paths.SAMPLES_DIR.glob("*.expected.json")):
        expected = json.loads(expected_path.read_text("utf-8"))
        if expected["source"] not in sources:
            if verbose:
                print(f"  {expected['source']}: held out (not an atlas source)")
            continue
        image_path = paths.SAMPLES_DIR / expected["source"]
        image = LocalExtractor._load(str(image_path))
        if image is None:
            print(f"  ! could not decode {expected['source']}")
            continue

        result = localize(image)
        if not result.ok:
            print(f"  ! {expected['source']}: {result.reason}")
            continue

        layout = result.layout
        used = 0
        for block in layout.teams:
            truth = expected["teams"].get(block.team)
            if truth is None or len(truth) != block.row_count:
                report["skips"].append(
                    f"{expected['source']}: {block.team} has {block.row_count} rows, "
                    f"ground truth has {len(truth) if truth else 0}")
                continue
            for row in range(block.row_count):
                for column in COLUMN_NAMES:
                    value = truth[row].get(column)
                    if value is None:
                        continue
                    text = f"{value:,}"
                    cell = cells.cell_image(image, layout, block, row, column)
                    mask = cells.glyph_mask(cell)
                    found = cells.segment(mask)
                    if len(found) != len(text):
                        report["cells_skipped"] += 1
                        report["skips"].append(
                            f"{expected['source']} {block.team} r{row} {column}: "
                            f"expected {text!r} ({len(text)} glyphs), segmented {len(found)}")
                        continue
                    normalized = _normalize(cell)
                    for character, glyph in zip(text, found):
                        instances[character].append(_cut(normalized, glyph))
                    used += 1
        report["cells_used"] += used
        report["sources"].append({"file": expected["source"], "cells": used})
        if verbose:
            print(f"  {expected['source']}: {used} cells used")

    return instances, report


def _normalize(cell: np.ndarray) -> np.ndarray:
    """Cell as float 0-1 with the background at 0 and the glyph peak at 1.

    Done per cell because the background is a different colour in every block
    and every row, and because a zero is rendered dimmed rather than white.
    """
    grey = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY).astype(np.float32)
    floor, ceiling = float(np.percentile(grey, 5)), float(grey.max())
    if ceiling - floor < 1e-6:
        return np.zeros_like(grey)
    return np.clip((grey - floor) / (ceiling - floor), 0.0, 1.0)


def _cut(normalized: np.ndarray, glyph: cells.Glyph) -> np.ndarray:
    x0 = int(round(glyph.x0))
    y0 = int(round(glyph.y0))
    return normalized[y0:y0 + glyph.height, x0:x0 + glyph.width]


def build(instances: dict[str, list[np.ndarray]]) -> tuple[dict[str, np.ndarray], dict]:
    """Average each character's instances at its median size."""
    templates: dict[str, np.ndarray] = {}
    stats: dict[str, dict] = {}
    for character, samples in sorted(instances.items()):
        height = int(np.median([s.shape[0] for s in samples]))
        width = int(np.median([s.shape[1] for s in samples]))
        stack = [cv2.resize(s, (width, height), interpolation=cv2.INTER_AREA)
                 for s in samples]
        templates[character] = np.clip(np.mean(stack, axis=0), 0.0, 1.0)
        # Spread across instances: a high value means the samples disagree,
        # which usually means a mislabelled pairing slipped through.
        stats[character] = {
            "instances": len(samples),
            "size": [width, height],
            "width_range": [int(min(s.shape[1] for s in samples)),
                            int(max(s.shape[1] for s in samples))],
            "mean_abs_deviation": round(float(np.mean(np.abs(np.array(stack) -
                                                            templates[character]))), 4),
        }
    return templates, stats


def write(templates: dict[str, np.ndarray], stats: dict, report: dict) -> None:
    paths.GLYPHS_DIR.mkdir(parents=True, exist_ok=True)
    for character, template in templates.items():
        name = glyph_module.FILENAMES[character]
        image = np.round(template * 255).astype(np.uint8)
        ok, buffer = cv2.imencode(".png", image)
        if not ok:
            raise ValueError(f"could not encode template for {character!r}")
        (paths.GLYPHS_DIR / f"{name}.png").write_bytes(buffer.tobytes())

    index = {
        "_doc": ("Digit atlas for the endgame scoreboard font, in canonical units "
                 "(the board normalized to 1920 wide). Built by "
                 "tools/build_glyph_atlas.py from samples/*.expected.json. "
                 "Rebuild only if a game patch changes the scoreboard font."),
        "built_from": [source["file"] for source in report["sources"]],
        "cells_used": report["cells_used"],
        "cells_skipped": report["cells_skipped"],
        "characters": stats,
    }
    (paths.GLYPHS_DIR / glyph_module.ATLAS_INDEX).write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8")


def verify(instances: dict[str, list[np.ndarray]]) -> int:
    """Match every collected instance back against the atlas just written.

    This is a floor, not an accuracy score — these are the glyphs the templates
    were averaged from, so anything less than near-perfect here means the atlas
    is internally inconsistent and nothing downstream is worth measuring.
    """
    atlas = glyph_module.load_atlas()
    if not atlas.ready:
        print("  atlas is missing digits; nothing to verify")
        return 1

    wrong = unconfident = total = 0
    worst: list[tuple[float, str, str]] = []
    for character, samples in sorted(instances.items()):
        if character not in glyph_module.DIGITS:
            # The reader identifies separators by size and never matches them
            # against a template, so scoring them here would report a failure
            # in a path that does not exist. See app/extract/reader.py.
            continue
        for sample in samples:
            total += 1
            result = glyph_module.match(sample, atlas, only=glyph_module.DIGITS)
            if result.character != character:
                wrong += 1
                worst.append((result.score, character, result.character or "none"))
            elif not result.confident:
                unconfident += 1
                worst.append((result.score, character, f"{result.character} (low)"))

    print(f"\n  round trip: {total - wrong - unconfident}/{total} confident and correct")
    if wrong:
        print(f"  WRONG: {wrong}")
    if unconfident:
        print(f"  low confidence: {unconfident}")
    for score, want, got in sorted(worst)[:10]:
        print(f"    {want!r} -> {got!r} at {score:.3f}")
    return 1 if wrong else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="write seed/glyphs/ (otherwise this is a dry run)")
    parser.add_argument("--verify", action="store_true",
                        help="match every collected instance back against the atlas")
    args = parser.parse_args(argv)

    print("Collecting labelled glyphs from samples/*.expected.json")
    instances, report = collect()
    if not instances:
        print("No labelled glyphs found. Write a samples/<name>.expected.json first.")
        return 1

    templates, stats = build(instances)
    print(f"\n  {report['cells_used']} cells used, {report['cells_skipped']} skipped")
    print(f"  {sum(len(v) for v in instances.values())} glyph instances, "
          f"{len(templates)} distinct characters\n")
    print(f"  {'char':<6}{'count':>7}{'size':>12}{'w range':>12}{'deviation':>12}")
    thin = []
    for character, stat in stats.items():
        print(f"  {character!r:<6}{stat['instances']:>7}"
              f"{str(tuple(stat['size'])):>12}{str(tuple(stat['width_range'])):>12}"
              f"{stat['mean_abs_deviation']:>12.4f}")
        if stat["instances"] < MIN_INSTANCES:
            thin.append(character)

    missing = [str(d) for d in range(10) if str(d) not in templates]
    if missing:
        print(f"\n  MISSING digits: {', '.join(missing)} — add a sample that contains them")
    if thin:
        print(f"  thin coverage (<{MIN_INSTANCES} instances): {', '.join(map(repr, thin))}")
    for skip in report["skips"][:10]:
        print(f"  skipped: {skip}")

    if args.write:
        write(templates, stats, report)
        glyph_module.cached_atlas.cache_clear()
        print(f"\n  wrote {len(templates)} templates to {paths.GLYPHS_DIR}")
    else:
        print("\n  dry run — pass --write to save")

    if args.verify:
        if not args.write:
            print("  --verify needs --write (it matches against the atlas on disk)")
            return 1
        return verify(instances)
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
