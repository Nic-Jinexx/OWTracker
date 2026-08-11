"""CSV export and database backup.

The point of both is that the operator's data is never trapped inside this
application.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .. import paths
from ..db import connect, get_conn

router = APIRouter(prefix="/api/export", tags=["export"])

# Whitelist: the table name is interpolated into SQL, so it can never come
# straight from the URL.
EXPORTABLE = (
    "matches", "match_players", "match_bans", "match_sources",
    "players", "player_nameplates", "heroes", "maps", "ranks",
    "field_provenance", "settings", "tags", "player_tags",
)

# A denormalized view is what someone actually wants in a spreadsheet.
JOINED_SQL = """
SELECT
    m.id AS match_id, m.played_at, m.result, m.team_size, m.duration_seconds,
    maps.name AS map, COALESCE(m.mode, maps.mode) AS mode,
    low.name AS rank_low, high.name AS rank_high,
    mp.team, mp.is_me, p.display_name AS player, h.name AS hero, mp.role,
    mp.eliminations, mp.assists, mp.deaths, mp.damage, mp.healing, mp.mitigation
FROM matches m
LEFT JOIN maps ON maps.id = m.map_id
LEFT JOIN ranks low  ON low.id  = m.rank_range_low
LEFT JOIN ranks high ON high.id = m.rank_range_high
LEFT JOIN match_players mp ON mp.match_id = m.id
LEFT JOIN players p ON p.id = mp.player_id
LEFT JOIN heroes  h ON h.id = mp.hero_id
ORDER BY m.played_at DESC, m.id DESC,
         CASE mp.team WHEN 'ally' THEN 0 ELSE 1 END, mp.row_index
"""


def _csv_response(rows, columns, filename: str) -> StreamingResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row[column] for column in columns])
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/tables")
def list_tables() -> dict:
    return {"tables": list(EXPORTABLE), "views": ["everything"]}


@router.get("/everything.csv")
def export_everything() -> StreamingResponse:
    """One flat row per player per match — the shape you'd want in Excel."""
    with get_conn() as conn:
        rows = conn.execute(JOINED_SQL).fetchall()
        columns = [d[0] for d in conn.execute(JOINED_SQL).description]
    stamp = datetime.now().strftime("%Y%m%d")
    return _csv_response(rows, columns, f"owtracker-{stamp}.csv")


@router.get("/{table}.csv")
def export_table(table: str) -> StreamingResponse:
    if table not in EXPORTABLE:
        raise HTTPException(404, f"Not exportable: {table}")
    with get_conn() as conn:
        cursor = conn.execute(f"SELECT * FROM {table}")
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
    return _csv_response(rows, columns, f"{table}.csv")


@router.post("/backup")
def backup_database() -> dict:
    """Copy the live database to a timestamped file.

    Uses SQLite's own backup API rather than a file copy, so it is safe while
    the server is running and with WAL journaling active.
    """
    paths.ensure_data_dirs()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = paths.BACKUPS_DIR / f"owtracker-{stamp}.db"

    source = connect()
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    return {
        "path": paths.portable(destination),
        "bytes": destination.stat().st_size,
    }


@router.get("/backups")
def list_backups() -> list[dict]:
    paths.ensure_data_dirs()
    return [
        {
            "name": path.name,
            "bytes": path.stat().st_size,
            "created": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        }
        for path in sorted(paths.BACKUPS_DIR.glob("*.db"), reverse=True)
    ]
