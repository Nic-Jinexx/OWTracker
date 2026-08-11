"""Operator configuration, stored in the `settings` key/value table.

Lives in the database rather than a JSON file or .env so the one-click backup
captures the whole setup and the DB stays self-describing. There are no
secrets here — this application has no credentials of any kind.
"""

from __future__ import annotations

import sqlite3

# key -> (default, type). The type is used to coerce on read; everything is
# stored as TEXT so the table is readable in any SQLite browser.
DEFAULTS: dict[str, tuple[object, type]] = {
    "my_display_name": ("", str),
    # Template match scores below this render amber in the review grid.
    "confidence_threshold": (0.75, float),
    # 6v6 is what this tracker is for. 5v5 stays fully supported — it is a
    # filter and a breakdown, never an assumption — but it is not the default.
    "default_team_size": (6, int),
    # Hero portraits are a 64-bit DCT hash, so this is bits out of 64.
    "hero_hash_max_distance": (8, int),
    # Nameplates are NOT a 64-bit hash: an animated plate defeats one entirely
    # (see app/extract/nameplates.py). They are a 480-bit white-text signature,
    # so this is bits out of 480.
    #
    # Measured over the corpus: the nearest signature of the SAME player is
    # within 35 bits for 23 of 24 appearances, while the nearest signature of a
    # DIFFERENT player is never closer than 44. 35 sits in that gap with nine
    # bits to spare. The looser value that leave-one-out testing appeared to
    # justify was wrong — it only looked safe because the player's own
    # signatures were in the library. A stranger, which is most of every lobby,
    # would have been matched to the nearest acquaintance at 49 bits.
    "nameplate_hash_max_distance": (35, int),
    # Read unrecognized nameplates as text and suggest a name. Only applies to
    # rows the hash could not place; the hash is exact and always wins. Costs
    # roughly a second per unrecognized row, and does nothing at all unless the
    # OCR engine is installed. 1 to enable, 0 to turn off.
    "read_unknown_names": (1, int),
}


def get(conn: sqlite3.Connection, key: str):
    if key not in DEFAULTS:
        raise KeyError(f"Unknown setting: {key}")
    default, kind = DEFAULTS[key]
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None or row["value"] is None or row["value"] == "":
        return default
    try:
        return kind(row["value"])
    except (TypeError, ValueError):
        return default


def set(conn: sqlite3.Connection, key: str, value) -> None:
    if key not in DEFAULTS:
        raise KeyError(f"Unknown setting: {key}")
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, "" if value is None else str(value)),
    )
    conn.commit()


def all(conn: sqlite3.Connection) -> dict:
    return {key: get(conn, key) for key in DEFAULTS}


def ensure_defaults(conn: sqlite3.Connection) -> None:
    """Materialize defaults so the table is legible to someone opening the DB
    directly, instead of being empty until a value is changed."""
    for key, (default, _) in DEFAULTS.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
            (key, str(default)),
        )
    conn.commit()
