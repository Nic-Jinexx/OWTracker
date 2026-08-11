"""Perceptual hashing (DCT pHash) — the primitive behind both hero portrait
identification and nameplate recognition.

Implemented directly on numpy/OpenCV rather than pulling in an `imagehash`
dependency: it is about twenty lines, and every dependency has to be vendored
into the shipped runtime.

The hash is deliberately insensitive to scale and mild compression, and
sensitive to structure. Two captures of the same hero portrait at different
resolutions should land within a few bits of each other; two different heroes
should not.
"""

from __future__ import annotations

import cv2
import numpy as np

# Work at 32x32 and keep the low-frequency 8x8 corner: enough structure to
# separate ~44 heroes, coarse enough to survive rescaling.
IMAGE_SIZE = 32
HASH_SIZE = 8
HASH_BITS = HASH_SIZE * HASH_SIZE  # 64


def phash(image: np.ndarray) -> str:
    """Return a 64-bit perceptual hash as 16 hex characters.

    Accepts grayscale or colour (BGR) arrays.
    """
    if image is None or image.size == 0:
        raise ValueError("phash received an empty image")

    if image.ndim == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    resized = cv2.resize(
        image.astype(np.float32), (IMAGE_SIZE, IMAGE_SIZE), interpolation=cv2.INTER_AREA
    )
    coefficients = cv2.dct(resized)
    block = coefficients[:HASH_SIZE, :HASH_SIZE].flatten()

    # Drop the DC term before taking the median: it carries overall brightness,
    # which would otherwise dominate and make every dark portrait look alike.
    median = float(np.median(block[1:]))
    bits = block > median

    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def hamming(left: str, right: str) -> int:
    """Bit distance between two hex hashes. 0 is identical, 64 is opposite."""
    return bin(int(left, 16) ^ int(right, 16)).count("1")


def nearest(candidate: str, library: dict[str, str], max_distance: int) -> tuple[str | None, int]:
    """Closest library entry within tolerance.

    `library` maps a label (hero name, player id) to a hash. Returns
    (label_or_None, distance). Returning None rather than the nearest match is
    the point: an unrecognized portrait must surface as unknown, not as a
    confident wrong hero.
    """
    best_label: str | None = None
    best_distance = HASH_BITS + 1
    for label, known in library.items():
        distance = hamming(candidate, known)
        if distance < best_distance:
            best_label, best_distance = label, distance
    if best_label is None or best_distance > max_distance:
        return None, best_distance
    return best_label, best_distance


def confidence_from_distance(distance: int, max_distance: int) -> float:
    """Map a bit distance onto the 0..1 confidence the review UI shades on.

    Exact match is 1.0; a match right at the tolerance limit scores low enough
    to render amber under any sensible threshold.
    """
    if max_distance <= 0:
        return 1.0 if distance == 0 else 0.0
    return max(0.0, 1.0 - (distance / float(max_distance)) * 0.5) if distance <= max_distance \
        else 0.0
