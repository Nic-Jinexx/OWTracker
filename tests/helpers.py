"""Test scaffolding: an isolated database per test case.

Stdlib unittest only — no third-party test dependency, so the suite runs under
the shipped runtime.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, paths  # noqa: E402


class DatabaseTestCase(unittest.TestCase):
    """Redirects the data directory at a temp folder so tests never touch the
    operator's real database or screenshots."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)

        self._saved = {
            name: getattr(paths, name)
            for name in ("DATA_DIR", "DB_PATH", "SCREENSHOTS_DIR",
                         "DRAFT_SCREENSHOTS_DIR", "BACKUPS_DIR")
        }
        paths.DATA_DIR = root / "data"
        paths.DB_PATH = paths.DATA_DIR / "owtracker.db"
        paths.SCREENSHOTS_DIR = paths.DATA_DIR / "screenshots"
        paths.DRAFT_SCREENSHOTS_DIR = paths.SCREENSHOTS_DIR / "drafts"
        paths.BACKUPS_DIR = paths.DATA_DIR / "backups"

        db.init_db()
        self.conn = db.connect()

    def tearDown(self) -> None:
        self.conn.close()
        for name, value in self._saved.items():
            setattr(paths, name, value)
        self._tmp.cleanup()

    # -- convenience ------------------------------------------------------

    def hero_id(self, name: str) -> int:
        return self.conn.execute("SELECT id FROM heroes WHERE name = ?", (name,)).fetchone()["id"]

    def map_id(self, name: str) -> int:
        return self.conn.execute("SELECT id FROM maps WHERE name = ?", (name,)).fetchone()["id"]

    def insert_draft(self, payload: dict) -> int:
        import json

        now = db.utcnow()
        cursor = self.conn.execute(
            "INSERT INTO drafts (created_at, updated_at, status, payload) VALUES (?, ?, 'open', ?)",
            (now, now, json.dumps(payload)),
        )
        self.conn.commit()
        return int(cursor.lastrowid)
