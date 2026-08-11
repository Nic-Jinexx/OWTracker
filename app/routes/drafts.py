"""Draft lifecycle: create, patch, resolve conflicts, commit.

Manual entry uses exactly these endpoints, so the commit transaction is
exercised from day one rather than written twice.
"""

from __future__ import annotations

import json

import cv2
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import draft as draft_module
from .. import ingest
from .. import paths
from .. import settings as settings_module
from ..commit import CommitError, commit_draft
from ..db import get_conn, utcnow
from ..extract import nameplates
from ..extract.local import LocalExtractor

router = APIRouter(prefix="/api/drafts", tags=["drafts"])


def _load(conn, draft_id: int) -> dict:
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Draft {draft_id} not found")
    return {"row": row, "payload": json.loads(row["payload"])}


def _save(conn, draft_id: int, payload: dict) -> None:
    conn.execute(
        "UPDATE drafts SET payload = ?, updated_at = ? WHERE id = ?",
        (json.dumps(payload), utcnow(), draft_id),
    )
    conn.commit()


def _envelope(payload: dict) -> dict:
    return {
        "payload": payload,
        "problems": draft_module.validate_for_commit(payload),
    }


@router.post("")
def create_draft(body: dict | None = None) -> dict:
    body = body or {}
    with get_conn() as conn:
        team_size = int(body.get("team_size") or settings_module.get(conn, "default_team_size"))
        payload = draft_module.empty_draft(team_size)
        # Stamped 'default', not 'manual'. It came from a setting, so nobody has
        # actually asserted it — and invariant 6 says the row count is read off
        # the screenshot anyway. Marking it manual made a 6v6 screenshot raise a
        # conflict against a 5 the operator never typed, which blocked commit on
        # a disagreement that did not exist.
        payload["meta"]["team_size"] = draft_module.field(team_size, source="default")

        # Pre-mark the operator's own row if their name is configured.
        my_name = settings_module.get(conn, "my_display_name")
        if my_name:
            payload["rows"][0]["is_me"] = True
            payload["rows"][0]["player_name"] = draft_module.field(my_name)

        now = utcnow()
        cursor = conn.execute(
            "INSERT INTO drafts (created_at, updated_at, status, payload) "
            "VALUES (?, ?, 'open', ?)",
            (now, now, json.dumps(payload)),
        )
        conn.commit()
        draft_id = int(cursor.lastrowid)

    return {"id": draft_id, **_envelope(payload)}


@router.get("")
def list_drafts() -> list[dict]:
    with get_conn() as conn:
        return [
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "status": r["status"],
                "committed_match_id": r["committed_match_id"],
            }
            for r in conn.execute(
                "SELECT * FROM drafts WHERE status = 'open' ORDER BY updated_at DESC"
            )
        ]


@router.get("/{draft_id}")
def get_draft(draft_id: int) -> dict:
    with get_conn() as conn:
        loaded = _load(conn, draft_id)
    return {"id": draft_id, "status": loaded["row"]["status"], **_envelope(loaded["payload"])}


@router.patch("/{draft_id}")
def patch_draft(draft_id: int, body: dict) -> dict:
    """Apply operator edits.

    Body shape:
        {"meta": {"result": "win", "map_id": 3},
         "rows": [{"team": "ally", "row_index": 0, "eliminations": 14,
                   "is_me": true}],
         "bans": [{"hero_id": 5, "slot_index": 0}]}

    Every value written here is recorded as source='manual', which is what
    protects it from being overwritten by a later screenshot.
    """
    with get_conn() as conn:
        loaded = _load(conn, draft_id)
        if loaded["row"]["status"] != "open":
            raise HTTPException(409, "Draft is no longer open")
        payload = loaded["payload"]

        for name, value in (body.get("meta") or {}).items():
            if name not in draft_module.META_FIELDS:
                raise HTTPException(400, f"Unknown match field: {name}")
            payload["meta"][name] = draft_module.field(value)

        index = {(r["team"], r["row_index"]): r for r in payload["rows"]}
        for incoming in body.get("rows") or []:
            key = (incoming.get("team"), incoming.get("row_index"))
            target = index.get(key)
            if target is None:
                raise HTTPException(400, f"No such row: {key[0]} #{key[1]}")

            if "is_me" in incoming:
                if incoming["is_me"]:
                    # Exactly one row can be you; clicking a new one moves it.
                    for row in payload["rows"]:
                        row["is_me"] = False
                target["is_me"] = bool(incoming["is_me"])

            for name, value in incoming.items():
                if name in ("team", "row_index", "is_me"):
                    continue
                if name in ("player_id", "nameplate_phash", "nameplate_width",
                            "nameplate_crop", "nameplate_crop_url",
                            "nameplate_confidence"):
                    target[name] = value
                    continue
                if name not in draft_module.ROW_FIELDS:
                    raise HTTPException(400, f"Unknown player field: {name}")
                target[name] = draft_module.field(value)
                # Typing a name by hand detaches the row from any auto-matched
                # player, so the operator's spelling always wins.
                if name == "player_name":
                    target["player_id"] = None

        if "bans" in body:
            payload["bans"] = body["bans"]

        _save(conn, draft_id, payload)

    return {"id": draft_id, **_envelope(payload)}


@router.post("/{draft_id}/files")
async def attach_files(
    draft_id: int,
    files: list[UploadFile] = File(...),
    kinds: str | None = Form(None),
    override_duplicate: bool = Form(False),
) -> dict:
    """Attach one or more screenshots to a draft and run the extractor.

    Grouping is the operator's assertion: whatever arrives in one request
    belongs to one match. This same endpoint handles the late-arriving second
    screenshot, which is why nothing here infers pairing.

    `kinds` is an optional comma-separated list matching `files` positionally;
    anything missing falls back to a filename hint the operator can correct.
    """
    declared = [k.strip() for k in kinds.split(",")] if kinds else []

    with get_conn() as conn:
        loaded = _load(conn, draft_id)
        if loaded["row"]["status"] != "open":
            raise HTTPException(409, "Draft is no longer open")
        payload = loaded["payload"]

        extractor = LocalExtractor(
            team_size=payload["meta"]["team_size"]["value"]
            or settings_module.get(conn, "default_team_size"),
            nameplate_library=nameplates.load_library(conn),
            nameplate_max_distance=settings_module.get(conn, "nameplate_hash_max_distance"),
            hero_max_distance=settings_module.get(conn, "hero_hash_max_distance"),
            read_names=bool(settings_module.get(conn, "read_unknown_names")),
        )
        hero_ids = {row["name"]: row["id"]
                    for row in conn.execute("SELECT id, name FROM heroes")}

        attached: list[dict] = []
        warnings: list[str] = []

        for position, upload in enumerate(files):
            raw = await upload.read()
            try:
                stored = ingest.store_for_draft(draft_id, upload.filename or "shot.png", raw)
            except ingest.IngestError as error:
                warnings.append(error.message)
                continue

            # Invariant 5: refuse a file already committed to a match unless
            # the operator explicitly overrides.
            duplicate = ingest.find_committed_duplicate(conn, stored["sha256"])
            if duplicate and not override_duplicate:
                raise HTTPException(409, {
                    "code": "duplicate_screenshot",
                    "message": (
                        f"{upload.filename} was already saved as match "
                        f"#{duplicate['match_id']}. Submit again with override to "
                        f"use it anyway."
                    ),
                    "match_id": duplicate["match_id"],
                })

            if any(existing["sha256"] == stored["sha256"] for existing in payload["files"]):
                warnings.append(f"{upload.filename} is already attached to this draft.")
                continue

            try:
                size = ingest.verify_readable(stored["absolute"])
            except ingest.IngestError as error:
                warnings.append(error.message)
                continue

            kind = (declared[position] if position < len(declared) and declared[position]
                    else ingest.guess_kind(upload.filename or "", size))
            if kind not in ingest.KINDS:
                raise HTTPException(400, f"Unknown screenshot kind: {kind}")

            stored["kind"] = kind
            stored["width"], stored["height"] = size
            payload["files"].append({k: stored[k] for k in
                                     ("path", "sha256", "filename", "kind",
                                      "ingested_at", "width", "height")})

            result = extractor.extract(stored["absolute"], kind)
            warnings.extend(result.warnings)
            if result.ok:
                _resolve_heroes(result.draft, hero_ids, kind)
                _store_crops(draft_id, result, stored["sha256"])
                # Stamp origin so the merge rules can rank endgame over in-game.
                _stamp_origin(result.draft, kind)
                draft_module.merge_draft(payload, result.draft)
            attached.append({"filename": upload.filename, "kind": kind,
                             "sha256": stored["sha256"], "path": stored["path"],
                             "url": paths.screenshot_url(stored["path"])})

        _save(conn, draft_id, payload)

    return {"id": draft_id, "attached": attached, "warnings": warnings, **_envelope(payload)}


def _resolve_heroes(fragment: dict, hero_ids: dict[str, int], kind: str) -> None:
    """Turn the extractor's hero *names* into `heroes.id` envelopes.

    The extractor works in hero names because that is what a hash library is
    keyed by and it has no database; the draft holds foreign keys. A name that
    is not in the table is dropped rather than guessed at — that means the seed
    list and the hash library have drifted, which is worth noticing, not
    papering over.
    """
    for row in fragment.get("rows", []):
        name = row.pop("hero_name", None)
        confidence = row.pop("hero_confidence", None)
        hero_id = hero_ids.get(name) if name else None
        if hero_id is not None:
            row["hero_id"] = draft_module.field(hero_id, source="template",
                                                origin=kind, confidence=confidence)


def _store_crops(draft_id: int, result, sha256: str) -> None:
    """Write nameplate crops next to the screenshot they came from.

    Under `data/screenshots/`, because that is the only tree the app serves —
    and recorded as a portable path plus a ready-made URL, because the review
    grid needs a URL and building one by hand is how the old code ended up
    linking at `/data/screenshots/...`, which is not mounted.
    """
    if not result.crops:
        return
    folder = paths.DRAFT_SCREENSHOTS_DIR / str(draft_id) / "nameplates"
    folder.mkdir(parents=True, exist_ok=True)
    by_position = {(row["team"], row["row_index"]): row
                   for row in result.draft.get("rows", [])}
    for crop in result.crops:
        target = folder / f"{sha256[:8]}-{crop['team']}-{crop['row_index']}.png"
        ok, buffer = cv2.imencode(".png", crop["image"])
        if not ok:
            continue
        target.write_bytes(buffer.tobytes())
        row = by_position.get((crop["team"], crop["row_index"]))
        if row is not None:
            row["nameplate_crop"] = paths.portable(target)
            row["nameplate_crop_url"] = paths.screenshot_url(paths.portable(target))


def _stamp_origin(fragment: dict, kind: str) -> None:
    for envelope in fragment.get("meta", {}).values():
        if envelope.get("source") == "template":
            envelope["origin"] = kind
    for row in fragment.get("rows", []):
        for name in draft_module.ROW_FIELDS:
            envelope = row.get(name)
            if isinstance(envelope, dict) and envelope.get("source") == "template":
                envelope["origin"] = kind


@router.patch("/{draft_id}/files/{sha256}")
def correct_kind(draft_id: int, sha256: str, body: dict) -> dict:
    """Let the operator correct a mis-detected screenshot kind."""
    kind = body.get("kind")
    if kind not in ingest.KINDS:
        raise HTTPException(400, f"kind must be one of {ingest.KINDS}")
    with get_conn() as conn:
        loaded = _load(conn, draft_id)
        payload = loaded["payload"]
        for entry in payload["files"]:
            if entry["sha256"].startswith(sha256):
                entry["kind"] = kind
                break
        else:
            raise HTTPException(404, "No such file on this draft")
        _save(conn, draft_id, payload)
    return {"id": draft_id, **_envelope(payload)}


@router.get("/{draft_id}/extractor")
def extractor_status(draft_id: int) -> dict:
    """What the extractor can currently do — surfaced in the drop zone so a
    missing reference library reads as 'not built yet', not 'broken'."""
    return LocalExtractor.reference_status()


@router.post("/{draft_id}/conflicts")
def resolve(draft_id: int, body: dict) -> dict:
    """Resolve a manual-vs-extraction disagreement. Body: {path, keep}."""
    keep = body.get("keep")
    if keep not in ("mine", "theirs"):
        raise HTTPException(400, "keep must be 'mine' or 'theirs'")
    with get_conn() as conn:
        loaded = _load(conn, draft_id)
        payload = draft_module.resolve_conflict(loaded["payload"], body.get("path", ""), keep)
        _save(conn, draft_id, payload)
    return {"id": draft_id, **_envelope(payload)}


@router.post("/{draft_id}/commit")
def commit(draft_id: int) -> dict:
    with get_conn() as conn:
        try:
            match_id = commit_draft(conn, draft_id)
        except CommitError as error:
            raise HTTPException(400, {"problems": error.problems})
    return {"match_id": match_id}


@router.delete("/{draft_id}")
def abandon(draft_id: int) -> dict:
    with get_conn() as conn:
        conn.execute(
            "UPDATE drafts SET status = 'abandoned', updated_at = ? WHERE id = ? AND status = 'open'",
            (utcnow(), draft_id),
        )
        conn.commit()
    return {"ok": True}
