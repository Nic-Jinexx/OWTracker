"""Stage 7: read a nameplate as text.

The rest of extraction reads a rendered UI with a fixed font, which is why it
is template matching and not recognition. Gamertags are the one thing on the
scoreboard the game does not choose: arbitrary strings in a stylized face, so
there is no reference bitmap to match and template matching has nothing to
work with. That is the gap this fills, and the only place in the project where
a model runs.

**It suggests, it does not decide.** Nameplate *identity* is still settled by
the perceptual hash in `nameplates.py`, which is proven and exact. OCR runs
only for rows the hash did not recognize, and produces one of two things:

- the name of a player already in the database, when the text is close enough
  to exactly one of them and clearly closer to that one than any other;
- otherwise the raw text, offered as a prefill for the operator to correct.

Both arrive as low-confidence values so the review grid highlights them. A
suggestion the operator has to fix is still most of the typing saved; a
confident wrong name is the failure this project refuses everywhere else, so
the matcher declines rather than reaches.

**Measured on the sample corpus** (24 nameplates, `tools/score_ocr.py`):

    raw text exactly right          13/24   54%
    snapped to the right player     24/24  100%
    snapped to the WRONG player      0/24
    strangers named as an acquaintance   0/24

That last line is the one that matters, and it is why this is tested with the
true player *removed* from the roster rather than by leave-one-out. Leave-one-out
always has the right answer in the library; it cannot show what happens to a
stranger. The same mistake nearly shipped the nameplate hash at a threshold
that matched strangers to acquaintances.

**Optional at runtime.** The engine is an extra dependency and a 30 MB download.
If it is not installed, `available()` is False, nothing imports it, and the app
behaves exactly as it did before: type each name once and the hash remembers it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

__all__ = ["NameSuggestion", "available", "clean", "distance", "read_text",
           "snap_to_roster", "suggest"]

# The nameplate crop that `nameplates.py` hashes is deliberately tight and
# masked to bright pixels: it wants a stable signature, not legible text, and a
# wider crop would hash the row background. Reading needs the opposite. This
# box is wider and taller, measured by sweeping it against the corpus: at the
# hashing bounds the reader truncated every long name (GOLDFISHSNAQ came back
# "GOLDFIS"), and moving the left edge inward to escape the badge that precedes
# the name started clipping real first letters instead. So the badge is left in
# and dealt with after the read.
TEXT_BOUNDS = (188.0, 24.0, 680.0, 108.0)

# How far the read text may sit from a known name and still be called that
# player. Two edits covers the observed damage: a hallucinated leading
# character, and one letter confusion (Q read as O, I read as T).
MAX_SNAP_DISTANCE = 2
# The best candidate must beat the runner-up by at least this much. Without it,
# two similarly-named players would make the choice a coin flip.
MIN_SNAP_MARGIN = 1
# Nothing shorter is worth snapping: at three characters, two edits reaches
# most of the roster.
MIN_SNAP_LENGTH = 4

# Suggestions are capped below any plausible review threshold so they always
# surface for confirmation, however sure the engine claims to be.
MAX_SUGGESTION_CONFIDENCE = 0.5

# A detection box shorter than this fraction of the tallest one is furniture,
# not the name. Measured on the corpus: badge boxes 49-54%, name boxes 93-98%,
# so anywhere in that gap works and 0.70 sits in the middle of it.
MIN_BOX_HEIGHT_RATIO = 0.70


@dataclass(frozen=True)
class NameSuggestion:
    """A proposed name and where it came from.

    `player_id` is set only when the text was snapped to somebody already in
    the database. A `read` suggestion is text nobody has confirmed yet.
    """
    name: str
    origin: str                  # "known" or "read"
    confidence: float
    raw: str = ""
    player_id: int | None = None


def available() -> bool:
    """Whether the OCR engine can be loaded, without loading it."""
    import importlib.util
    return importlib.util.find_spec("rapidocr_onnxruntime") is not None


@lru_cache(maxsize=1)
def _engine():
    """Built once. Construction loads two ONNX models and costs about a
    second, which is worth paying at most once per process."""
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def _box_height(box) -> float:
    ys = [point[1] for point in box]
    return max(ys) - min(ys)


def read_text(crop_bgr: np.ndarray) -> tuple[str, float]:
    """Raw text and confidence from one nameplate crop.

    Boxes are joined left to right rather than taking the most confident one:
    the reader sometimes splits a name in two, and taking the best box alone
    would return half of it with high confidence.

    Short boxes are dropped first. The nameplate carries a small badge to the
    left of the name, and the reader turns it into a stray "O" or "11" with
    total confidence — that is where the numbers appearing in names came from.
    It is always a *separate* detection box and always a much shorter one:
    measured across the corpus, badge boxes run 49-54% of the crop height while
    the name runs 93-98%. Filtering on relative height removes them without
    guessing at the text, which is the part that must not be tampered with.
    """
    if crop_bgr is None or crop_bgr.size == 0 or not available():
        return "", 0.0
    try:
        result, _ = _engine()(crop_bgr)
    except Exception:  # pragma: no cover - the engine must never break ingest
        return "", 0.0
    if not result:
        return "", 0.0

    tallest = max(_box_height(box[0]) for box in result)
    kept = [box for box in result
            if _box_height(box[0]) >= tallest * MIN_BOX_HEIGHT_RATIO]
    if not kept:                                  # pragma: no cover - defensive
        kept = result

    ordered = sorted(kept, key=lambda box: box[0][0][0])
    text = "".join(str(box[1]) for box in ordered)
    confidence = min(float(box[2]) for box in ordered)
    return text, confidence


def clean(raw: str) -> str:
    """Upper-case, and drop what a gamertag cannot contain.

    Deliberately does not strip a leading character, even though the badge
    before the name is read as a stray O or 1 about a third of the time. A name
    may legitimately start with either, and guessing wrong there would corrupt
    the one thing the operator is being asked to confirm. The leading junk is
    handled by the matcher, which tries the text both ways.
    """
    return "".join(ch for ch in raw.upper() if ch.isalnum() or ch in "_-[]")


def distance(left: str, right: str) -> int:
    """Levenshtein. Short strings, so the simple two-row version is plenty."""
    if left == right:
        return 0
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(previous[j] + 1,
                               current[j - 1] + 1,
                               previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def snap_to_roster(text: str, roster: list[tuple[int, str]]
                   ) -> tuple[int, str, int] | None:
    """Nearest known player as `(id, name, distance)`, or None to decline.

    Declining is the common and correct outcome for anyone new. The candidate
    set includes the text with one and two leading characters removed, because
    the badge rendered immediately before the name reads as a character;
    trimming is tried here, where a wrong trim can only fail to match, rather
    than in `clean`, where it would corrupt the text shown to the operator.
    """
    if not text or len(text) < MIN_SNAP_LENGTH or not roster:
        return None

    candidates = {text}
    if len(text) > MIN_SNAP_LENGTH + 1:
        candidates.add(text[1:])
        candidates.add(text[2:])

    best: tuple[int, str, int] | None = None
    runner_up = len(text) + 99
    for player_id, name in roster:
        gap = min(distance(candidate, name.upper()) for candidate in candidates)
        if best is None or gap < best[2]:
            runner_up = best[2] if best else runner_up
            best = (player_id, name, gap)
        elif gap < runner_up:
            runner_up = gap

    if best is None or best[2] > MAX_SNAP_DISTANCE:
        return None
    if runner_up - best[2] < MIN_SNAP_MARGIN:
        # Two players equally close. Unknown is a fine answer; wrong is not.
        return None
    return best


def suggest(crop_bgr: np.ndarray, roster: list[tuple[int, str]]
            ) -> NameSuggestion | None:
    """Read a nameplate and propose a name, or None if nothing legible."""
    raw, confidence = read_text(crop_bgr)
    text = clean(raw)
    if not text:
        return None

    match = snap_to_roster(text, roster)
    if match is not None:
        player_id, name, gap = match
        # Closer text is better evidence, but never confident enough to skip
        # review: the operator confirms every suggestion.
        scaled = confidence * (1.0 - gap / (MAX_SNAP_DISTANCE + 1))
        return NameSuggestion(name=name, origin="known", player_id=player_id,
                              confidence=min(scaled, MAX_SUGGESTION_CONFIDENCE),
                              raw=text)
    return NameSuggestion(name=text, origin="read", raw=text,
                          confidence=min(confidence, MAX_SUGGESTION_CONFIDENCE) * 0.5)
