"""The single commit path.

Every match in the database came through here (invariant 10). Manual entry and
extraction both just populate a draft; this module is the only code that writes
to `matches`, `match_players`, `match_bans`, `match_sources`, or
`field_provenance`.

Commit is one transaction (invariant 9). Screenshots move from the draft folder
to the match folder as part of it, and the moves are undone if the transaction
fails, so a failed commit leaves neither a partial match nor orphaned files.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from . import draft as draft_module
from . import paths
from .db import utcnow


class CommitError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def _get_or_create_player(conn: sqlite3.Connection, display_name: str, now: str) -> int:
    """Exact display name is the identity (invariant 4). No normalization, no
    alias merging — a renamed player legitimately becomes a new row."""
    name = display_name.strip()
    row = conn.execute("SELECT id FROM players WHERE display_name = ?", (name,)).fetchone()
    if row:
        conn.execute(
            "UPDATE players SET last_seen = ?, games_seen = games_seen + 1 WHERE id = ?",
            (now, row["id"]),
        )
        return int(row["id"])
    cursor = conn.execute(
        "INSERT INTO players (display_name, first_seen, last_seen, games_seen) "
        "VALUES (?, ?, ?, 1)",
        (name, now, now),
    )
    return int(cursor.lastrowid)


def _learn_portrait(conn: sqlite3.Connection, phash: str | None,
                    hero_id: int | None, now: str) -> None:
    """Teach `hero_portraits` that this hash is this hero.

    Refuses to write a hash another hero already claims. Two heroes holding the
    same bits does not produce a wrong answer — `identify`'s margin rule sees a
    tie and returns unknown — but it makes the matcher permanently worse at
    *both* of them, which is a strange price to pay for one mis-clicked
    dropdown. The existing claim stands and the operator can drop it from
    Settings, where every learned portrait is listed.

    Silent, because a commit is not the place to argue: the match itself is
    fine, only the optional lesson is declined.
    """
    if not phash or hero_id is None:
        return
    owner = conn.execute(
        "SELECT hero_id FROM hero_portraits WHERE phash = ? LIMIT 1", (phash,)
    ).fetchone()
    if owner is not None and int(owner["hero_id"]) != int(hero_id):
        return

    known = conn.execute(
        "SELECT id FROM hero_portraits WHERE hero_id = ? AND phash = ?",
        (hero_id, phash),
    ).fetchone()
    if known:
        conn.execute(
            "UPDATE hero_portraits SET times_matched = times_matched + 1 WHERE id = ?",
            (known["id"],),
        )
    else:
        conn.execute(
            "INSERT INTO hero_portraits (hero_id, phash, first_seen, times_matched) "
            "VALUES (?, ?, ?, 1)",
            (hero_id, phash, now),
        )


def _assign_season(conn: sqlite3.Connection, match_id: int) -> None:
    """File one match into whichever season's range contains its date.

    No match is a normal outcome: before any season exists, and for a match
    played outside every range, `season_id` stays NULL and the app reports it
    as unassigned rather than guessing at the nearest one.
    """
    conn.execute(
        """
        UPDATE matches SET season_id = (
            SELECT s.id FROM seasons s
            WHERE matches.played_at IS NOT NULL
              AND date(matches.played_at) >= s.starts_on
              AND (s.ends_on IS NULL OR date(matches.played_at) <= s.ends_on)
        )
        WHERE id = ?
        """,
        (match_id,),
    )


def _record_provenance(conn: sqlite3.Connection, table: str, row_id: int,
                       column: str, envelope: dict, superseded: dict[str, str]) -> None:
    """Provenance is written for extracted fields (invariant 3).

    Only `template` is recorded. A `manual` value is the operator's own word and
    needs no provenance; a `default` one came from a setting nobody asserted, so
    there is even less to say about it. `field_provenance.source` is CHECK-
    constrained to those two real sources, so anything else must be filtered
    here rather than handed to the database.
    """
    if draft_module.is_blank(envelope):
        return
    if (envelope.get("source") or "manual") != "template":
        return
    source = "template"
    conn.execute(
        "INSERT INTO field_provenance "
        "(table_name, row_id, column_name, source, confidence, superseded_value) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (table, row_id, column, source, envelope.get("confidence"),
         superseded.get(column)),
    )


def commit_draft(conn: sqlite3.Connection, draft_id: int) -> int:
    """Turn a draft into a match. Returns the new match id."""
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise CommitError([f"Draft {draft_id} does not exist."])
    if row["status"] == "committed":
        # The id is NULL when the match was deleted afterwards (ON DELETE SET
        # NULL). The draft still may not be committed twice — re-committing
        # would silently resurrect a match the operator chose to remove.
        match_id = row["committed_match_id"]
        where = f"as match {match_id}" if match_id is not None else "as a match that has since been deleted"
        raise CommitError([f"Draft {draft_id} is already committed {where}."])

    payload = json.loads(row["payload"])
    problems = draft_module.validate_for_commit(payload)
    if problems:
        raise CommitError(problems)

    now = utcnow()
    meta = payload["meta"]
    rows = draft_module.populated_rows(payload)
    superseded_by_path = {item["path"]: item["value"] for item in payload.get("superseded", [])}

    moves: list[tuple[Path, Path]] = []
    try:
        conn.execute("BEGIN")

        team_size = meta["team_size"]["value"]
        if not team_size:
            ally = sum(1 for r in rows if r["team"] == "ally")
            enemy = sum(1 for r in rows if r["team"] == "enemy")
            team_size = max(ally, enemy) or None

        columns = (
            meta["played_at"]["value"] or now,
            meta["map_id"]["value"],
            meta["mode"]["value"],
            meta["result"]["value"],
            team_size,
            meta["duration_seconds"]["value"],
            meta["rank_range_low"]["value"],
            meta["rank_range_high"]["value"],
            meta["notes"]["value"],
        )

        editing = payload.get("editing_match_id")
        if editing is not None:
            # An edit. Update the row in place and clear what hangs off it,
            # rather than delete-and-reinsert: the match id is referenced by
            # match_sources, so a new id would orphan the screenshots and break
            # every link to this match that already exists.
            if conn.execute("SELECT 1 FROM matches WHERE id = ?", (editing,)).fetchone() is None:
                raise CommitError(
                    [f"Match {editing} no longer exists, so this edit has nothing to save to. "
                     f"It was probably deleted in another tab."])
            match_id = int(editing)
            conn.execute(
                "UPDATE matches SET played_at = ?, map_id = ?, mode = ?, result = ?, "
                "team_size = ?, duration_seconds = ?, rank_range_low = ?, "
                "rank_range_high = ?, notes = ? WHERE id = ?",
                (*columns, match_id),
            )
            # Rebuilt below from the draft. Provenance goes too: it describes
            # where the *old* values came from and would otherwise be read as
            # describing the new ones.
            #
            # Order matters. The player provenance is found *through*
            # match_players, so it has to go before the rows it is looked up
            # by; deleting them first would leave every one of those
            # provenance rows behind, attached to ids that no longer exist.
            # The two tables are cleared by separate statements because
            # `row_id` means a different thing in each, and one combined
            # `row_id IN (...)` would cross-delete between them.
            conn.execute(
                "DELETE FROM field_provenance WHERE table_name = 'match_players' "
                "AND row_id IN (SELECT id FROM match_players WHERE match_id = ?)",
                (match_id,),
            )
            conn.execute(
                "DELETE FROM field_provenance WHERE table_name = 'matches' AND row_id = ?",
                (match_id,),
            )
            # `games_seen` is a stored counter that `_get_or_create_player`
            # bumps for every row it writes. Rewriting the roster below would
            # bump it a second time for everyone in this match, so give back
            # what this match already contributed first. Counted per row, not
            # per player, to stay symmetric with how it was added.
            conn.execute(
                """
                UPDATE players SET games_seen = MAX(0, games_seen - (
                    SELECT COUNT(*) FROM match_players mp
                    WHERE mp.match_id = ? AND mp.player_id = players.id))
                WHERE id IN (SELECT player_id FROM match_players
                             WHERE match_id = ? AND player_id IS NOT NULL)
                """,
                (match_id, match_id),
            )
            conn.execute("DELETE FROM match_players WHERE match_id = ?", (match_id,))
            conn.execute("DELETE FROM match_bans WHERE match_id = ?", (match_id,))
        else:
            cursor = conn.execute(
                "INSERT INTO matches (played_at, map_id, mode, result, team_size, "
                "duration_seconds, rank_range_low, rank_range_high, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*columns, now),
            )
            match_id = int(cursor.lastrowid)

        # Season is derived from the date, never entered. Doing it here means a
        # match is filed the moment it exists, and the same statement that runs
        # when a season's dates change is the one that runs now — so there is
        # only ever one rule deciding what belongs where.
        _assign_season(conn, match_id)

        for column in draft_module.META_FIELDS:
            _record_provenance(conn, "matches", match_id, column, meta[column],
                               {column: superseded_by_path.get(f"meta.{column}")})

        # Screenshots: draft folder -> match folder. Recorded so they can be
        # put back if the transaction fails.
        destination = paths.SCREENSHOTS_DIR / str(match_id)
        for source_file in payload.get("files", []):
            origin_path = paths.ROOT / source_file["path"]
            if not origin_path.exists():
                continue
            destination.mkdir(parents=True, exist_ok=True)
            final_path = destination / origin_path.name
            shutil.move(str(origin_path), str(final_path))
            moves.append((origin_path, final_path))
            conn.execute(
                "INSERT INTO match_sources (match_id, file_path, file_sha256, kind, ingested_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    match_id,
                    paths.portable(final_path),
                    source_file["sha256"],
                    source_file["kind"],
                    source_file.get("ingested_at") or now,
                ),
            )

        for entry in rows:
            player_id = entry.get("player_id")
            name_field = entry.get("player_name", draft_module.field(None))
            if player_id is None and not draft_module.is_blank(name_field):
                player_id = _get_or_create_player(conn, str(name_field["value"]), now)
            elif player_id is not None:
                conn.execute(
                    "UPDATE players SET last_seen = ?, games_seen = games_seen + 1 WHERE id = ?",
                    (now, player_id),
                )

            values = {name: entry.get(name, draft_module.field(None))["value"]
                      for name in draft_module.STAT_FIELDS}
            cursor = conn.execute(
                "INSERT INTO match_players (match_id, player_id, team, is_me, role, hero_id, "
                "eliminations, assists, deaths, damage, healing, mitigation, row_index) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    match_id,
                    player_id,
                    entry["team"],
                    1 if entry.get("is_me") else 0,
                    entry.get("role", draft_module.field(None))["value"],
                    entry.get("hero_id", draft_module.field(None))["value"],
                    values["eliminations"], values["assists"], values["deaths"],
                    values["damage"], values["healing"], values["mitigation"],
                    entry.get("row_index"),
                ),
            )
            match_player_id = int(cursor.lastrowid)

            prefix = f"rows[{entry['team']}:{entry['row_index']}]."
            for column in draft_module.ROW_FIELDS:
                envelope = entry.get(column, draft_module.field(None))
                _record_provenance(
                    conn, "match_players", match_player_id, column, envelope,
                    {column: superseded_by_path.get(prefix + column)},
                )

            # A recognized-or-typed nameplate teaches the matcher this face.
            phash = entry.get("nameplate_phash")
            if phash and player_id is not None:
                known = conn.execute(
                    "SELECT id FROM player_nameplates WHERE player_id = ? AND phash = ?",
                    (player_id, phash),
                ).fetchone()
                if known:
                    conn.execute(
                        "UPDATE player_nameplates SET times_matched = times_matched + 1 WHERE id = ?",
                        (known["id"],),
                    )
                else:
                    conn.execute(
                        "INSERT INTO player_nameplates (player_id, phash, width_px, first_seen, times_matched) "
                        "VALUES (?, ?, ?, ?, 1)",
                        (player_id, phash, entry.get("nameplate_width"), now),
                    )

            # A confirmed hero teaches the matcher this portrait, exactly as a
            # confirmed name teaches it a nameplate. This is the only way the
            # hero library ever grows on the operator's machine: the shipped one
            # covers what the sample corpus happened to contain, and there is no
            # offline source of hero art to fill in the rest.
            _learn_portrait(
                conn,
                entry.get("portrait_phash"),
                entry.get("hero_id", draft_module.field(None))["value"],
                now,
            )

        for ban in payload.get("bans", []):
            if ban.get("hero_id") is None:
                continue
            conn.execute(
                "INSERT INTO match_bans (match_id, hero_id, slot_index) VALUES (?, ?, ?)",
                (match_id, ban["hero_id"], ban.get("slot_index")),
            )

        conn.execute(
            "UPDATE drafts SET status = 'committed', committed_match_id = ?, updated_at = ? "
            "WHERE id = ?",
            (match_id, now, draft_id),
        )

        conn.commit()
        return match_id

    except Exception:
        conn.rollback()
        for original, moved in reversed(moves):
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(moved), str(original))
            except OSError:
                pass  # Best effort; the DB rollback is what matters.
        raise
