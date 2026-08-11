"""Seasons: operator-defined windows of time to group matches into.

A season is a name and a date range, not Blizzard's numbering. See
`migrations/004_seasons.sql` for why.

Assignment is derived, never typed. A match belongs to whichever season's range
contains its `played_at`, and the same rule runs on commit, on edit, and when
the ranges themselves change. That means moving a season boundary regroups the
matches under it instead of leaving them where an earlier rule put them, which
is the behaviour you want the first time you realize a season started a week
earlier than you thought.

A match with no `played_at`, or one falling in no season, is simply
unassigned. Unassigned is a real answer and shows up as its own bucket rather
than being forced into the nearest season.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db import get_conn, utcnow

router = APIRouter(prefix="/api/seasons", tags=["seasons"])

# A match is in a season when its date falls inside the range. `ends_on` NULL
# means the season is still running, so it has no upper bound. Dates are
# stored as ISO text, which sorts and compares correctly as strings, so this
# is a plain BETWEEN and not a date function.
IN_SEASON = """
    m.played_at IS NOT NULL
    AND date(m.played_at) >= s.starts_on
    AND (s.ends_on IS NULL OR date(m.played_at) <= s.ends_on)
"""


def _overlaps(conn, starts_on: str, ends_on: str | None, exclude_id: int | None) -> list[str]:
    """Names of existing seasons whose range would collide with this one.

    Overlap is refused rather than resolved. Two seasons covering the same day
    would make "which season is this match in" depend on row order, and the
    answer would silently change when a season was renamed.
    """
    rows = conn.execute(
        "SELECT id, name, starts_on, ends_on FROM seasons WHERE id IS NOT ?",
        (exclude_id,),
    ).fetchall()
    clashes = []
    for row in rows:
        other_end = row["ends_on"] or "9999-12-31"
        this_end = ends_on or "9999-12-31"
        if starts_on <= other_end and row["starts_on"] <= this_end:
            clashes.append(row["name"])
    return clashes


def _validate(starts_on: str, ends_on: str | None) -> None:
    if not starts_on:
        raise HTTPException(400, "A season needs a start date.")
    if ends_on and ends_on < starts_on:
        raise HTTPException(400, "A season cannot end before it starts.")


def reassign(conn) -> int:
    """Recompute `matches.season_id` from the dates. Returns rows changed.

    One statement for the whole table, run after any change to a season's
    range, so assignment can never drift from the ranges that define it.
    """
    cursor = conn.execute(f"""
        UPDATE matches AS m
        SET season_id = (SELECT s.id FROM seasons s WHERE {IN_SEASON})
        WHERE season_id IS NOT (SELECT s.id FROM seasons s WHERE {IN_SEASON})
    """)
    return cursor.rowcount


def _row(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "starts_on": row["starts_on"],
        "ends_on": row["ends_on"],
        "ongoing": row["ends_on"] is None,
        "notes": row["notes"],
        "matches": row["matches"] if "matches" in row.keys() else None,
    }


@router.get("")
def list_seasons() -> list[dict]:
    """Newest first, each with how many matches landed in it."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT s.*, (SELECT COUNT(*) FROM matches m WHERE m.season_id = s.id) AS matches
            FROM seasons s
            ORDER BY s.starts_on DESC, s.id DESC
        """).fetchall()
        unassigned = conn.execute(
            "SELECT COUNT(*) AS n FROM matches WHERE season_id IS NULL").fetchone()["n"]

    out = [_row(r) for r in rows]
    if unassigned:
        # Surfaced as a pseudo-season so the UI can offer it as a filter and
        # the count is visible. id None is what the filter sends back.
        out.append({"id": None, "name": "Unassigned", "starts_on": None, "ends_on": None,
                    "ongoing": False, "notes": None, "matches": unassigned})
    return out


@router.post("")
def create_season(body: dict) -> dict:
    name = (body.get("name") or "").strip()
    starts_on = (body.get("starts_on") or "").strip()
    ends_on = (body.get("ends_on") or "").strip() or None
    if not name:
        raise HTTPException(400, "A season needs a name.")
    _validate(starts_on, ends_on)

    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM seasons WHERE name = ?", (name,)).fetchone():
            raise HTTPException(409, f"There is already a season called {name!r}.")
        clashes = _overlaps(conn, starts_on, ends_on, None)
        if clashes:
            raise HTTPException(
                409, f"Those dates overlap {', '.join(clashes)}. "
                     f"Seasons may not cover the same day twice.")
        cursor = conn.execute(
            "INSERT INTO seasons (name, starts_on, ends_on, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, starts_on, ends_on, (body.get("notes") or "").strip() or None, utcnow()),
        )
        season_id = int(cursor.lastrowid)
        moved = reassign(conn)
        conn.commit()
        row = conn.execute("""
            SELECT s.*, (SELECT COUNT(*) FROM matches m WHERE m.season_id = s.id) AS matches
            FROM seasons s WHERE s.id = ?""", (season_id,)).fetchone()
    return {**_row(row), "matches_reassigned": moved}


@router.patch("/{season_id}")
def update_season(season_id: int, body: dict) -> dict:
    with get_conn() as conn:
        current = conn.execute("SELECT * FROM seasons WHERE id = ?", (season_id,)).fetchone()
        if current is None:
            raise HTTPException(404, f"No season {season_id}.")

        name = (body.get("name") or current["name"]).strip()
        starts_on = (body.get("starts_on") or current["starts_on"]).strip()
        if "ends_on" in body:
            ends_on = (body.get("ends_on") or "").strip() or None
        else:
            ends_on = current["ends_on"]
        _validate(starts_on, ends_on)

        duplicate = conn.execute(
            "SELECT 1 FROM seasons WHERE name = ? AND id != ?", (name, season_id)).fetchone()
        if duplicate:
            raise HTTPException(409, f"There is already a season called {name!r}.")
        clashes = _overlaps(conn, starts_on, ends_on, season_id)
        if clashes:
            raise HTTPException(
                409, f"Those dates overlap {', '.join(clashes)}.")

        conn.execute(
            "UPDATE seasons SET name = ?, starts_on = ?, ends_on = ?, notes = ? WHERE id = ?",
            (name, starts_on, ends_on,
             (body.get("notes", current["notes"]) or "").strip() or None, season_id),
        )
        moved = reassign(conn)
        conn.commit()
        row = conn.execute("""
            SELECT s.*, (SELECT COUNT(*) FROM matches m WHERE m.season_id = s.id) AS matches
            FROM seasons s WHERE s.id = ?""", (season_id,)).fetchone()
    return {**_row(row), "matches_reassigned": moved}


@router.delete("/{season_id}")
def delete_season(season_id: int) -> dict:
    """Delete a season. Its matches survive, unassigned."""
    with get_conn() as conn:
        released = conn.execute(
            "SELECT COUNT(*) AS n FROM matches WHERE season_id = ?", (season_id,)).fetchone()["n"]
        cursor = conn.execute("DELETE FROM seasons WHERE id = ?", (season_id,))
        if cursor.rowcount == 0:
            raise HTTPException(404, f"No season {season_id}.")
        # The foreign key already nulled them; recompute in case the deleted
        # range overlapped nothing and another season now claims them.
        reassign(conn)
        conn.commit()
    return {"ok": True, "matches_released": released}


@router.post("/reassign")
def reassign_all() -> dict:
    """Recompute every match's season from the dates.

    Not needed in normal use — every path that can change the answer already
    calls this. It exists for a database edited outside the app, which the
    whole design invites by keeping the file readable in any SQLite browser.
    """
    with get_conn() as conn:
        moved = reassign(conn)
        conn.commit()
        unassigned = conn.execute(
            "SELECT COUNT(*) AS n FROM matches WHERE season_id IS NULL").fetchone()["n"]
    return {"matches_reassigned": moved, "unassigned": unassigned}
