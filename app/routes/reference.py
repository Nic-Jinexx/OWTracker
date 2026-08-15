"""Seeded lookup data: heroes, maps, ranks, and the tag vocabulary.

Also the one window onto the *learned* hero portrait library. Everything else
here is fixed seed data the operator cannot change; `hero_portraits` is written
by every commit, so it needs somewhere to be looked at and something to undo it
with. A library that grows silently and can only grow is a library that gets one
mis-clicked dropdown and keeps it forever.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db import get_conn
from ..extract import heroes as heroes_module

router = APIRouter(prefix="/api", tags=["reference"])


@router.get("/reference")
def reference() -> dict:
    """Everything the frontend needs to render pickers, in one call."""
    with get_conn() as conn:
        heroes = [dict(r) for r in conn.execute(
            "SELECT id, name, role FROM heroes ORDER BY role, name"
        )]
        maps = [dict(r) for r in conn.execute(
            "SELECT id, name, mode FROM maps ORDER BY mode, name"
        )]
        ranks = [dict(r) for r in conn.execute(
            "SELECT id, tier, division, ordinal, name FROM ranks ORDER BY ordinal"
        )]
        tags = [dict(r) for r in conn.execute(
            "SELECT code, tag_set, color, label FROM tags ORDER BY sort_order"
        )]

    modes: dict[str, list[dict]] = {}
    for entry in maps:
        modes.setdefault(entry["mode"], []).append(entry)

    return {
        "heroes": heroes,
        "heroes_by_role": {
            role: [h for h in heroes if h["role"] == role]
            for role in ("tank", "damage", "support")
        },
        "maps": maps,
        "maps_by_mode": modes,
        "ranks": ranks,
        "tags": tags,
        # The dots render as two fixed rows, so hand them over already split.
        "tags_by_set": {
            name: [t for t in tags if t["tag_set"] == name] for name in ("role", "free")
        },
    }


@router.get("/reference/hero-portraits")
def hero_portraits() -> dict:
    """What the portrait matcher has been taught, and what it shipped knowing.

    `shipped` is listed separately and is not deletable: it is a file inside the
    installation, not this operator's data, and a backup of `owtracker.db` would
    not bring it back.
    """
    shipped = heroes_module.load_hero_library()
    with get_conn() as conn:
        learned = heroes_module.learned_counts(conn)
        total = conn.execute("SELECT COUNT(*) AS n FROM hero_portraits").fetchone()["n"]
        heroes_total = conn.execute("SELECT COUNT(*) AS n FROM heroes").fetchone()["n"]

    known = {row["hero_name"] for row in learned} | set(shipped)
    return {
        "learned": learned,
        "learned_portraits": total,
        "shipped": sorted(shipped),
        "heroes_known": len(known),
        "heroes_total": heroes_total,
    }


@router.delete("/reference/hero-portraits/{hero_id}")
def forget_hero_portraits(hero_id: int) -> dict:
    """Forget everything learned about one hero's portrait.

    Per hero rather than per hash. A hash means nothing to the operator — the
    thing they can actually judge is "the matcher keeps calling this Reinhardt
    and it is not Reinhardt", and the fix for that is to drop Reinhardt's
    learned portraits and name it again next match.

    Committed matches are untouched. This is the recognition library, not the
    history: forgetting a portrait does not un-record the games it identified,
    which are the operator's own confirmed answers.
    """
    with get_conn() as conn:
        hero = conn.execute("SELECT name FROM heroes WHERE id = ?", (hero_id,)).fetchone()
        if hero is None:
            raise HTTPException(404, f"No hero with id {hero_id}")
        cursor = conn.execute("DELETE FROM hero_portraits WHERE hero_id = ?", (hero_id,))
        conn.commit()
    return {"hero": hero["name"], "forgotten": cursor.rowcount}
