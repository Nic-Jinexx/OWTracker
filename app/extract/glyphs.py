"""The digit atlas: load reference bitmaps and match a glyph against them.

Reading a number is template matching, not character recognition — the
scoreboard is a rendered UI with a fixed font, so after scale normalization a
`7` in the DMG column is the same bitmap every time (CLAUDE-OWTRACKER.md →
Extraction). The match score is therefore a real confidence value rather than
an estimate, which is what lets a doubtful cell be emitted null instead of
guessed at.

The atlas is built by `tools/build_glyph_atlas.py` and committed as seed data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from .. import paths

ATLAS_INDEX = "atlas.json"
# On-disk name per character, since '?' and friends are not portable filenames
# and a bare ',' is asking for trouble on Windows.
FILENAMES = {**{str(d): str(d) for d in range(10)}, ",": "comma"}
CHARACTERS = {v: k for k, v in FILENAMES.items()}

DIGITS = "0123456789"
# The comma template is built and shipped because it documents the font and
# because the atlas is cut from labelled text that contains commas — but the
# reader never matches against it. See app/extract/reader.py for why.

# Normalized cross-correlation, so 1.0 is a perfect match and 0.0 is noise.
# Anything under this is reported as unrecognized rather than resolved to the
# nearest character: a confident wrong number is the one outcome the spec's
# milestone-4 hard stop forbids outright.
MIN_SCORE = 0.80
# A glyph that matches its runner-up almost as well as its winner is ambiguous
# even if both score highly, so require the winner to lead by this much.
MIN_MARGIN = 0.03


@dataclass(frozen=True)
class Match:
    character: str | None
    score: float
    runner_up: str | None = None
    runner_up_score: float = 0.0

    @property
    def confident(self) -> bool:
        return (self.character is not None
                and self.score >= MIN_SCORE
                and self.score - self.runner_up_score >= MIN_MARGIN)


@dataclass(frozen=True)
class Atlas:
    templates: dict[str, np.ndarray]     # character -> float32 template, 0-1
    built_from: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.templates)

    @property
    def characters(self) -> list[str]:
        return sorted(self.templates)

    @property
    def ready(self) -> bool:
        """Every digit present. The comma is optional — a scoreboard with no
        four-figure value on it is unusual but not broken."""
        return all(str(d) in self.templates for d in range(10))


def load_atlas(directory=None) -> Atlas:
    """Read the atlas from disk. Returns an empty Atlas if it is not built."""
    directory = directory or paths.GLYPHS_DIR
    if not directory.exists():
        return Atlas(templates={})

    built_from: tuple[str, ...] = ()
    index_path = directory / ATLAS_INDEX
    if index_path.exists():
        try:
            built_from = tuple(json.loads(index_path.read_text("utf-8")).get("built_from", []))
        except (ValueError, OSError):
            built_from = ()

    templates: dict[str, np.ndarray] = {}
    for path in sorted(directory.glob("*.png")):
        character = CHARACTERS.get(path.stem)
        if character is None:
            continue
        image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is not None and image.size:
            templates[character] = image.astype(np.float32) / 255.0
    return Atlas(templates=templates, built_from=built_from)


@lru_cache(maxsize=1)
def cached_atlas() -> Atlas:
    return load_atlas()


def match(glyph: np.ndarray, atlas: Atlas | None = None,
          only: str | None = None) -> Match:
    """Best-matching character for one segmented glyph.

    Each template is resampled to the glyph's own size before correlating.
    Normalizing shape away like this is what makes the score depend on the mark
    itself rather than on how many pixels the screenshot happened to give it.

    `only` restricts the candidate set — the reader passes DIGITS, because
    letting a digit compete against the comma template invents ambiguity where
    the glyph's own size has already settled the question.
    """
    atlas = atlas if atlas is not None else cached_atlas()
    if not atlas or glyph is None or glyph.size == 0:
        return Match(character=None, score=0.0)

    probe = glyph.astype(np.float32)
    if probe.max() > 1.0:
        probe /= 255.0
    probe = _zero_mean(probe)

    scored: list[tuple[float, str]] = []
    for character, template in atlas.templates.items():
        if only is not None and character not in only:
            continue
        resized = cv2.resize(template, (probe.shape[1], probe.shape[0]),
                             interpolation=cv2.INTER_AREA)
        scored.append((_correlate(probe, _zero_mean(resized)), character))
    if not scored:
        return Match(character=None, score=0.0)

    scored.sort(reverse=True)
    best_score, best = scored[0]
    second_score, second = scored[1] if len(scored) > 1 else (0.0, None)
    if best_score < MIN_SCORE:
        return Match(character=None, score=best_score,
                     runner_up=best, runner_up_score=second_score)
    return Match(character=best, score=best_score,
                 runner_up=second, runner_up_score=second_score)


def _zero_mean(array: np.ndarray) -> np.ndarray:
    return array - float(array.mean())


def _correlate(a: np.ndarray, b: np.ndarray) -> float:
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator == 0.0:
        return 0.0
    return float((a * b).sum() / denominator)
