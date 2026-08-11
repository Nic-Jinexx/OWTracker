"""The DraftMatch shape and the merge rules that govern it.

A draft is the only thing that ever becomes a match. Manual entry and
extraction both produce one of these, so there is exactly one commit path
(invariant 10).

Every value is wrapped in an envelope carrying where it came from and how much
we trust it:

    {"value": 14, "source": "template", "origin": "endgame_report",
     "confidence": 0.98}

`source` is the database enum ('template' | 'manual'). `origin` additionally
records *which screenshot* a template value came from, because the merge rules
need to rank an endgame report above a mid-match Tab shot.
"""

from __future__ import annotations

from typing import Any, Iterable

# Per-row fields that come off the scoreboard.
STAT_FIELDS = ("eliminations", "assists", "deaths", "damage", "healing", "mitigation")
ROW_FIELDS = ("player_name", "role", "hero_id") + STAT_FIELDS

# Match-level fields.
META_FIELDS = (
    "played_at",
    "map_id",
    "mode",
    "result",
    "team_size",
    "duration_seconds",
    "rank_range_low",
    "rank_range_high",
    "notes",
)

# Which screenshot wins a disagreement. The endgame report is authoritative for
# statistics; the in-game scoreboard is a mid-match snapshot and is stale by
# construction.
# Origin marking a value the name reader guessed rather than a screenshot
# asserted. Ranked below every real source, and exempted from conflicts
# entirely in `merge_field`.
SUGGESTION_ORIGIN = "name_suggestion"

ORIGIN_RANK = {
    SUGGESTION_ORIGIN: 0,
    "in_game_scoreboard": 1,
    "endgame_report": 2,
    "manual": 3,
}


def field(value: Any = None, source: str = "manual", origin: str | None = None,
          confidence: float | None = None) -> dict:
    """Build a field envelope."""
    if source == "manual":
        origin = "manual"
        confidence = 1.0
    return {"value": value, "source": source, "origin": origin, "confidence": confidence}


def empty_row(team: str, row_index: int) -> dict:
    row: dict[str, Any] = {
        "team": team,
        "row_index": row_index,
        "is_me": False,
        # Set by the nameplate matcher when it recognizes someone.
        "player_id": None,
        "nameplate_phash": None,
        "nameplate_width": None,
        "nameplate_crop": None,
        # Ready-made browser URL for the crop. Built server-side because only
        # `/screenshots` is mounted and hand-assembling that prefix in the page
        # is exactly how the match view ended up linking at a 404 for a year.
        "nameplate_crop_url": None,
        "nameplate_confidence": None,
    }
    for name in ROW_FIELDS:
        row[name] = field(None)
    return row


def empty_draft(team_size: int = 6) -> dict:
    """A blank draft: `team_size` ally rows, the same number of enemy rows,
    nothing filled in. The default is a fallback only — every caller passes the
    setting, and the extractor overrides it from the image (invariant 6)."""
    return {
        "meta": {name: field(None) for name in META_FIELDS},
        "rows": (
            [empty_row("ally", i) for i in range(team_size)]
            + [empty_row("enemy", i) for i in range(team_size)]
        ),
        "bans": [],
        "files": [],
        # Manual-vs-new disagreements awaiting an operator decision. Commit is
        # blocked while this is non-empty.
        "conflicts": [],
    }


def is_blank(envelope: dict) -> bool:
    value = envelope.get("value")
    return value is None or (isinstance(value, str) and value.strip() == "")


# --------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------

def merge_field(existing: dict, incoming: dict, path: str,
                conflicts: list[dict]) -> tuple[dict, str | None]:
    """Merge one field per the precedence rules.

    Returns (resulting_envelope, superseded_value_or_None).

    - Empty existing            -> take the incoming value.
    - Operator edited it        -> keep it, and raise a conflict for the
                                   operator to resolve. Nothing typed by hand
                                   is ever discarded silently.
    - Both from extraction      -> the higher-ranked screenshot wins, silently,
                                   with the loser recorded for provenance.
    """
    if is_blank(incoming):
        return existing, None

    if is_blank(existing):
        return incoming, None

    if existing.get("value") == incoming.get("value"):
        return existing, None

    # A suggestion is not an assertion. The name reader offers a guess for a
    # nameplate nobody has identified; it is not a second source claiming to
    # know better, so it defers to anything already there and never raises a
    # conflict. Without this, opening a draft with your own display name
    # pre-filled and then attaching a screenshot produced a conflict on your
    # own row every single time, because the reader had read the nameplate and
    # disagreed with the setting.
    if incoming.get("origin") == SUGGESTION_ORIGIN:
        return existing, None

    if existing.get("source") == "manual":
        conflicts.append({
            "path": path,
            "mine": existing.get("value"),
            "theirs": incoming.get("value"),
            "theirs_origin": incoming.get("origin"),
            "theirs_confidence": incoming.get("confidence"),
        })
        return existing, None

    existing_rank = ORIGIN_RANK.get(existing.get("origin") or "", 0)
    incoming_rank = ORIGIN_RANK.get(incoming.get("origin") or "", 0)
    if incoming_rank > existing_rank:
        return incoming, _as_text(existing.get("value"))
    return existing, _as_text(incoming.get("value"))


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


def merge_draft(base: dict, incoming: dict) -> dict:
    """Fold a freshly-extracted draft into an existing one.

    Rows are matched by (team, row_index) — position on the scoreboard — not by
    hero or name. Hero identity must never imply team or row identity
    (invariant 7).
    """
    conflicts: list[dict] = list(base.get("conflicts", []))
    superseded: list[dict] = []

    for name in META_FIELDS:
        if name not in incoming.get("meta", {}):
            continue
        merged, loser = merge_field(
            base["meta"].get(name, field(None)),
            incoming["meta"][name],
            f"meta.{name}",
            conflicts,
        )
        base["meta"][name] = merged
        if loser is not None:
            superseded.append({"path": f"meta.{name}", "value": loser})

    index = {(row["team"], row["row_index"]): row for row in base["rows"]}
    for incoming_row in incoming.get("rows", []):
        key = (incoming_row["team"], incoming_row["row_index"])
        target = index.get(key)
        if target is None:
            base["rows"].append(incoming_row)
            continue
        for name in ROW_FIELDS:
            if name not in incoming_row:
                continue
            merged, loser = merge_field(
                target.get(name, field(None)),
                incoming_row[name],
                f"rows[{key[0]}:{key[1]}].{name}",
                conflicts,
            )
            target[name] = merged
            if loser is not None:
                superseded.append({"path": f"rows[{key[0]}:{key[1]}].{name}", "value": loser})
        for passthrough in ("nameplate_phash", "nameplate_width", "nameplate_crop",
                            "nameplate_crop_url", "nameplate_confidence", "player_id"):
            if incoming_row.get(passthrough) and not target.get(passthrough):
                target[passthrough] = incoming_row[passthrough]

    if incoming.get("bans") and not base.get("bans"):
        base["bans"] = incoming["bans"]

    base["conflicts"] = conflicts
    base.setdefault("superseded", []).extend(superseded)
    return base


def resolve_conflict(draft: dict, path: str, keep: str) -> dict:
    """Apply an operator decision on a conflicted field.

    `keep` is 'mine' or 'theirs'.
    """
    remaining = []
    for conflict in draft.get("conflicts", []):
        if conflict["path"] != path:
            remaining.append(conflict)
            continue
        if keep == "theirs":
            _set_by_path(draft, path, field(
                conflict["theirs"],
                source="template",
                origin=conflict.get("theirs_origin"),
                confidence=conflict.get("theirs_confidence"),
            ))
    draft["conflicts"] = remaining
    return draft


def _set_by_path(draft: dict, path: str, envelope: dict) -> None:
    if path.startswith("meta."):
        draft["meta"][path.split(".", 1)[1]] = envelope
        return
    # rows[team:index].field
    head, name = path.rsplit(".", 1)
    inner = head[len("rows["):-1]
    team, index_text = inner.split(":")
    for row in draft["rows"]:
        if row["team"] == team and row["row_index"] == int(index_text):
            row[name] = envelope
            return


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def populated_rows(draft: dict) -> list[dict]:
    """Rows carrying any real content. A blank row is not a player."""
    result = []
    for row in draft.get("rows", []):
        if row.get("is_me"):
            result.append(row)
            continue
        if any(not is_blank(row.get(name, field(None))) for name in ROW_FIELDS):
            result.append(row)
    return result


def validate_for_commit(draft: dict) -> list[str]:
    """Return a list of human-readable problems. Empty means committable."""
    problems: list[str] = []

    result = draft.get("meta", {}).get("result", field(None))
    if is_blank(result):
        problems.append("Result is required — choose Win, Loss, or Draw.")
    elif result["value"] not in ("win", "loss", "draw"):
        problems.append(f"Result must be win, loss, or draw (got {result['value']!r}).")

    if draft.get("conflicts"):
        problems.append(
            f"{len(draft['conflicts'])} field conflict(s) need a decision before saving."
        )

    rows = populated_rows(draft)
    me_count = sum(1 for row in rows if row.get("is_me"))
    if rows and me_count == 0:
        problems.append("Mark which row is you before saving.")
    if me_count > 1:
        problems.append("Only one row can be marked as you.")

    return problems
