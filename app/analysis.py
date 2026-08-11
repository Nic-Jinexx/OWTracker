"""Aggregates, computed from any player's point of view.

The old `stats.py` could only answer questions about the operator: every query
hardcoded `me.is_me = 1`. This module generalises that to a **subject** — the
operator by default, any player on request — without a second copy of every
query, because each aggregate already expresses maps, heroes, allies and
opponents *relative to the subject's row and team*.

Two things make that generalisation non-trivial, and both are silent if wrong.

**Results are stored from the operator's perspective.** `matches.result` says
what *I* did. A player on the enemy team of a match I lost won that match, so
every tally has to remap the outcome relative to the subject's side. For
`is_me = 1` the subject is always on `ally`, so `OUTCOME` provably collapses to
`m.result` and this module is a strict superset of the old behaviour — which is
exactly what the pre-existing `tests/test_analysis.py` proves by still passing.

**"Teammate" is not "not me".** The old `by_teammate` excluded the operator's
row with `is_me = 0`, which is right only while the subject *is* the operator.
For anyone else the operator is a perfectly ordinary teammate. Allies are
therefore "same team, different row", never "same team, not me".

Pure `sqlite3`; no FastAPI import. The routes are the thin part.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict

# A rate from fewer than five games is not a signal. Everything that renders a
# percentage carries `reliable` so the UI can grey it, and the leaderboards gate
# on it outright.
RELIABLE_MIN_GAMES = 5

# 95% two-sided, for the Wilson interval.
Z = 1.959963984540054

# Whose result is it? Draws are draws for everyone; otherwise the subject's
# outcome is the operator's outcome only when they were on the operator's side.
OUTCOME = """CASE WHEN m.result = 'draw'    THEN 'draw'
                  WHEN subj.team = 'ally'   THEN m.result
                  WHEN m.result = 'win'     THEN 'loss'
                  ELSE 'win' END"""

TALLY = (
    "COUNT(*) AS games, "
    "SUM(CASE WHEN outcome = 'win'  THEN 1 ELSE 0 END) AS wins, "
    "SUM(CASE WHEN outcome = 'loss' THEN 1 ELSE 0 END) AS losses, "
    "SUM(CASE WHEN outcome = 'draw' THEN 1 ELSE 0 END) AS draws"
)


@dataclass(frozen=True)
class Subject:
    """Whose statistics these are. `player_id=None` means the operator."""
    player_id: int | None = None

    def clause(self) -> tuple[str, list]:
        if self.player_id is None:
            return "subj.is_me = 1", []
        return "subj.player_id = ?", [self.player_id]


@dataclass(frozen=True)
class Filters:
    hero_id: int | None = None
    map_id: int | None = None
    mode: str | None = None
    role: str | None = None
    teammate_id: int | None = None
    opponent_id: int | None = None
    rank_min: int | None = None
    rank_max: int | None = None
    date_from: str | None = None
    date_to: str | None = None
    team_size: int | None = None

    def applied(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _where(subject: Subject, filters: Filters) -> tuple[str, list]:
    """The WHERE clause for the subject's match set.

    Every hero/role clause is about the *subject's* row, so "win rate on Ana"
    means matches the subject played Ana — not matches anyone did.
    """
    clause, params = subject.clause()
    clauses = [clause]

    if filters.hero_id is not None:
        clauses.append("subj.hero_id = ?")
        params.append(filters.hero_id)
    if filters.role is not None:
        clauses.append("COALESCE(subj.role, sh.role) = ?")
        params.append(filters.role)
    if filters.map_id is not None:
        clauses.append("m.map_id = ?")
        params.append(filters.map_id)
    if filters.mode is not None:
        clauses.append(
            "COALESCE(m.mode, (SELECT mode FROM maps WHERE maps.id = m.map_id)) = ?")
        params.append(filters.mode)
    if filters.team_size is not None:
        clauses.append("m.team_size = ?")
        params.append(filters.team_size)
    if filters.date_from:
        clauses.append("m.played_at >= ?")
        params.append(filters.date_from)
    if filters.date_to:
        clauses.append("m.played_at <= ?")
        params.append(filters.date_to)
    if filters.rank_min is not None:
        clauses.append("(SELECT ordinal FROM ranks WHERE ranks.id = m.rank_range_low) >= ?")
        params.append(filters.rank_min)
    if filters.rank_max is not None:
        clauses.append("(SELECT ordinal FROM ranks WHERE ranks.id = m.rank_range_high) <= ?")
        params.append(filters.rank_max)
    if filters.teammate_id is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM match_players t WHERE t.match_id = m.id "
            "AND t.player_id = ? AND t.id != subj.id AND t.team = subj.team)")
        params.append(filters.teammate_id)
    if filters.opponent_id is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM match_players o WHERE o.match_id = m.id "
            "AND o.player_id = ? AND o.team != subj.team)")
        params.append(filters.opponent_id)

    return " AND ".join(clauses), params


def subject_matches(subject: Subject, filters: Filters) -> tuple[str, list]:
    """`WITH sm AS (...)` — the subject's match set, once.

    Every aggregate below selects from `sm`, so the filter logic and the
    outcome remap exist in exactly one place.

    `SELECT DISTINCT` guards a real case: the same `player_id` can legitimately
    hold two rows in one match if a nameplate matched twice, and without it that
    match would be counted twice in every tally.
    """
    where, params = _where(subject, filters)
    sql = f"""
    WITH sm AS (
        SELECT DISTINCT
            m.id         AS match_id,
            m.played_at  AS played_at,
            m.map_id     AS map_id,
            m.mode       AS match_mode,
            m.team_size  AS team_size,
            subj.team    AS team,
            subj.id      AS subject_row_id,
            subj.hero_id AS hero_id,
            COALESCE(subj.role, sh.role) AS role,
            {OUTCOME} AS outcome
        FROM matches m
        JOIN match_players subj ON subj.match_id = m.id
        LEFT JOIN heroes sh ON sh.id = subj.hero_id
        WHERE {where}
    )"""
    return sql, params


def shape(row, min_games: int = RELIABLE_MIN_GAMES) -> dict:
    """Add `win_rate` and `reliable` to a tally row."""
    row = dict(row)
    games = row.get("games") or 0
    wins = row.get("wins") or 0
    row["games"] = games
    row["wins"] = wins
    row["losses"] = row.get("losses") or 0
    row["draws"] = row.get("draws") or 0
    row["win_rate"] = (wins / games) if games else None
    row["reliable"] = games >= min_games
    return row


def _rows(conn, cte: str, params: list, body: str, extra: list | None = None) -> list[dict]:
    return [shape(r) for r in conn.execute(cte + body, params + (extra or []))]


def _one(conn, cte: str, params: list, body: str) -> dict:
    row = conn.execute(cte + body, params).fetchone()
    return shape(row if row else {})


# ---------------------------------------------------------------- aggregates


def overall(conn, subject: Subject, filters: Filters) -> dict:
    cte, params = subject_matches(subject, filters)
    return _one(conn, cte, params, f" SELECT {TALLY} FROM sm")


def totals(conn, subject: Subject, filters: Filters) -> dict:
    """Coverage counters.

    `by_map` inner-joins `maps`, so its games undercount whenever a match has no
    map recorded. Reporting the gap is better than letting the discrepancy sit
    there invisibly and be read as a bug in the arithmetic.
    """
    cte, params = subject_matches(subject, filters)
    row = conn.execute(cte + """
        SELECT COUNT(*) AS matches,
               SUM(CASE WHEN map_id  IS NOT NULL THEN 1 ELSE 0 END) AS matches_with_map,
               SUM(CASE WHEN hero_id IS NOT NULL THEN 1 ELSE 0 END) AS matches_with_hero,
               MIN(played_at) AS first_played,
               MAX(played_at) AS last_played,
               SUM(CASE WHEN EXISTS (SELECT 1 FROM match_bans b
                                     WHERE b.match_id = sm.match_id)
                        THEN 1 ELSE 0 END) AS matches_with_bans,
               SUM(CASE WHEN EXISTS (SELECT 1 FROM match_players s
                                     WHERE s.id = sm.subject_row_id
                                       AND s.eliminations IS NOT NULL)
                        THEN 1 ELSE 0 END) AS matches_with_stats
        FROM sm""", params).fetchone()
    return {k: (row[k] or 0) if k.startswith("matches") else row[k] for k in row.keys()}


def by_map(conn, subject, filters) -> list[dict]:
    cte, params = subject_matches(subject, filters)
    return _rows(conn, cte, params, f"""
        SELECT maps.id AS id, maps.name AS name, maps.mode AS mode, {TALLY}
        FROM sm JOIN maps ON maps.id = sm.map_id
        GROUP BY maps.id ORDER BY games DESC, maps.name""")


def by_hero(conn, subject, filters) -> list[dict]:
    """The subject's own hero."""
    cte, params = subject_matches(subject, filters)
    return _rows(conn, cte, params, f"""
        SELECT h.id AS id, h.name AS name, h.role AS role, {TALLY}
        FROM sm JOIN heroes h ON h.id = sm.hero_id
        GROUP BY h.id ORDER BY games DESC, h.name""")


def by_role(conn, subject, filters) -> list[dict]:
    cte, params = subject_matches(subject, filters)
    return _rows(conn, cte, params, f"""
        SELECT role AS name, {TALLY} FROM sm
        WHERE role IS NOT NULL GROUP BY role ORDER BY games DESC, role""")


def by_mode(conn, subject, filters) -> list[dict]:
    cte, params = subject_matches(subject, filters)
    # The match's own mode wins over the map's: a map can be played in a mode
    # other than its default, and the operator can say so.
    return _rows(conn, cte, params, f"""
        SELECT COALESCE(sm.match_mode, maps.mode) AS mode,
               COALESCE(sm.match_mode, maps.mode) AS name, {TALLY}
        FROM sm LEFT JOIN maps ON maps.id = sm.map_id
        WHERE COALESCE(sm.match_mode, maps.mode) IS NOT NULL
        GROUP BY COALESCE(sm.match_mode, maps.mode) ORDER BY games DESC""")


def by_team_size(conn, subject, filters) -> list[dict]:
    cte, params = subject_matches(subject, filters)
    return _rows(conn, cte, params, f"""
        SELECT team_size AS id, (team_size || 'v' || team_size) AS name, {TALLY}
        FROM sm WHERE team_size IS NOT NULL
        GROUP BY team_size ORDER BY games DESC""")


def by_ally(conn, subject, filters, min_games: int = 1, limit: int | None = None):
    """Who the subject plays *with*, and how it goes.

    `a.id != sm.subject_row_id` rather than `is_me = 0`: for any subject who is
    not the operator, the operator is an ally like anyone else.
    """
    cte, params = subject_matches(subject, filters)
    sql = f"""
        SELECT p.id AS id, p.display_name AS name, {TALLY}
        FROM sm
        JOIN match_players a ON a.match_id = sm.match_id
                            AND a.team     = sm.team
                            AND a.id      != sm.subject_row_id
                            AND a.player_id IS NOT NULL
        JOIN players p ON p.id = a.player_id
        GROUP BY p.id HAVING games >= ? ORDER BY games DESC, p.display_name"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return _rows(conn, cte, params, sql, [min_games])


def by_opponent(conn, subject, filters, min_games: int = 1, limit: int | None = None):
    cte, params = subject_matches(subject, filters)
    sql = f"""
        SELECT p.id AS id, p.display_name AS name, {TALLY}
        FROM sm
        JOIN match_players o ON o.match_id = sm.match_id
                            AND o.team    != sm.team
                            AND o.player_id IS NOT NULL
        JOIN players p ON p.id = o.player_id
        GROUP BY p.id HAVING games >= ? ORDER BY games DESC, p.display_name"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return _rows(conn, cte, params, sql, [min_games])


def by_ally_hero(conn, subject, filters) -> list[dict]:
    cte, params = subject_matches(subject, filters)
    return _rows(conn, cte, params, f"""
        SELECT h.id AS id, h.name AS name, h.role AS role, {TALLY}
        FROM sm
        JOIN match_players a ON a.match_id = sm.match_id AND a.team = sm.team
                            AND a.id != sm.subject_row_id
        JOIN heroes h ON h.id = a.hero_id
        GROUP BY h.id ORDER BY games DESC, h.name""")


def by_opponent_hero(conn, subject, filters) -> list[dict]:
    cte, params = subject_matches(subject, filters)
    return _rows(conn, cte, params, f"""
        SELECT h.id AS id, h.name AS name, h.role AS role, {TALLY}
        FROM sm
        JOIN (SELECT DISTINCT match_id, team, hero_id FROM match_players
              WHERE hero_id IS NOT NULL) o
             ON o.match_id = sm.match_id AND o.team != sm.team
        JOIN heroes h ON h.id = o.hero_id
        GROUP BY h.id ORDER BY games DESC, h.name""")


def bans(conn, subject, filters) -> dict:
    """Heroes banned in the subject's lobbies.

    The rate divides by matches that *have* ban data, not by every match —
    otherwise every ban rate reads low for a reason that has nothing to do with
    the game and everything to do with which screenshots got typed in.
    """
    cte, params = subject_matches(subject, filters)
    heroes = _rows(conn, cte, params, f"""
        SELECT h.id AS id, h.name AS name, h.role AS role, {TALLY}
        FROM sm
        JOIN (SELECT DISTINCT match_id, hero_id FROM match_bans) b
             ON b.match_id = sm.match_id
        JOIN heroes h ON h.id = b.hero_id
        GROUP BY h.id ORDER BY games DESC, h.name""")
    with_bans = conn.execute(cte + """
        SELECT COUNT(*) AS n FROM sm
        WHERE EXISTS (SELECT 1 FROM match_bans b WHERE b.match_id = sm.match_id)""",
        params).fetchone()["n"] or 0
    for hero in heroes:
        hero["bans"] = hero["games"]
        hero["ban_rate"] = (hero["games"] / with_bans) if with_bans else None
    return {"heroes": heroes, "matches_with_bans": with_bans}


# A roster is only a composition if every seat is accounted for. Blank draft
# rows are never committed, so an unrecorded player leaves *fewer rows* rather
# than a row full of NULLs — completeness has to be measured against the
# match's own team_size, not against how many NULLs came back.
_COMP_ROSTER = """
    , shapes AS (
        SELECT sm.match_id, sm.outcome, sm.team_size,
               SUM(CASE WHEN COALESCE(mp.role, h.role) = 'tank'    THEN 1 ELSE 0 END) AS tanks,
               SUM(CASE WHEN COALESCE(mp.role, h.role) = 'damage'  THEN 1 ELSE 0 END) AS damage,
               SUM(CASE WHEN COALESCE(mp.role, h.role) = 'support' THEN 1 ELSE 0 END) AS supports,
               SUM(CASE WHEN COALESCE(mp.role, h.role) IS NULL     THEN 1 ELSE 0 END) AS unknown,
               COUNT(*) AS rostered
        FROM sm
        JOIN match_players mp ON mp.match_id = sm.match_id AND mp.team = sm.team
        LEFT JOIN heroes h ON h.id = mp.hero_id
        GROUP BY sm.match_id
    )"""
# Complete = nobody's role unknown, and as many seats filled as the match says.
_COMP_COMPLETE = "unknown = 0 AND (team_size IS NULL OR rostered = team_size)"


def comp_shapes(conn, subject, filters) -> dict:
    """Team composition as a role shape: `2-2-2`, `3-1-2`.

    Open Queue is real — `endgame_fullscreen_2329x1312.png` is 3 tank / 1 damage
    / 2 support — so role counts are described, never validated.

    An incomplete roster is excluded and counted rather than labelled with a
    shape that is quietly missing a player: `1-2-1` for a five-player team is
    not a composition, it is four rows and a gap. The key concatenates three
    counts rather than using `group_concat`, whose ordering is unspecified.
    """
    cte, params = subject_matches(subject, filters)
    rows = _rows(conn, cte, params, _COMP_ROSTER + f"""
    SELECT tanks || '-' || damage || '-' || supports AS name,
           tanks, damage, supports, {TALLY}
    FROM shapes WHERE {_COMP_COMPLETE}
    GROUP BY name ORDER BY games DESC, name""")
    unclassified = conn.execute(
        cte + _COMP_ROSTER + f" SELECT COUNT(*) AS n FROM shapes WHERE NOT ({_COMP_COMPLETE})",
        params).fetchone()["n"] or 0
    return {"shapes": rows, "unclassified_games": unclassified}


def hero_pairs(conn, subject, filters, min_games: int = 2) -> list[dict]:
    """Allied hero duos.

    `b.hero_id > a.hero_id` counts each pair once; the label sorts the two names
    alphabetically so the same duo always reads the same way, regardless of
    which hero happens to hold the lower id.
    """
    cte, params = subject_matches(subject, filters)
    return _rows(conn, cte, params, f"""
        SELECT MIN(ha.name, hb.name) || ' + ' || MAX(ha.name, hb.name) AS name,
               ha.id AS hero_a, hb.id AS hero_b, {TALLY}
        FROM sm
        JOIN match_players a ON a.match_id = sm.match_id AND a.team = sm.team
                            AND a.hero_id IS NOT NULL
        JOIN match_players b ON b.match_id = sm.match_id AND b.team = sm.team
                            AND b.hero_id IS NOT NULL AND b.hero_id > a.hero_id
        JOIN heroes ha ON ha.id = a.hero_id
        JOIN heroes hb ON hb.id = b.hero_id
        GROUP BY a.hero_id, b.hero_id
        HAVING games >= ? ORDER BY games DESC, name""", [min_games])


def exact_comps(conn, subject, filters, min_games: int = 1) -> list[dict]:
    """The literal set of allied heroes.

    Almost every row will be a single game for a long time — that is the nature
    of the question, not a defect — so this lives behind "see more" and is
    labelled honestly rather than dressed up as a ranking.

    A lineup only counts when every seat has a hero, measured against the
    match's `team_size`: three named heroes out of five is a gap in the data,
    not a three-hero composition.
    """
    cte, params = subject_matches(subject, filters)
    return _rows(conn, cte, params, f"""
    , lineups AS (
        SELECT sm.match_id, sm.outcome, sm.team_size,
               (SELECT GROUP_CONCAT(hh.name, ' · ')
                FROM match_players mp2
                JOIN heroes hh ON hh.id = mp2.hero_id
                WHERE mp2.match_id = sm.match_id AND mp2.team = sm.team) AS name,
               (SELECT COUNT(*) FROM match_players mp3
                WHERE mp3.match_id = sm.match_id AND mp3.team = sm.team
                  AND mp3.hero_id IS NOT NULL) AS named
        FROM sm
    )
    SELECT name, {TALLY} FROM lineups
    WHERE name IS NOT NULL AND (team_size IS NULL OR named = team_size)
    GROUP BY name HAVING games >= ? ORDER BY games DESC, name""", [min_games])


def map_hero_crosstab(conn, subject, filters, min_games: int = 1) -> dict:
    """Which hero works on which map. A flat cell list, not a dense matrix —
    20 maps x 44 heroes is 880 cells of which a handful are ever non-zero."""
    cte, params = subject_matches(subject, filters)
    cells = _rows(conn, cte, params, f"""
        SELECT maps.id AS map_id, maps.name AS map_name,
               h.id AS hero_id, h.name AS hero_name, maps.name AS name, {TALLY}
        FROM sm
        JOIN maps ON maps.id = sm.map_id
        JOIN heroes h ON h.id = sm.hero_id
        GROUP BY maps.id, h.id HAVING games >= ?
        ORDER BY games DESC, maps.name, h.name""", [min_games])
    return {
        "cells": cells,
        "maps": sorted({(c["map_id"], c["map_name"]) for c in cells}, key=lambda t: t[1]),
        "heroes": sorted({(c["hero_id"], c["hero_name"]) for c in cells}, key=lambda t: t[1]),
    }


def streaks(conn, subject, filters) -> dict:
    """Current and longest runs.

    A draw breaks a streak: it neither extends a run nor starts the opposite
    one. A Python loop over a few thousand ordered rows rather than a
    gaps-and-islands window query, because this stays readable.
    """
    cte, params = subject_matches(subject, filters)
    rows = list(conn.execute(
        cte + " SELECT match_id, played_at, outcome FROM sm ORDER BY played_at, match_id",
        params))

    longest = {"win": {"length": 0, "started_at": None, "ended_at": None},
               "loss": {"length": 0, "started_at": None, "ended_at": None}}
    run_kind, run_len, run_start = None, 0, None
    for row in rows:
        outcome = row["outcome"]
        if outcome == run_kind:
            run_len += 1
        else:
            run_kind, run_len, run_start = outcome, 1, row["played_at"]
        if outcome in longest and run_len > longest[outcome]["length"]:
            longest[outcome] = {"length": run_len, "started_at": run_start,
                                "ended_at": row["played_at"]}
    current = {"outcome": run_kind, "length": run_len} if rows else {"outcome": None,
                                                                     "length": 0}
    return {"current": current, "longest_win": longest["win"],
            "longest_loss": longest["loss"]}


AVERAGE_COLUMNS = ("eliminations", "assists", "deaths", "damage", "healing", "mitigation")


def averages(conn, subject, filters) -> dict | None:
    """The subject's own average line.

    `rows_with_stats` travels with it: enemy rows routinely have no statistics
    at all, so a selected enemy-side player must read as "nothing recorded"
    rather than as a row of zeroes.
    """
    cte, params = subject_matches(subject, filters)
    cols = ", ".join(f"AVG(s.{c}) AS {c}" for c in AVERAGE_COLUMNS)
    row = conn.execute(cte + f"""
        SELECT COUNT(*) AS rows_with_stats, {cols}
        FROM sm JOIN match_players s ON s.id = sm.subject_row_id
        WHERE s.eliminations IS NOT NULL""", params).fetchone()
    return dict(row) if row and row["rows_with_stats"] else None


def averages_by_role(conn, subject, filters) -> list[dict]:
    """Averaging healing across a tank game and a support game produces a
    number that means nothing. Split it."""
    cte, params = subject_matches(subject, filters)
    cols = ", ".join(f"AVG(s.{c}) AS {c}" for c in AVERAGE_COLUMNS)
    return [dict(r) for r in conn.execute(cte + f"""
        SELECT sm.role AS role, COUNT(*) AS rows_with_stats, {cols}
        FROM sm JOIN match_players s ON s.id = sm.subject_row_id
        WHERE s.eliminations IS NOT NULL AND sm.role IS NOT NULL
        GROUP BY sm.role ORDER BY rows_with_stats DESC""", params)]


# ------------------------------------------------------------------- ranking


def wilson_lower(wins: int, losses: int, z: float = Z) -> float:
    """Lower bound of the Wilson score interval.

    Ranking by raw win rate puts every 2-0 above every 27-13, which is the
    opposite of useful. Wilson asks "how good is this at worst, given how little
    I know", so 2-0 scores 0.342 and 27-13 scores 0.520 — and because the answer
    is a probability, a map, a hero and a teammate can share one leaderboard.

    Draws are excluded from the denominator: they are neither evidence for nor
    against, and scoring one as half a win would hide that distinction.
    """
    n = wins + losses
    if n <= 0:
        return 0.0
    phat = wins / n
    denominator = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denominator)


def wilson_upper(wins: int, losses: int, z: float = Z) -> float:
    n = wins + losses
    if n <= 0:
        return 1.0
    phat = wins / n
    denominator = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return min(1.0, (centre + margin) / denominator)


# What a highlight can be about. `by_ally` is included because "who do I win
# with" is one of the questions this whole page exists to answer.
HIGHLIGHT_KINDS = {
    "map": by_map,
    "hero": by_hero,
    "role": by_role,
    "ally": by_ally,
    "comp": None,        # filled below; comp_shapes returns a dict, not a list
}


def highlights(conn, subject: Subject, filters: Filters, top: int = 5,
               min_games: int = RELIABLE_MIN_GAMES, rank: str = "score",
               kinds: tuple[str, ...] = ("map", "hero", "role", "ally", "comp")) -> dict:
    """Best and worst performances, plus the ban frequency chart.

    Wilson makes the *ordering* fair across wildly different sample sizes; the
    `min_games` gate decides what is worth showing at all. Two knobs, two jobs —
    a 2-0 hero is ranked honestly by Wilson and still excluded by the gate.

    `delta` is against the subject's own overall rate, because a 55% map is a
    bad map for a 60% player. Sorting by `delta` instead of `score` answers
    "where am I unusually good", which is a different question from "where am I
    good".
    """
    baseline_row = overall(conn, subject, filters)
    baseline = baseline_row["win_rate"]

    entries: list[dict] = []
    for kind in kinds:
        if kind == "comp":
            rows = comp_shapes(conn, subject, filters)["shapes"]
        elif kind in HIGHLIGHT_KINDS and HIGHLIGHT_KINDS[kind] is not None:
            rows = HIGHLIGHT_KINDS[kind](conn, subject, filters)
        else:
            continue
        for row in rows:
            if row["games"] < min_games:
                continue
            wins, losses = row["wins"], row["losses"]
            score = wilson_lower(wins, losses)
            entries.append({
                **row,
                "kind": kind,
                "decisive": wins + losses,
                "score": score,
                "ceiling": wilson_upper(wins, losses),
                "delta": (score - baseline) if baseline is not None else None,
            })

    key = (lambda e: (e["delta"] if e["delta"] is not None else e["score"]))\
        if rank == "delta" else (lambda e: e["score"])
    best = sorted(entries, key=key, reverse=True)[:top]
    # Worst uses the upper bound ascending, which keeps a 0-1 record off the
    # list for the same reason 2-0 stays off the best list.
    worst = sorted(entries, key=lambda e: e["ceiling"])[:top]

    return {
        "best": best,
        "worst": worst,
        # Frequency, not rate — how often a hero was banned is a count, and
        # putting it through Wilson would be a category error.
        "most_banned": bans(conn, subject, filters)["heroes"][:top],
        "baseline": baseline,
        "min_games": min_games,
        "rank": rank,
    }
