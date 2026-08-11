"""Seeded lookup data: heroes, maps, ranks, and the tag vocabulary."""

from __future__ import annotations

from fastapi import APIRouter

from ..db import get_conn

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
