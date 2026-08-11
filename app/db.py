"""SQLite access, migrations, and seeding.

The database is the whole product: it must open in any SQLite browser and make
sense without this application. No ORM, plain SQL, numbered migration files.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from . import paths


def utcnow() -> str:
    """ISO-8601 UTC timestamp. Stored as TEXT so the DB stays inspectable."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    paths.ensure_data_dirs()
    conn = sqlite3.connect(paths.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Per-call connection. Routes are sync, so FastAPI runs them in a
    threadpool — a shared connection would need locking for no benefit."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Migrations
# --------------------------------------------------------------------------

def _current_version(conn: sqlite3.Connection) -> int:
    # The settings table holds the version, so it has to exist before the
    # first migration runs. Creating it here rather than in 001 avoids a
    # chicken-and-egg problem and is harmless to repeat.
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row else 0


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply every migration newer than the recorded version, in order, each
    in its own transaction. Returns the filenames applied."""
    version = _current_version(conn)
    applied: list[str] = []

    files = sorted(paths.MIGRATIONS_DIR.glob("*.sql"))
    for path in files:
        try:
            number = int(path.name.split("_", 1)[0])
        except ValueError:
            raise RuntimeError(f"Migration filename must start with a number: {path.name}")
        if number <= version:
            continue

        sql = path.read_text(encoding="utf-8")
        try:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(number),),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        applied.append(path.name)

    return applied


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------

def _seed_table(conn: sqlite3.Connection, table: str, natural_key: str, rows: list[dict]) -> int:
    """Insert-if-absent by natural key.

    Idempotent, so re-running never duplicates and appending a newly released
    hero to seed/heroes.json picks it up on the next start.
    """
    columns = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in columns)
    column_list = ", ".join(columns)
    inserted = 0
    for row in rows:
        existing = conn.execute(
            f"SELECT 1 FROM {table} WHERE {natural_key} = ?", (row[natural_key],)
        ).fetchone()
        if existing:
            continue
        conn.execute(
            f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})",
            [row[c] for c in columns],
        )
        inserted += 1
    return inserted


def seed(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, key, filename in (
        ("heroes", "name", "heroes.json"),
        ("maps", "name", "maps.json"),
        ("ranks", "ordinal", "ranks.json"),
    ):
        data = json.loads((paths.SEED_DIR / filename).read_text(encoding="utf-8"))
        counts[table] = _seed_table(conn, table, key, data)
    conn.commit()
    return counts


def init_db() -> dict:
    """Called once at startup. Safe to call repeatedly."""
    with get_conn() as conn:
        applied = migrate(conn)
        counts = seed(conn)
        from . import settings as settings_module

        settings_module.ensure_defaults(conn)
    return {"migrations_applied": applied, "seeded": counts}
