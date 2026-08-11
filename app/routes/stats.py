"""Aggregate endpoints.

Thin: the queries live in `app/analysis.py`, which computes from any subject's
point of view. These routes choose a subject, pass the filters through, and
shape the response.

Every filter narrows to matches where the *subject's* row matches the criterion
— "win rate on Ana" means matches they played Ana, not matches anyone did.
Sample sizes travel with every rate, for the reason in players.py.

Plain defaults rather than `Query(...)` throughout, so these functions stay
callable directly from tests and scripts and not only through FastAPI.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import analysis
from ..analysis import Filters, Subject
from ..db import get_conn

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _filters(hero_id, map_id, mode, role, teammate_id, opponent_id,
             rank_min, rank_max, date_from, date_to, team_size=None,
             season_id=None) -> Filters:
    return Filters(hero_id=hero_id, map_id=map_id, mode=mode, role=role,
                   teammate_id=teammate_id, opponent_id=opponent_id,
                   rank_min=rank_min, rank_max=rank_max,
                   date_from=date_from, date_to=date_to, team_size=team_size,
                   season_id=season_id)


def _subject(conn, player_id: int | None) -> tuple[Subject, dict]:
    """Resolve the subject and describe them for the response header."""
    if player_id is None:
        name = conn.execute(
            "SELECT value FROM settings WHERE key = 'my_display_name'").fetchone()
        return Subject(), {"player_id": None, "is_me": True,
                           "display_name": (name["value"] if name else "") or "You",
                           "notes": None, "tags": [], "games_seen": None,
                           "first_seen": None, "last_seen": None}

    row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Player {player_id} not found")
    tags = [r["tag_code"] for r in conn.execute(
        "SELECT pt.tag_code FROM player_tags pt JOIN tags t ON t.code = pt.tag_code "
        "WHERE pt.player_id = ? ORDER BY t.sort_order", (player_id,))]
    is_me = conn.execute(
        "SELECT 1 FROM match_players WHERE player_id = ? AND is_me = 1 LIMIT 1",
        (player_id,)).fetchone() is not None
    return Subject(player_id=player_id), {
        "player_id": player_id, "is_me": is_me,
        "display_name": row["display_name"], "notes": row["notes"], "tags": tags,
        "games_seen": row["games_seen"],
        "first_seen": row["first_seen"], "last_seen": row["last_seen"],
    }


@router.get("")
def aggregates(
    hero_id: int | None = None,
    map_id: int | None = None,
    mode: str | None = None,
    role: str | None = None,
    teammate_id: int | None = None,
    opponent_id: int | None = None,
    # Rank bounds are `ranks.ordinal` values (1 = Bronze 5 … 40 = Champion 1).
    rank_min: int | None = None,
    rank_max: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    team_size: int | None = None,
    season_id: int | str | None = None,
) -> dict:
    """The operator's aggregates. Response shape unchanged since v0.1.0."""
    filters = _filters(hero_id, map_id, mode, role, teammate_id, opponent_id,
                       rank_min, rank_max, date_from, date_to, team_size, season_id)
    subject = Subject()
    with get_conn() as conn:
        return {
            "overall": analysis.overall(conn, subject, filters),
            "by_map": analysis.by_map(conn, subject, filters),
            "by_hero": analysis.by_hero(conn, subject, filters),
            "by_mode": analysis.by_mode(conn, subject, filters),
            "by_teammate": analysis.by_ally(conn, subject, filters),
            "by_opponent_hero": analysis.by_opponent_hero(conn, subject, filters),
            "my_averages": analysis.averages(conn, subject, filters),
        }


# How many allies/opponents ride along in the overview before the caller has to
# ask for the full list. Enough to fill a panel and its "see more" preview.
ROSTER_PREVIEW = 50


@router.get("/overview")
def overview(
    player_id: int | None = None,
    hero_id: int | None = None,
    map_id: int | None = None,
    mode: str | None = None,
    role: str | None = None,
    teammate_id: int | None = None,
    opponent_id: int | None = None,
    rank_min: int | None = None,
    rank_max: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    team_size: int | None = None,
    season_id: int | str | None = None,
    top: int = 5,
    min_games: int = analysis.RELIABLE_MIN_GAMES,
    rank: str = "score",
) -> dict:
    """Everything about one subject, in one call.

    Deliberately not selective: this is a local app over a few thousand rows, so
    the whole set of aggregates is a handful of milliseconds. An `include=`
    param would be complexity bought for a problem that does not exist.
    """
    filters = _filters(hero_id, map_id, mode, role, teammate_id, opponent_id,
                       rank_min, rank_max, date_from, date_to, team_size, season_id)
    with get_conn() as conn:
        subject, who = _subject(conn, player_id)
        allies = analysis.by_ally(conn, subject, filters)
        opponents = analysis.by_opponent(conn, subject, filters)
        comps = analysis.comp_shapes(conn, subject, filters)
        return {
            "subject": who,
            "filters_applied": filters.applied(),
            "totals": analysis.totals(conn, subject, filters),
            "overall": analysis.overall(conn, subject, filters),
            "streaks": analysis.streaks(conn, subject, filters),
            "by_map": analysis.by_map(conn, subject, filters),
            "by_hero": analysis.by_hero(conn, subject, filters),
            "by_role": analysis.by_role(conn, subject, filters),
            "by_mode": analysis.by_mode(conn, subject, filters),
            "by_team_size": analysis.by_team_size(conn, subject, filters),
            # One list. "Won with most" and "lost with most" are the same data
            # sorted two ways, and shipping it twice would just risk drift.
            "by_ally": allies[:ROSTER_PREVIEW],
            "by_ally_total": len(allies),
            "by_opponent": opponents[:ROSTER_PREVIEW],
            "by_opponent_total": len(opponents),
            "by_ally_hero": analysis.by_ally_hero(conn, subject, filters),
            "by_opponent_hero": analysis.by_opponent_hero(conn, subject, filters),
            "bans": analysis.bans(conn, subject, filters),
            "comps": comps,
            "averages": analysis.averages(conn, subject, filters),
            "averages_by_role": analysis.averages_by_role(conn, subject, filters),
            "highlights": analysis.highlights(conn, subject, filters, top=top,
                                              min_games=min_games, rank=rank),
        }


@router.get("/top")
def top_performances(
    player_id: int | None = None,
    hero_id: int | None = None,
    map_id: int | None = None,
    mode: str | None = None,
    role: str | None = None,
    teammate_id: int | None = None,
    opponent_id: int | None = None,
    rank_min: int | None = None,
    rank_max: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    team_size: int | None = None,
    season_id: int | str | None = None,
    top: int = 5,
    min_games: int = analysis.RELIABLE_MIN_GAMES,
    rank: str = "score",
) -> dict:
    filters = _filters(hero_id, map_id, mode, role, teammate_id, opponent_id,
                       rank_min, rank_max, date_from, date_to, team_size, season_id)
    with get_conn() as conn:
        subject, who = _subject(conn, player_id)
        return {"subject": who,
                **analysis.highlights(conn, subject, filters, top=top,
                                      min_games=min_games, rank=rank)}


@router.get("/comps")
def comps(
    player_id: int | None = None,
    map_id: int | None = None,
    mode: str | None = None,
    team_size: int | None = None,
    season_id: int | str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_games: int = 1,
    include_pairs: bool = True,
    exact: bool = False,
) -> dict:
    """Team composition, three ways.

    Role shape is the headline because it has few enough buckets to reach a
    usable sample. Pairs repeat often enough to be signal. Exact lineups are
    mostly one game each and are labelled as such rather than ranked.
    """
    filters = _filters(None, map_id, mode, None, None, None,
                       None, None, date_from, date_to, team_size, season_id)
    with get_conn() as conn:
        subject, who = _subject(conn, player_id)
        result = {"subject": who, **analysis.comp_shapes(conn, subject, filters)}
        result["pairs"] = (analysis.hero_pairs(conn, subject, filters, min_games=2)
                           if include_pairs else [])
        result["exact"] = (analysis.exact_comps(conn, subject, filters, min_games)
                           if exact else None)
        return result


@router.get("/crosstab")
def crosstab(
    player_id: int | None = None,
    mode: str | None = None,
    team_size: int | None = None,
    season_id: int | str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_games: int = 1,
) -> dict:
    """Hero x map. Kept out of the overview because it is the biggest payload
    and belongs behind a deliberate click."""
    filters = _filters(None, None, mode, None, None, None,
                       None, None, date_from, date_to, team_size, season_id)
    with get_conn() as conn:
        subject, who = _subject(conn, player_id)
        return {"subject": who,
                **analysis.map_hero_crosstab(conn, subject, filters, min_games)}


@router.get("/allies")
def allies(player_id: int | None = None, min_games: int = 1,
           limit: int | None = None) -> dict:
    with get_conn() as conn:
        subject, who = _subject(conn, player_id)
        return {"subject": who,
                "allies": analysis.by_ally(conn, subject, Filters(), min_games, limit),
                "opponents": analysis.by_opponent(conn, subject, Filters(),
                                                  min_games, limit)}


@router.get("/trend")
def trend(
    player_id: int | None = None,
    mode: str | None = None,
    map_id: int | None = None,
    team_size: int | None = None,
    season_id: int | str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    window: int = 10,
) -> dict:
    """Win rate over time, plus one series per endorsement dot.

    Deliberately not filterable by hero or role: those are properties of a
    single row, and a trend line drawn only over the games where the subject
    played Ana is a different chart with the same shape and a much smaller
    sample. Map, mode, format and season all narrow the *match set*, which is
    what a timeline is about.
    """
    filters = _filters(None, map_id, mode, None, None, None,
                       None, None, date_from, date_to, team_size, season_id)
    with get_conn() as conn:
        subject, who = _subject(conn, player_id)
        return {"subject": who,
                **analysis.trend(conn, subject, filters, window=window)}
