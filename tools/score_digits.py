"""Accuracy harness for the digit reader (CLAUDE-OWTRACKER.md -> Quality Bar).

    .venv/Scripts/python.exe tools/score_digits.py [--holdout]

Reads every stat cell in every sample that has ground truth and reports the
delta. `--holdout` scores only samples the atlas was *not* built from, which is
the number that actually means something — in-sample accuracy is close to a
tautology, since the templates are averages of those very glyphs.

Three outcomes are counted separately and they are not equally bad:

  correct      read, and right.
  unread       refused: at least one glyph scored below threshold. Costs the
               operator a typed number, which is the price of not lying.
  WRONG        read, and wrong. The milestone-4 hard stop forbids these
               outright, so this column must stay at zero.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import paths                                          # noqa: E402
from app.extract import glyphs as glyph_module                 # noqa: E402
from app.extract.local import LocalExtractor                   # noqa: E402
from app.extract.localize import COLUMN_NAMES, localize        # noqa: E402
from app.extract.reader import read_cell                       # noqa: E402


def score(holdout_only: bool) -> int:
    atlas = glyph_module.load_atlas()
    if not atlas.ready:
        print("Atlas is not built. Run tools/build_glyph_atlas.py --write first.")
        return 1
    built_from = set(atlas.built_from)
    print(f"Atlas built from: {', '.join(sorted(built_from)) or 'unknown'}\n")

    totals = Counter()
    failures: list[str] = []

    for expected_path in sorted(paths.SAMPLES_DIR.glob("*.expected.json")):
        expected = json.loads(expected_path.read_text("utf-8"))
        source = expected["source"]
        in_sample = source in built_from
        if holdout_only and in_sample:
            continue

        image = LocalExtractor._load(str(paths.SAMPLES_DIR / source))
        result = localize(image) if image is not None else None
        if result is None or not result.ok:
            print(f"{source}: could not localize")
            totals["localize_failed"] += 1
            continue
        layout = result.layout

        counts = Counter()
        for block in layout.teams:
            truth = expected["teams"].get(block.team) or []
            for row in range(min(block.row_count, len(truth))):
                for column in COLUMN_NAMES:
                    want = truth[row].get(column)
                    if want is None:
                        continue
                    reading = read_cell(image, layout, block, row, column, atlas)
                    counts["total"] += 1
                    if reading.value == want:
                        counts["correct"] += 1
                    elif reading.value is None:
                        counts["unread"] += 1
                        failures.append(f"  unread {source} {block.team} r{row} "
                                        f"{column}: want {want:,} — {reading.problem}")
                    else:
                        counts["wrong"] += 1
                        failures.append(f"  WRONG  {source} {block.team} r{row} "
                                        f"{column}: want {want:,}, got {reading.value:,} "
                                        f"(worst glyph {reading.confidence:.3f})")

        tag = "in-sample" if in_sample else "HELD OUT"
        rate = 100.0 * counts["correct"] / max(1, counts["total"])
        print(f"{source}  [{tag}]")
        print(f"  {counts['correct']}/{counts['total']} correct ({rate:.1f}%)   "
              f"unread {counts['unread']}   WRONG {counts['wrong']}")
        totals.update(counts)

    if not totals["total"]:
        print("No ground truth to score against.")
        return 1

    print(f"\n{'=' * 60}")
    print(f"  cells scored : {totals['total']}")
    print(f"  correct      : {totals['correct']} "
          f"({100.0 * totals['correct'] / totals['total']:.1f}%)")
    print(f"  unread       : {totals['unread']}")
    print(f"  WRONG        : {totals['wrong']}")
    if failures:
        print()
        for line in failures[:25]:
            print(line)
        if len(failures) > 25:
            print(f"  ... and {len(failures) - 25} more")
    return 1 if totals["wrong"] else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", action="store_true",
                        help="score only samples the atlas was not built from")
    return score(parser.parse_args(argv).holdout)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
