"""Browse committed matches."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import paths
from ..db import get_conn

router = APIRouter(prefix="/api/matches", tags=["matches"])

LIST_SQL = """
SELECT
    m.id,
    m.played_at,
    m.result,
    m.team_size,
    m.duration_seconds,
    m.notes,
    maps.name  AS map_name,
    COALESCE(m.mode, maps.mode) AS mode,
    low.name   AS rank_low,
    high.name  AS rank_high,
    me_hero.name AS my_hero,
    me.eliminations AS my_eliminations,
    me.deaths       AS my_deaths,
    (SELECT COUNT(*) FROM match_sources s WHERE s.match_id = m.id) AS source_count
FROM matches m
LEFT JOIN maps ON maps.id = m.map_id
LEFT JOIN ranks low  ON low.id  = m.rank_range_low
LEFT JOIN ranks high ON high.id = m.rank_range_high
LEFT JOIN match_players me ON me.match_id = m.id AND me.is_me = 1
LEFT JOIN heroes me_hero ON me_hero.id = me.hero_id
ORDER BY m.played_at DESC, m.id DESC
LIMIT ? OFFSET ?
"""


@router.get("")
def list_matches(limit: int = 100, offset: int = 0) -> dict:
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(LIST_SQL, (limit, offset))]
        total = conn.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"]
    return {"matches": rows, "total": total}


@router.get("/{match_id}")
def get_match(match_id: int) -> dict:
    with get_conn() as conn:
        match = conn.execute(
            """
            SELECT m.*, maps.name AS map_name, maps.mode AS map_mode,
                   low.name AS rank_low, high.name AS rank_high
            FROM matches m
            LEFT JOIN maps ON maps.id = m.map_id
            LEFT JOIN ranks low  ON low.id  = m.rank_range_low
            LEFT JOIN ranks high ON high.id = m.rank_range_high
            WHERE m.id = ?
            """,
            (match_id,),
        ).fetchone()
        if match is None:
            raise HTTPException(404, f"Match {match_id} not found")

        players = [dict(r) for r in conn.execute(
            """
            SELECT mp.*, p.display_name, h.name AS hero_name, h.role AS hero_role
            FROM match_players mp
            LEFT JOIN players p ON p.id = mp.player_id
            LEFT JOIN heroes  h ON h.id = mp.hero_id
            WHERE mp.match_id = ?
            ORDER BY CASE mp.team WHEN 'ally' THEN 0 ELSE 1 END, mp.row_index
            """,
            (match_id,),
        )]

        bans = [dict(r) for r in conn.execute(
            "SELECT b.slot_index, h.id AS hero_id, h.name AS hero_name "
            "FROM match_bans b JOIN heroes h ON h.id = b.hero_id "
            "WHERE b.match_id = ? ORDER BY b.slot_index",
            (match_id,),
        )]

        sources = [dict(r) for r in conn.execute(
            "SELECT id, file_path, kind, ingested_at FROM match_sources WHERE match_id = ?",
            (match_id,),
        )]
    for source in sources:
        source["url"] = paths.screenshot_url(source["file_path"])

    return {
        "match": dict(match),
        "players": players,
        "bans": bans,
        "sources": sources,
    }


@router.delete("/{match_id}")
def delete_match(match_id: int) -> dict:
    """Remove a match. Archived screenshots are deliberately left on disk —
    invariant 5 says they are never auto-deleted."""
    with get_conn() as conn:
        cursor = conn.execute("DELETE FROM matches WHERE id = ?", (match_id,))
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"Match {match_id} not found")
    return {"ok": True}
