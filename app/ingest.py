"""Screenshot ingestion: hashing, duplicate refusal, archival.

Grouping is asserted by the operator, never inferred. Whatever is submitted
together becomes one draft; a second endpoint attaches a late-arriving file to
an existing draft. The app deliberately does not pair files by timestamp or
roster similarity — back-to-back games with the same lobby are common, and a
wrong pairing would fabricate a match that never happened.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from . import paths
from .db import utcnow

KINDS = ("endgame_report", "in_game_scoreboard")

# Pillow decodes these; anything else is rejected before it reaches the
# extractor.
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}

MAX_BYTES = 40 * 1024 * 1024


class IngestError(Exception):
    """Bad input. Always surfaced to the operator, never as a stack trace."""

    def __init__(self, message: str, *, code: str = "invalid", extra: dict | None = None):
        self.message = message
        self.code = code
        self.extra = extra or {}
        super().__init__(message)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def find_committed_duplicate(conn: sqlite3.Connection, digest: str) -> sqlite3.Row | None:
    """Invariant 5: the same file must not silently become a second match."""
    return conn.execute(
        "SELECT ms.match_id, ms.file_path, ms.kind, ms.ingested_at, m.played_at, m.result "
        "FROM match_sources ms JOIN matches m ON m.id = ms.match_id "
        "WHERE ms.file_sha256 = ? LIMIT 1",
        (digest,),
    ).fetchone()


def store_for_draft(draft_id: int, filename: str, payload: bytes) -> dict:
    """Write an uploaded file into the draft's own folder.

    Files live under screenshots/drafts/<draft_id>/ until commit moves them to
    screenshots/<match_id>/. An abandoned draft therefore leaves a clearly
    named folder that is obvious and safe to sweep.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise IngestError(
            f"{filename}: not an image OWTracker can read "
            f"({', '.join(sorted(ALLOWED_SUFFIXES))}).",
            code="unsupported_type",
        )
    if not payload:
        raise IngestError(f"{filename}: file is empty.", code="empty")
    if len(payload) > MAX_BYTES:
        raise IngestError(
            f"{filename}: larger than {MAX_BYTES // 1024 // 1024} MB.", code="too_large"
        )

    digest = sha256_bytes(payload)
    folder = paths.DRAFT_SCREENSHOTS_DIR / str(draft_id)
    folder.mkdir(parents=True, exist_ok=True)

    # Content-addressed name: re-uploading the same file cannot produce two
    # copies, and the name is stable across renames on the operator's disk.
    safe_stem = Path(filename).stem[:40].replace(" ", "_")
    safe_stem = "".join(c for c in safe_stem if c.isalnum() or c in "-_") or "shot"
    destination = folder / f"{safe_stem}-{digest[:12]}{suffix}"
    destination.write_bytes(payload)

    return {
        "path": paths.portable(destination),
        "absolute": str(destination),
        "sha256": digest,
        "filename": filename,
        "bytes": len(payload),
        "ingested_at": utcnow(),
    }


def verify_readable(absolute_path: str) -> tuple[int, int]:
    """Confirm the bytes really are a decodable image.

    A malformed file must produce a visible error and an empty draft, never a
    crash — so this runs before anything downstream touches the pixels.
    """
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:  # pragma: no cover - Pillow is a hard dependency
        return (0, 0)

    try:
        with Image.open(absolute_path) as image:
            image.verify()
        with Image.open(absolute_path) as image:
            return image.size
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise IngestError(
            f"That file could not be read as an image ({error.__class__.__name__}).",
            code="undecodable",
        ) from error


def guess_kind(filename: str, size: tuple[int, int] | None = None) -> str:
    """A first guess only — the operator can always correct it in the UI.

    Real detection arrives with anchor-based localization, which can tell the
    two layouts apart by what it actually finds. Until then this is a
    filename hint and nothing more, which is why the drop zone shows the
    detected kind as an editable control rather than a fact.
    """
    lowered = Path(filename).name.lower()
    for marker in ("tab", "scoreboard", "ingame", "in-game", "in_game"):
        if marker in lowered:
            return "in_game_scoreboard"
    return "endgame_report"
