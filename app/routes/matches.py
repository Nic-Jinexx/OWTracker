"""Browse committed matches, and reopen one for editing."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from .. import draft as draft_module
from .. import paths
from ..db import get_conn, utcnow

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


@router.post("/{match_id}/reopen")
def reopen_match(match_id: int) -> dict:
    """Turn a committed match back into a draft, for editing.

    Editing does not get its own writer. Invariant 10 says there is exactly one
    code path that writes a match, and a second one that quietly diverged from
    the first is how the edit screen ends up able to save states the entry
    screen would have rejected. So an edit is: unpack the match into the draft
    shape, hand it to the review grid the operator already knows, and let the
    ordinary commit put it back — with `editing_match_id` telling commit to
    replace rather than insert.

    The match stays exactly as it is until that commit lands. Abandoning the
    draft changes nothing, which is what makes the edit button safe to press.
    """
    existing = get_match(match_id)
    match, players = existing["match"], existing["players"]

    with get_conn() as conn:
        open_draft = conn.execute(
            "SELECT id FROM drafts WHERE status = 'open' "
            "AND json_extract(payload, '$.editing_match_id') = ?",
            (match_id,),
        ).fetchone()
        if open_draft:
            # Two drafts editing one match would race, and the loser's edits
            # would vanish with no warning. Reuse the one already open.
            return {"draft_id": open_draft["id"], "reused": True}

        team_size = match["team_size"] or max(
            (len([p for p in players if p["team"] == side]) for side in ("ally", "enemy")),
            default=6) or 6
        payload = draft_module.empty_draft(team_size)
        payload["editing_match_id"] = match_id

        for column in draft_module.META_FIELDS:
            payload["meta"][column] = draft_module.field(match.get(column), source="manual")

        by_slot = {(p["team"], p["row_index"]): p for p in players}
        for row in payload["rows"]:
            source_row = by_slot.get((row["team"], row["row_index"]))
            if source_row is None:
                continue
            row["is_me"] = bool(source_row["is_me"])
            # Name only, deliberately not `player_id`. Commit prefers the id
            # when both are present, so carrying it would make retyping a name
            # in the edit grid do nothing at all: the row would silently save
            # as whoever it was before. Identity is the exact display name
            # (invariant 4), so re-resolving from the name gives back the same
            # player when it is unchanged, and the right one when it is not.
            row["player_id"] = None
            row["player_name"] = draft_module.field(
                source_row["display_name"], source="manual")
            row["role"] = draft_module.field(source_row["role"], source="manual")
            row["hero_id"] = draft_module.field(source_row["hero_id"], source="manual")
            for stat in draft_module.STAT_FIELDS:
                row[stat] = draft_module.field(source_row[stat], source="manual")

        payload["bans"] = [b["hero_id"] for b in existing["bans"]]
        # The screenshots stay attached to the match, not carried into the
        # draft: they are already archived under the match id and moving them
        # again on commit would be moving them onto themselves.
        payload["files"] = []

        now = utcnow()
        cursor = conn.execute(
            "INSERT INTO drafts (created_at, updated_at, status, payload) "
            "VALUES (?, ?, 'open', ?)",
            (now, now, json.dumps(payload)),
        )
        conn.commit()
        return {"draft_id": int(cursor.lastrowid), "reused": False}


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
