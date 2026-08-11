"""Player pages.

"With" and "against" are defined relative to the operator's own row (`is_me`),
which is why exactly one such row per match matters so much.

Every rate is returned alongside its sample size. The frontend greys anything
under five games rather than printing a bare percentage — a 100% win rate over
one game is noise, and showing it as a number invites reading it as signal.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db import get_conn, utcnow

router = APIRouter(prefix="/api/players", tags=["players"])

# Shared shape: games played alongside/against me, and how many I won.
RELATION_SQL = """
SELECT
    COUNT(*) AS games,
    SUM(CASE WHEN m.result = 'win'  THEN 1 ELSE 0 END) AS wins,
    SUM(CASE WHEN m.result = 'loss' THEN 1 ELSE 0 END) AS losses,
    SUM(CASE WHEN m.result = 'draw' THEN 1 ELSE 0 END) AS draws
FROM match_players them
JOIN matches m       ON m.id = them.match_id
JOIN match_players me ON me.match_id = them.match_id AND me.is_me = 1
WHERE them.player_id = ?
  AND them.is_me = 0
  AND them.team {comparison} me.team
"""


def _rate(row) -> dict:
    games = row["games"] or 0
    wins = row["wins"] or 0
    return {
        "games": games,
        "wins": wins,
        "losses": row["losses"] or 0,
        "draws": row["draws"] or 0,
        # Rate is None below the display threshold so the UI cannot
        # accidentally render a percentage off a two-game sample.
        "win_rate": (wins / games) if games else None,
        "reliable": games >= 5,
    }


def _tags_by_player(conn, player_ids: list[int] | None = None) -> dict[int, list[str]]:
    """Tag codes per player, in the vocabulary's own order.

    One query for the whole list rather than one per row — a tag column in a
    forty-player table should not cost forty round trips.
    """
    sql = ("SELECT pt.player_id, pt.tag_code FROM player_tags pt "
           "JOIN tags t ON t.code = pt.tag_code ")
    params: list = []
    if player_ids:
        sql += "WHERE pt.player_id IN (%s) " % ",".join("?" * len(player_ids))
        params = list(player_ids)
    sql += "ORDER BY pt.player_id, t.sort_order"

    grouped: dict[int, list[str]] = {}
    for row in conn.execute(sql, params):
        grouped.setdefault(row["player_id"], []).append(row["tag_code"])
    return grouped


@router.get("")
def list_players(limit: int = 200) -> list[dict]:
    """The players list, and the feed for the Overall page's subject picker.

    `has_notes` rather than the note text: the list only needs to know whether
    to show its marker, and a long note has no business travelling with every
    row of a table that will not render it.
    """
    with get_conn() as conn:
        players = [dict(r) for r in conn.execute(
            """
            SELECT p.id, p.display_name, p.games_seen, p.first_seen, p.last_seen,
                   (p.notes IS NOT NULL) AS has_notes,
                   EXISTS (SELECT 1 FROM match_players mp
                           WHERE mp.player_id = p.id AND mp.is_me = 1) AS is_me,
                   (SELECT COUNT(*) FROM player_nameplates np WHERE np.player_id = p.id)
                       AS nameplate_count
            FROM players p
            ORDER BY p.games_seen DESC, p.display_name
            LIMIT ?
            """,
            (limit,),
        )]
        tags = _tags_by_player(conn, [p["id"] for p in players])
        for player in players:
            player["tags"] = tags.get(player["id"], [])
            player["has_notes"] = bool(player["has_notes"])
            player["is_me"] = bool(player["is_me"])
        return players


@router.get("/{player_id}")
def get_player(player_id: int) -> dict:
    with get_conn() as conn:
        player = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        if player is None:
            raise HTTPException(404, f"Player {player_id} not found")

        with_me = conn.execute(
            RELATION_SQL.format(comparison="="), (player_id,)
        ).fetchone()
        against_me = conn.execute(
            RELATION_SQL.format(comparison="!="), (player_id,)
        ).fetchone()

        averages = conn.execute(
            """
            SELECT COUNT(*) AS rows_with_stats,
                   AVG(eliminations) AS eliminations, AVG(assists) AS assists,
                   AVG(deaths) AS deaths, AVG(damage) AS damage,
                   AVG(healing) AS healing, AVG(mitigation) AS mitigation
            FROM match_players
            WHERE player_id = ? AND eliminations IS NOT NULL
            """,
            (player_id,),
        ).fetchone()

        heroes = [dict(r) for r in conn.execute(
            """
            SELECT h.id, h.name, h.role, COUNT(*) AS games
            FROM match_players mp
            JOIN heroes h ON h.id = mp.hero_id
            WHERE mp.player_id = ?
            GROUP BY h.id
            ORDER BY games DESC, h.name
            """,
            (player_id,),
        )]

        # `m.result` is stored from the operator's perspective, so it is only
        # this player's result when they were on the operator's team. `result`
        # is theirs; `my_result` is kept alongside it because "you lost, they
        # won" is the interesting row, not a contradiction.
        recent = [dict(r) for r in conn.execute(
            """
            SELECT m.id, m.played_at,
                   m.result AS my_result,
                   CASE WHEN m.result = 'draw'        THEN 'draw'
                        WHEN them.team = me.team      THEN m.result
                        WHEN m.result = 'win'         THEN 'loss'
                        ELSE 'win' END AS result,
                   maps.name AS map_name,
                   h.name AS hero_name, them.team AS their_team, me.team AS my_team
            FROM match_players them
            JOIN matches m        ON m.id = them.match_id
            LEFT JOIN maps        ON maps.id = m.map_id
            LEFT JOIN heroes h    ON h.id = them.hero_id
            LEFT JOIN match_players me ON me.match_id = m.id AND me.is_me = 1
            WHERE them.player_id = ?
            ORDER BY m.played_at DESC, m.id DESC
            LIMIT 25
            """,
            (player_id,),
        )]

        tags = _tags_by_player(conn, [player_id]).get(player_id, [])

    return {
        "player": dict(player),
        "tags": tags,
        "with_me": _rate(with_me),
        "against_me": _rate(against_me),
        "averages": dict(averages) if averages and averages["rows_with_stats"] else None,
        "heroes": heroes,
        "recent": recent,
    }


@router.patch("/{player_id}")
def update_player(player_id: int, body: dict) -> dict:
    """Edit a player's note.

    Blank and whitespace-only collapse to NULL server-side, so "has a note" is
    one unambiguous check everywhere instead of a truthiness rule the frontend
    and the database have to agree on independently.
    """
    unknown = set(body) - {"notes"}
    if unknown:
        raise HTTPException(400, f"Unknown field(s): {', '.join(sorted(unknown))}")

    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM players WHERE id = ?", (player_id,)).fetchone() is None:
            raise HTTPException(404, f"Player {player_id} not found")
        if "notes" in body:
            notes = (body["notes"] or "").strip() or None
            conn.execute(
                "UPDATE players SET notes = ?, notes_updated_at = ? WHERE id = ?",
                (notes, utcnow() if notes else None, player_id),
            )
        conn.commit()
        player = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        return {"player": dict(player),
                "tags": _tags_by_player(conn, [player_id]).get(player_id, [])}


@router.put("/{player_id}/tags")
def set_tags(player_id: int, body: dict) -> dict:
    """Replace a player's whole tag set.

    Wholesale rather than per-dot toggles: it is one request either way, it is
    idempotent, and two fast clicks cannot interleave into a lost update.
    """
    codes = body.get("tags")
    if not isinstance(codes, list) or any(not isinstance(c, str) for c in codes):
        raise HTTPException(400, "tags must be a list of tag codes")
    codes = list(dict.fromkeys(codes))

    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM players WHERE id = ?", (player_id,)).fetchone() is None:
            raise HTTPException(404, f"Player {player_id} not found")
        known = {r["code"] for r in conn.execute("SELECT code FROM tags")}
        unknown = [c for c in codes if c not in known]
        if unknown:
            # The foreign key would catch this too, but a 400 naming the bad
            # code is a better answer than an integrity error.
            raise HTTPException(400, f"Unknown tag(s): {', '.join(unknown)}")

        now = utcnow()
        conn.execute("DELETE FROM player_tags WHERE player_id = ?", (player_id,))
        conn.executemany(
            "INSERT INTO player_tags (player_id, tag_code, created_at) VALUES (?, ?, ?)",
            [(player_id, code, now) for code in codes],
        )
        conn.commit()
        return {"id": player_id,
                "tags": _tags_by_player(conn, [player_id]).get(player_id, [])}
