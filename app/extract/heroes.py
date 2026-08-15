"""Stage 6: identify a hero from its portrait.

Portraits are genuinely static — the scoreboard shows default hero art, never an
equipped skin — so an ordinary perceptual hash is the right tool.

The crop is deliberately tight to the portrait interior. The row background is a
flat, strongly team-coloured field, and a crop that includes much of it hashes
the *team* rather than the *hero*: every blue-side portrait would drift toward
every other blue-side portrait.

The library comes from two places, and `merged_library` is what the extractor
should actually be handed:

- **Shipped.** `seed/hero_hashes.json`, built by `tools/build_hero_hashes.py`
  from portraits cut out of the sample corpus and confirmed by the operator.
  There is no source of official hero art here — the project is offline by
  decision — so this covers only what the corpus contained: ten heroes.
- **Learned.** `hero_portraits`, written by the commit path every time a hero is
  confirmed in the review grid, exactly as `player_nameplates` is written every
  time a name is. This is the half that grows, and the reason the hero column
  stops needing to be typed after a handful of matches.

The shipped half was long the only half, which is why the docstring here used to
claim the library does not grow with use. It does now, and the same asymmetry
governs both halves: a miss costs one dropdown, a wrong answer silently corrupts
every hero win rate and every comp and nothing downstream would reveal it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .. import paths
from . import cells
from .imagehash import HASH_BITS, confidence_from_distance, hamming, phash

__all__ = ["HeroMatch", "cached_library", "claimant", "hamming", "identify",
           "identify_portrait", "learned_counts", "load_hero_library",
           "load_learned", "merged_library", "portrait_signature"]

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
    """The *shipped* library only. Safe to cache because the seed file is a
    build artifact that cannot change while the app is running; the learned
    half is read fresh from the database on every extraction, because it
    changes every time a match is committed."""
    return load_hero_library()


# --------------------------------------------------------------------------
# The learned half
# --------------------------------------------------------------------------

def load_learned(conn) -> dict[str, list[str]]:
    """Hashes taught by confirming a hero in the review grid.

    Keyed by hero *name*, not id, because that is what `identify` matches
    against and what the seed file holds. The route turns the answer back into
    an id, the same way it already does for the shipped library.
    """
    library: dict[str, list[str]] = {}
    for row in conn.execute(
            "SELECT h.name AS name, p.phash AS phash "
            "FROM hero_portraits p JOIN heroes h ON h.id = p.hero_id"):
        library.setdefault(row["name"], []).append(row["phash"])
    return library


def merged_library(conn) -> dict[str, list[str]]:
    """What the extractor should match against: shipped plus learned.

    Merged rather than either-or. The shipped hashes were confirmed against the
    corpus and are worth keeping even for a hero the operator has also taught,
    and a hero present in both simply ends up with more angles on it.
    """
    library = {name: list(hashes) for name, hashes in load_hero_library().items()}
    for name, hashes in load_learned(conn).items():
        known = library.setdefault(name, [])
        known.extend(value for value in hashes if value not in known)
    return library


def claimant(conn, phash: str) -> int | None:
    """Which hero, if any, already claims this exact hash.

    Teaching one hash to two heroes makes every later identification between
    them a coin flip — `identify`'s margin rule would return unknown for both,
    which is safe but means the operator has silently made the matcher worse at
    two heroes at once. The commit path checks this before writing.
    """
    row = conn.execute(
        "SELECT hero_id FROM hero_portraits WHERE phash = ? LIMIT 1", (phash,)
    ).fetchone()
    return int(row["hero_id"]) if row else None


def learned_counts(conn) -> list[dict]:
    """Per-hero summary of what this database has been taught, for the
    settings page — the operator's only window onto a library that is otherwise
    invisible and now writable."""
    return [
        {"hero_id": row["hero_id"], "hero_name": row["hero_name"],
         "role": row["role"], "portraits": row["portraits"],
         "times_matched": row["times_matched"], "first_seen": row["first_seen"]}
        for row in conn.execute(
            """
            SELECT h.id AS hero_id, h.name AS hero_name, h.role AS role,
                   COUNT(*) AS portraits,
                   SUM(p.times_matched) AS times_matched,
                   MIN(p.first_seen) AS first_seen
            FROM hero_portraits p JOIN heroes h ON h.id = p.hero_id
            GROUP BY h.id ORDER BY h.name
            """)
    ]


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
