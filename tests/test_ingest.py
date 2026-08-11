"""Ingestion and perceptual hashing.

These are the parts of the extraction pipeline that do not depend on real
Overwatch screenshots, so they are tested against synthetic images. Anything
that needs the actual game UI (localization, the glyph atlas, hero portraits)
is calibrated and tested against samples/ instead.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import cv2
import numpy as np

from app import draft as draft_module
from app import ingest
from app.commit import commit_draft
from app.extract.imagehash import (
    HASH_BITS,
    confidence_from_distance,
    hamming,
    nearest,
    phash,
)
from app.extract.local import LocalExtractor
from helpers import DatabaseTestCase


def synthetic(seed: int, width: int = 200, height: int = 120) -> np.ndarray:
    """A structured image — shapes and gradients, closer to a hero portrait
    than pure noise would be."""
    rng = np.random.default_rng(seed)
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :, 0] = np.linspace(0, 255, width, dtype=np.uint8)[None, :]
    image[:, :, 1] = np.linspace(0, 180, height, dtype=np.uint8)[:, None]
    for _ in range(6):
        x, y = int(rng.integers(0, width)), int(rng.integers(0, height))
        radius = int(rng.integers(8, 30))
        color = tuple(int(c) for c in rng.integers(0, 255, 3))
        cv2.circle(image, (x, y), radius, color, -1)
    return image


def png_bytes(image: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return buffer.tobytes()


class TestPerceptualHash(unittest.TestCase):

    def test_hash_is_stable(self):
        image = synthetic(1)
        self.assertEqual(phash(image), phash(image.copy()))

    def test_hash_is_sixteen_hex_characters(self):
        value = phash(synthetic(2))
        self.assertEqual(len(value), 16)
        int(value, 16)  # parses

    def test_rescaling_barely_moves_the_hash(self):
        """The whole point: the same portrait captured at 1080p and 1440p must
        still match."""
        original = synthetic(3, 240, 240)
        smaller = cv2.resize(original, (120, 120), interpolation=cv2.INTER_AREA)
        larger = cv2.resize(original, (480, 480), interpolation=cv2.INTER_CUBIC)
        self.assertLessEqual(hamming(phash(original), phash(smaller)), 6)
        self.assertLessEqual(hamming(phash(original), phash(larger)), 6)

    def test_jpeg_compression_barely_moves_the_hash(self):
        original = synthetic(4)
        ok, buffer = cv2.imencode(".jpg", original, [cv2.IMWRITE_JPEG_QUALITY, 70])
        self.assertTrue(ok)
        recompressed = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        self.assertLessEqual(hamming(phash(original), phash(recompressed)), 6)

    def test_different_images_are_far_apart(self):
        distances = [
            hamming(phash(synthetic(i)), phash(synthetic(j)))
            for i in range(6) for j in range(6) if i < j
        ]
        self.assertGreater(min(distances), 8,
                           "distinct images must not collide within tolerance")

    def test_brightness_shift_does_not_dominate(self):
        """Dropping the DC term is what stops every dark portrait hashing
        alike."""
        original = synthetic(7)
        brighter = cv2.convertScaleAbs(original, alpha=1.0, beta=40)
        self.assertLessEqual(hamming(phash(original), phash(brighter)), 6)

    def test_nearest_returns_none_when_out_of_tolerance(self):
        """An unrecognized portrait must surface as unknown, never as the
        closest wrong hero."""
        library = {"Ana": phash(synthetic(10)), "Reaper": phash(synthetic(11))}
        label, distance = nearest(phash(synthetic(99)), library, max_distance=4)
        self.assertIsNone(label)
        self.assertGreater(distance, 4)

    def test_nearest_finds_the_right_entry(self):
        library = {"Ana": phash(synthetic(10)), "Reaper": phash(synthetic(11))}
        label, distance = nearest(phash(synthetic(11)), library, max_distance=4)
        self.assertEqual(label, "Reaper")
        self.assertEqual(distance, 0)

    def test_confidence_falls_off_with_distance(self):
        self.assertEqual(confidence_from_distance(0, 8), 1.0)
        self.assertLess(confidence_from_distance(8, 8), confidence_from_distance(2, 8))
        self.assertEqual(confidence_from_distance(HASH_BITS, 8), 0.0)

    def test_empty_image_is_rejected(self):
        with self.assertRaises(ValueError):
            phash(np.zeros((0, 0, 3), dtype=np.uint8))


class TestIngest(DatabaseTestCase):

    def test_stores_with_content_addressed_name(self):
        payload = png_bytes(synthetic(1))
        stored = ingest.store_for_draft(1, "Overwatch Screenshot.png", payload)
        self.assertEqual(stored["sha256"], ingest.sha256_bytes(payload))
        self.assertIn(stored["sha256"][:12], stored["path"])
        self.assertTrue(stored["path"].endswith(".png"))

    def test_reuploading_the_same_file_does_not_duplicate_on_disk(self):
        payload = png_bytes(synthetic(2))
        first = ingest.store_for_draft(1, "shot.png", payload)
        second = ingest.store_for_draft(1, "shot.png", payload)
        self.assertEqual(first["path"], second["path"])

    def test_rejects_unsupported_extension(self):
        with self.assertRaises(ingest.IngestError) as caught:
            ingest.store_for_draft(1, "notes.txt", b"hello")
        self.assertEqual(caught.exception.code, "unsupported_type")

    def test_rejects_empty_file(self):
        with self.assertRaises(ingest.IngestError) as caught:
            ingest.store_for_draft(1, "shot.png", b"")
        self.assertEqual(caught.exception.code, "empty")

    def test_rejects_oversized_file(self):
        with self.assertRaises(ingest.IngestError) as caught:
            ingest.store_for_draft(1, "shot.png", b"x" * (ingest.MAX_BYTES + 1))
        self.assertEqual(caught.exception.code, "too_large")

    def test_a_png_that_is_not_a_png_is_caught_before_extraction(self):
        stored = ingest.store_for_draft(1, "liar.png", b"this is plain text, not an image")
        with self.assertRaises(ingest.IngestError) as caught:
            ingest.verify_readable(stored["absolute"])
        self.assertEqual(caught.exception.code, "undecodable")

    def test_truncated_image_is_caught(self):
        payload = png_bytes(synthetic(3))
        stored = ingest.store_for_draft(1, "half.png", payload[: len(payload) // 2])
        with self.assertRaises(ingest.IngestError):
            ingest.verify_readable(stored["absolute"])

    def test_readable_image_reports_its_size(self):
        stored = ingest.store_for_draft(1, "ok.png", png_bytes(synthetic(4, 320, 200)))
        self.assertEqual(ingest.verify_readable(stored["absolute"]), (320, 200))

    def test_kind_guess_uses_filename_hint(self):
        self.assertEqual(ingest.guess_kind("Overwatch_tab_2026.png"), "in_game_scoreboard")
        self.assertEqual(ingest.guess_kind("scoreboard.png"), "in_game_scoreboard")
        self.assertEqual(ingest.guess_kind("Overwatch 2026-01-01.png"), "endgame_report")

    def test_committed_file_is_recognized_as_a_duplicate(self):
        """Invariant 5."""
        payload = draft_module.empty_draft(5)
        payload["meta"]["result"] = draft_module.field("win")
        stored = ingest.store_for_draft(1, "shot.png", png_bytes(synthetic(5)))
        payload["files"] = [{
            "path": stored["path"], "sha256": stored["sha256"],
            "kind": "endgame_report", "ingested_at": stored["ingested_at"],
        }]
        draft_id = self.insert_draft(payload)
        match_id = commit_draft(self.conn, draft_id)

        duplicate = ingest.find_committed_duplicate(self.conn, stored["sha256"])
        self.assertIsNotNone(duplicate)
        self.assertEqual(duplicate["match_id"], match_id)

        self.assertIsNone(ingest.find_committed_duplicate(self.conn, "0" * 64))

    def test_commit_moves_files_out_of_the_draft_folder(self):
        from app import paths

        payload = draft_module.empty_draft(5)
        payload["meta"]["result"] = draft_module.field("win")
        stored = ingest.store_for_draft(7, "shot.png", png_bytes(synthetic(6)))
        payload["files"] = [{
            "path": stored["path"], "sha256": stored["sha256"],
            "kind": "endgame_report", "ingested_at": stored["ingested_at"],
        }]
        match_id = commit_draft(self.conn, self.insert_draft(payload))

        from pathlib import Path

        self.assertFalse(Path(stored["absolute"]).exists(), "file should have moved")
        final = paths.SCREENSHOTS_DIR / str(match_id)
        self.assertTrue(any(final.iterdir()), "file should be in the match folder")

        row = self.conn.execute(
            "SELECT file_path FROM match_sources WHERE match_id = ?", (match_id,)
        ).fetchone()
        self.assertIn(f"/{match_id}/", row["file_path"].replace("\\", "/"))


class TestLocalExtractorContract(DatabaseTestCase):
    """The extractor must never crash the server, whatever it is handed."""

    def test_undecodable_file_returns_an_empty_draft_and_a_warning(self):
        stored = ingest.store_for_draft(1, "liar.png", b"definitely not an image")
        result = LocalExtractor().extract(stored["absolute"], "endgame_report")
        self.assertFalse(result.ok)
        self.assertTrue(result.warnings)
        self.assertEqual(result.draft["meta"]["result"]["value"], None)

    def test_missing_file_returns_an_empty_draft(self):
        result = LocalExtractor().extract("no/such/file.png", "endgame_report")
        self.assertFalse(result.ok)
        self.assertTrue(result.warnings)

    def test_a_decodable_image_that_is_not_a_scoreboard_says_why(self):
        """It decodes fine, so the refusal has to come from localization and
        has to explain itself — 'nothing happened' would leave the operator
        with no idea whether the app or the screenshot was at fault."""
        stored = ingest.store_for_draft(1, "shot.png", png_bytes(synthetic(8, 1920, 1080)))
        result = LocalExtractor().extract(stored["absolute"], "endgame_report")
        self.assertFalse(result.ok)
        self.assertTrue(result.warnings)
        self.assertEqual(result.diagnostics["width"], 1920)
        self.assertEqual(result.diagnostics["height"], 1080)

    def test_a_missing_atlas_costs_the_statistics_and_nothing_else(self):
        """A missing digit atlas used to abort the whole extraction.

        It should not: hero portraits and nameplates need nothing from it, and
        throwing away the roster because the numbers are unreadable helps
        nobody. What it must still do is say why the numbers are blank, so this
        does not read as a broken screenshot.
        """
        import tempfile

        from app import paths as paths_module
        from app.extract import glyphs as glyph_module

        sample = paths_module.SAMPLES_DIR / "kingsrowloss.png"
        if not sample.is_file():
            self.skipTest("no sample corpus")

        saved = paths_module.GLYPHS_DIR
        with tempfile.TemporaryDirectory() as empty:
            paths_module.GLYPHS_DIR = Path(empty)
            glyph_module.cached_atlas.cache_clear()
            try:
                result = LocalExtractor(6).extract(str(sample), "endgame_report")
            finally:
                paths_module.GLYPHS_DIR = saved
                glyph_module.cached_atlas.cache_clear()

        self.assertTrue(any("atlas" in w.lower() for w in result.warnings), result.warnings)
        self.assertEqual(result.diagnostics["cells_read"], 0)
        # The scoreboard was still located, so the row count came off the image.
        self.assertEqual(result.draft["meta"]["team_size"]["value"], 6)
        # The stat keys are absent rather than blank, which merge_draft treats
        # the same way — either is safe, and omitting them says "not attempted"
        # rather than "attempted and empty".
        for row in result.draft["rows"]:
            self.assertNotIn("eliminations", row,
                             "no atlas means no statistics, not guessed ones")


if __name__ == "__main__":
    unittest.main()
