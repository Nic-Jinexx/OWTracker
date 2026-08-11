"""Deterministic, offline screenshot extraction.

No models, no network, no OCR engine. Digits and hero portraits are matched
against reference bitmaps built once from the game's own rendering; player
names are recognized by nameplate hash rather than read.
"""

from .base import ExtractionResult, Extractor  # noqa: F401
