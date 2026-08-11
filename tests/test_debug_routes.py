"""Debug endpoint tests.

Route functions are called directly rather than over HTTP — the suite has no
HTTP client dependency, and this is also why route signatures must use plain
defaults instead of `Query(...)`.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException                              # noqa: E402

from app import paths                                          # noqa: E402
from app.routes import debug                                   # noqa: E402
from helpers import DatabaseTestCase                            # noqa: E402

SAMPLES = sorted(paths.SAMPLES_DIR.glob("*.png")) if paths.SAMPLES_DIR.exists() else []

_MANIFEST = paths.SAMPLES_DIR / "manifest.json"
_ENTRIES = (json.loads(_MANIFEST.read_text(encoding="utf-8"))["images"]
            if _MANIFEST.exists() else [])
_BY_FILE = {entry["file"]: entry for entry in _ENTRIES}
# `localize_sample` is the *endgame* localizer's report whatever the sample is,
# so an in-game shot is expected to come back refused there. The visual
# endpoints dispatch by declared kind; the JSON one deliberately does not.
EXPECTED_OK = {
    entry["file"]: entry.get("localizes", True) and entry["kind"] == "endgame_report"
    for entry in _ENTRIES
}


@unittest.skipIf(not SAMPLES, "no screenshots in samples/")
class SampleOverlayTest(unittest.TestCase):
    def test_overlay_returns_a_png(self):
        response = debug.overlay_sample(SAMPLES[0].name)
        self.assertEqual(response.media_type, "image/png")
        self.assertTrue(response.body.startswith(b"\x89PNG"))

    def test_localize_report_is_json_serializable(self):
        """Diagnostics ride along in the extractor result, which is stored as
        JSON, so a stray numpy scalar in there would only fail at write time.

        This has to hold on the refusal path too — that is exactly when a
        caller most wants to see the diagnostics.
        """
        for path in SAMPLES:
            with self.subTest(sample=path.name):
                report = debug.localize_sample(path.name)
                json.dumps(report)
                self.assertEqual(report["ok"], EXPECTED_OK.get(path.name, True),
                                 report["reason"])
                if not report["ok"]:
                    self.assertTrue(report["reason"])

    def test_gallery_lists_every_sample(self):
        html = debug.sample_gallery()
        for path in SAMPLES:
            self.assertIn(path.name, html)

    def test_the_gallery_draws_each_sample_with_its_own_localizer(self):
        """There are two boards and two localizers. Drawing a Tab shot with the
        endgame one renders a correct refusal as a picture of a failure, which
        is exactly the wrong signal on the page that exists to be looked at."""
        tab = [name for name, entry in _BY_FILE.items()
               if entry["kind"] == "in_game_scoreboard"]
        if not tab:
            self.skipTest("no in-game sample")
        html = debug.sample_gallery()
        for name in tab:
            with self.subTest(sample=name):
                self.assertIn(f"<b>{name}</b> [tab] — ok", html)
                self.assertTrue(
                    debug.overlay_sample(name).body.startswith(b"\x89PNG"))
        for name, entry in _BY_FILE.items():
            if entry["kind"] == "endgame_report":
                self.assertIn(f"<b>{name}</b> [endgame]", html)

    def test_unknown_sample_is_a_404(self):
        with self.assertRaises(HTTPException) as caught:
            debug.overlay_sample("no-such-file.png")
        self.assertEqual(caught.exception.status_code, 404)

    def test_traversal_out_of_samples_is_refused(self):
        """The one endpoint that takes a filename must not be talkable into
        reading anything outside samples/."""
        for attempt in ("../CLAUDE-OWTRACKER.md", "..\\requirements.txt",
                        "sub/../../run.bat", "/etc/passwd", "C:\\Windows\\win.ini"):
            with self.subTest(attempt=attempt):
                with self.assertRaises(HTTPException) as caught:
                    debug.overlay_sample(attempt)
                self.assertIn(caught.exception.status_code, (400, 404))


class DraftOverlayTest(DatabaseTestCase):
    def test_draft_without_files_is_a_404(self):
        draft_id = self.insert_draft({"files": [], "meta": {}, "rows": []})
        with self.assertRaises(HTTPException) as caught:
            debug.overlay_draft(draft_id)
        self.assertEqual(caught.exception.status_code, 404)

    def test_missing_draft_is_a_404(self):
        with self.assertRaises(HTTPException) as caught:
            debug.overlay_draft(999999)
        self.assertEqual(caught.exception.status_code, 404)

    @unittest.skipIf(not SAMPLES, "no screenshots in samples/")
    def test_overlay_for_an_attached_screenshot(self):
        sample = SAMPLES[0]
        draft_id = self.insert_draft({
            "files": [{"path": paths.portable(sample), "sha256": "abc123",
                       "filename": sample.name, "kind": "endgame_report"}],
            "meta": {}, "rows": [],
        })
        response = debug.overlay_draft(draft_id)
        self.assertTrue(response.body.startswith(b"\x89PNG"))

        report = debug.localize_draft(draft_id)
        self.assertTrue(report["ok"], report["reason"])
        self.assertEqual(report["diagnostics"]["row_counts"], {"blue": 6, "red": 6})

    @unittest.skipIf(not SAMPLES, "no screenshots in samples/")
    def test_unknown_sha_on_a_real_draft_is_a_404(self):
        sample = SAMPLES[0]
        draft_id = self.insert_draft({
            "files": [{"path": paths.portable(sample), "sha256": "abc123",
                       "filename": sample.name, "kind": "endgame_report"}],
            "meta": {}, "rows": [],
        })
        with self.assertRaises(HTTPException) as caught:
            debug.overlay_draft(draft_id, sha256="deadbeef")
        self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
