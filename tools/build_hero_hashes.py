"""Build seed/hero_hashes.json by labelling the portraits in samples/.

    .venv/Scripts/python.exe tools/build_hero_hashes.py            # cluster + sheet
    .venv/Scripts/python.exe tools/build_hero_hashes.py --write    # apply labels

There is no source of official hero art here — the project is offline by
decision — so the library is bootstrapped from the portraits that appear in the
sample corpus. This is the escape hatch the spec describes for new heroes, used
as the front door.

Two passes:

1. Without `--write`, every portrait in every sample is cropped, clustered by
   perceptual hash, and written to `data/hero_labels/` as one contact sheet plus
   a `labels.json` holding one entry per cluster. Any cluster whose hero is
   already known is pre-filled; the rest say `null`.
2. The operator names each cluster in `labels.json` and re-runs with `--write`,
   which hashes the confirmed clusters into `seed/hero_hashes.json`.

**A guess is never written.** A wrong label does not fail loudly — it silently
teaches the matcher to call one hero another, forever, and every match after it
inherits the error. Anything not confirmed by a human stays out.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import paths                                          # noqa: E402
from app.extract import cells                                  # noqa: E402
from app.extract.heroes import portrait_signature              # noqa: E402
from app.extract.imagehash import hamming                      # noqa: E402
from app.extract.local import LocalExtractor                   # noqa: E402
from app.extract.localize import localize                      # noqa: E402

OUT_DIR = paths.DATA_DIR / "hero_labels"
LABELS_PATH = OUT_DIR / "labels.json"
SHEET_PATH = OUT_DIR / "portraits.png"

# Two crops of the same hero on opposite team backgrounds sit a few bits apart;
# two different heroes are far further. Clustering at 10 keeps a hero together
# without merging neighbours — and a cluster that wrongly merges two heroes is
# visible on the contact sheet, which is why the sheet exists.
CLUSTER_DISTANCE = 10


def collect() -> list[dict]:
    """Every portrait in the corpus, with its hash and where it came from."""
    found: list[dict] = []
    manifest_path = paths.SAMPLES_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))["images"] \
        if manifest_path.exists() else []
    endgame = {e["file"] for e in manifest
               if e.get("kind") == "endgame_report" and e.get("localizes", True)}

    for path in sorted(paths.SAMPLES_DIR.glob("*.png")):
        if endgame and path.name not in endgame:
            continue
        image = LocalExtractor._load(str(path))
        if image is None:
            continue
        result = localize(image)
        if not result.ok:
            print(f"  ! {path.name}: {result.reason}")
            continue
        for block in result.layout.teams:
            for row in range(block.row_count):
                crop = cells.portrait_image(image, result.layout, block, row)
                signature = portrait_signature(crop)
                if signature:
                    found.append({"sample": path.name, "team": block.team,
                                  "row": row, "hash": signature, "image": crop})
    return found


def cluster(portraits: list[dict]) -> list[dict]:
    """Group portraits that are the same hero."""
    groups: list[dict] = []
    for portrait in portraits:
        for group in groups:
            if hamming(portrait["hash"], group["members"][0]["hash"]) <= CLUSTER_DISTANCE:
                group["members"].append(portrait)
                break
        else:
            groups.append({"members": [portrait]})
    groups.sort(key=lambda g: -len(g["members"]))
    for index, group in enumerate(groups):
        group["id"] = index
    return groups


def contact_sheet(groups: list[dict], path: Path) -> None:
    """One row per cluster, so a merged cluster is obvious at a glance."""
    tile_w, tile_h, pad = 116, 122, 6
    columns = max(len(g["members"]) for g in groups) if groups else 1
    columns = min(columns, 8)
    width = pad + columns * (tile_w + pad) + 190
    height = pad + len(groups) * (tile_h + pad)
    sheet = np.full((height, width, 3), 22, np.uint8)

    for row, group in enumerate(groups):
        y = pad + row * (tile_h + pad)
        for column, member in enumerate(group["members"][:columns]):
            tile = cv2.resize(member["image"], (tile_w, tile_h))
            x = 190 + pad + column * (tile_w + pad)
            sheet[y:y + tile_h, x:x + tile_w] = tile
        cv2.putText(sheet, f"#{group['id']}  x{len(group['members'])}",
                    (8, y + tile_h // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (220, 220, 220), 1, cv2.LINE_AA)
    ok, buffer = cv2.imencode(".png", sheet)
    if ok:
        path.write_bytes(buffer.tobytes())


def known_heroes() -> set[str]:
    data = json.loads((paths.SEED_DIR / "heroes.json").read_text("utf-8"))
    return {entry["name"] for entry in data}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="write seed/hero_hashes.json from the confirmed labels")
    args = parser.parse_args(argv)

    print("Cropping portraits from samples/")
    portraits = collect()
    if not portraits:
        print("No portraits found.")
        return 1
    groups = cluster(portraits)
    print(f"  {len(portraits)} portraits in {len(groups)} clusters")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    contact_sheet(groups, SHEET_PATH)

    # Carry forward the whole previous entry, not just the name. `confirmed_by`
    # is how an unverified guess is told apart from a human decision, and a
    # regeneration that dropped it would quietly launder every guess into a
    # confirmation — which is exactly what this file exists to prevent.
    previous: dict[str, dict] = {}
    if LABELS_PATH.exists():
        try:
            saved = json.loads(LABELS_PATH.read_text("utf-8"))
            previous = {str(e["cluster"]): e for e in saved.get("clusters", [])}
        except (ValueError, OSError):
            previous = {}

    entries = []
    for group in groups:
        was = previous.get(str(group["id"]), {})
        entry = {
            "cluster": group["id"],
            "hero": was.get("hero"),
            "count": len(group["members"]),
            "seen_in": sorted({m["sample"] for m in group["members"]}),
            "hashes": sorted({m["hash"] for m in group["members"]}),
        }
        if was.get("confirmed_by"):
            entry["confirmed_by"] = was["confirmed_by"]
        entries.append(entry)

    LABELS_PATH.write_text(json.dumps({
        "_doc": ("Name each cluster by setting `hero` to a name from "
                 "seed/heroes.json, then re-run with --write. Clusters left null "
                 "are skipped — a guess would poison the library silently. "
                 f"Look at {SHEET_PATH.name} to see what each cluster is."),
        "contact_sheet": SHEET_PATH.name,
        "clusters": entries,
    }, indent=2) + "\n", encoding="utf-8")

    labelled = [e for e in entries if e["hero"]]
    print(f"  {len(labelled)} of {len(entries)} clusters are labelled")
    print(f"  sheet:  {SHEET_PATH}")
    print(f"  labels: {LABELS_PATH}")

    if not args.write:
        print("\n  dry run — name the clusters in labels.json, then pass --write")
        return 0

    valid = known_heroes()
    unknown = sorted({e["hero"] for e in labelled if e["hero"] not in valid})
    if unknown:
        print(f"\n  NOT IN seed/heroes.json: {', '.join(unknown)}")
        return 1
    if not labelled:
        print("\n  nothing labelled yet; nothing written")
        return 1

    library: dict[str, list[str]] = {}
    for entry in labelled:
        library.setdefault(entry["hero"], []).extend(entry["hashes"])
    library = {name: sorted(set(hashes)) for name, hashes in library.items()}

    paths.HERO_HASHES_PATH.write_text(json.dumps({
        "_doc": ("Perceptual hashes of hero portraits, cropped in canonical space "
                 "and confirmed by the operator. Several per hero because the "
                 "blue-side and red-side crops differ slightly. Rebuild with "
                 "tools/build_hero_hashes.py when a hero is added."),
        "heroes": library,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"\n  wrote {len(library)} heroes "
          f"({sum(len(v) for v in library.values())} hashes) to {paths.HERO_HASHES_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
