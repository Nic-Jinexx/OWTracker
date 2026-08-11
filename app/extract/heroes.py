"""Stage 6: identify a hero from its portrait.

Unlike nameplates, portraits are genuinely static — the scoreboard shows default
hero art, never an equipped skin — so an ordinary perceptual hash is the right
tool and the library does not grow with use. It only changes when a hero is
released or the art is refreshed.

The crop is deliberately tight to the portrait interior. The row background is a
flat, strongly team-coloured field, and a crop that includes much of it hashes
the *team* rather than the *hero*: every blue-side portrait would drift toward
every other blue-side portrait.

The library is seed data, built by `tools/build_hero_hashes.py` from portraits
cut out of the sample corpus and confirmed by the operator. There is no source
of official hero art here — the project is offline by decision — so the library
starts empty and is bootstrapped by labelling. That is the escape hatch the
spec already describes, used as the front door.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .. import paths
from . import cells
from .imagehash import HASH_BITS, confidence_from_distance, hamming, phash

__all__ = ["HeroMatch", "cached_library", "hamming", "identify",
           "identify_portrait", "load_hero_library", "portrait_signature"]

# A hero must beat the runner-up by this many bits. Two heroes that hash within
# a bit or two of each other are a coin flip, and resolving one anyway is the
# confident-wrong-answer the milestone-4 hard stop forbids.
MIN_MARGIN_BITS = 2


@dataclass(frozen=True)
class HeroMatch:
    name: str | None
    distance: int
    confidence: float
    runner_up: str | None = None
    runner_up_distance: int | None = None

    @property
    def identified(self) -> bool:
        return self.name is not None


def library_path():
    return paths.HERO_HASHES_PATH


def load_hero_library() -> dict[str, list[str]]:
    """`{hero name: [hash, ...]}`.

    A list per hero, not a single hash: a hero rendered on the blue side and the
    red side is not quite the same crop, and holding both beats picking one and
    hoping. Also tolerates the older `{name: "hash"}` shape.
    """
    path = library_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    entries = raw.get("heroes", raw) if isinstance(raw, dict) else {}
    library: dict[str, list[str]] = {}
    for name, value in entries.items():
        if isinstance(value, str):
            library[name] = [value]
        elif isinstance(value, list):
            library[name] = [v for v in value if isinstance(v, str)]
    return {k: v for k, v in library.items() if v}


@lru_cache(maxsize=1)
def cached_library() -> dict[str, list[str]]:
    return load_hero_library()


def portrait_signature(crop_bgr: np.ndarray) -> str | None:
    if crop_bgr is None or crop_bgr.size == 0:
        return None
    try:
        return phash(crop_bgr)
    except ValueError:
        return None


def identify(candidate: str | None, library: dict[str, list[str]],
             max_distance: int) -> HeroMatch:
    """Nearest hero, if it is close enough and clearly ahead of the next one."""
    if not candidate or not library:
        return HeroMatch(None, HASH_BITS, 0.0)

    best_name, best = None, HASH_BITS + 1
    second_name, second = None, HASH_BITS + 1
    for name, hashes in library.items():
        gap = min(hamming(candidate, known) for known in hashes)
        if gap < best:
            second_name, second = best_name, best
            best_name, best = name, gap
        elif gap < second:
            second_name, second = name, gap

    if best_name is None or best > max_distance:
        return HeroMatch(None, best, 0.0, best_name, best if best_name else None)
    if second_name is not None and second - best < MIN_MARGIN_BITS:
        # Too close to call. Unknown is a fine answer; wrong is not.
        return HeroMatch(None, best, 0.0, second_name, second)
    return HeroMatch(best_name, best, confidence_from_distance(best, max_distance),
                     second_name, second if second_name else None)


def identify_portrait(image, layout, block, row, library: dict[str, list[str]],
                      max_distance: int) -> HeroMatch:
    crop = cells.portrait_image(image, layout, block, row)
    return identify(portrait_signature(crop), library, max_distance)
