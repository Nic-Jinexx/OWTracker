"""Debug endpoints for the extraction pipeline.

Milestone 2's hard stop is that the localizer's overlay must be correct on
real screenshots before any reading code is written, so the overlay has to be
reachable from a browser and not only from a script.

Read-only, and every path is resolved and confined to a known directory — an
endpoint that takes a filename is the one place this app could be talked into
reading something it should not.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response

from .. import paths
from ..db import get_conn
from ..extract.local import LocalExtractor
from ..extract.localize import localize
from ..extract.overlay import render, render_tab
from ..extract.tab import localize_tab

router = APIRouter(prefix="/debug", tags=["debug"])

PNG = "image/png"


def _read(path: Path):
    image = LocalExtractor._load(str(path))
    if image is None:
        raise HTTPException(422, f"{path.name} could not be decoded as an image.")
    return image


def _declared_kind(name: str) -> str:
    """What the manifest says a sample is, defaulting to an endgame report.

    Only the *visual* endpoints consult this. There are two boards and two
    localizers now, and an overlay drawn by the wrong one is worse than no
    overlay: it renders a correct refusal as a picture of a failure.
    """
    manifest = paths.SAMPLES_DIR / "manifest.json"
    if not manifest.exists():
        return "endgame_report"
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8"))["images"]
    except (ValueError, OSError, KeyError):
        return "endgame_report"
    for entry in entries:
        if entry.get("file") == name:
            return entry.get("kind", "endgame_report")
    return "endgame_report"


def _localize_as_declared(name: str, image):
    """Run whichever localizer owns this sample's declared kind."""
    if _declared_kind(name) == "in_game_scoreboard":
        return localize_tab(image)
    return localize(image)


def _render_as_declared(name: str, image) -> bytes:
    result = _localize_as_declared(name, image)
    if _declared_kind(name) == "in_game_scoreboard":
        return render_tab(image, result)
    return render(image, result)


def _draft_file(draft_id: int, sha256: str | None) -> Path:
    with get_conn() as conn:
        row = conn.execute("SELECT payload FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Draft {draft_id} not found")
    files = json.loads(row["payload"]).get("files", [])
    if not files:
        raise HTTPException(404, f"Draft {draft_id} has no screenshots attached.")
    if sha256:
        files = [f for f in files if f["sha256"].startswith(sha256)]
        if not files:
            raise HTTPException(404, f"No file on draft {draft_id} matching {sha256}.")
    return paths.ROOT / files[0]["path"]


def _sample(name: str) -> Path:
    """Resolve a name inside samples/, refusing anything that escapes it."""
    candidate = (paths.SAMPLES_DIR / name).resolve()
    try:
        candidate.relative_to(paths.SAMPLES_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "Sample names may not contain a path.")
    if not candidate.is_file():
        raise HTTPException(404, f"No sample named {name}.")
    return candidate


@router.get("/overlay/sample/{name}")
def overlay_sample(name: str) -> Response:
    path = _sample(name)
    return Response(content=_render_as_declared(path.name, _read(path)), media_type=PNG)


@router.get("/localize/sample/{name}")
def localize_sample(name: str) -> dict:
    return _report(_sample(name))


@router.get("/overlay/{draft_id}")
def overlay_draft(draft_id: int, sha256: str | None = None) -> Response:
    return Response(content=render(_read(_draft_file(draft_id, sha256))), media_type=PNG)


@router.get("/localize/{draft_id}")
def localize_draft(draft_id: int, sha256: str | None = None) -> dict:
    return _report(_draft_file(draft_id, sha256))


def _report(path: Path) -> dict:
    result = localize(_read(path))
    return {
        "file": path.name,
        "ok": result.ok,
        "reason": result.reason,
        "warnings": result.warnings,
        "diagnostics": result.diagnostics,
    }


@router.get("/samples", response_class=HTMLResponse)
def sample_gallery() -> str:
    """Every sample's overlay on one page — the milestone-2 eyeball check."""
    images = sorted(p for p in paths.SAMPLES_DIR.glob("*.png")) \
        if paths.SAMPLES_DIR.exists() else []
    if not images:
        return "<p>No samples in <code>samples/</code>.</p>"

    blocks = []
    for path in images:
        result = _localize_as_declared(path.name, _read(path))
        kind = "tab" if _declared_kind(path.name) == "in_game_scoreboard" else "endgame"
        status = "ok" if result.ok else f"FAILED — {result.reason}"
        notes = "".join(f"<li>{w}</li>" for w in result.warnings)
        blocks.append(
            f"<figure><figcaption><b>{path.name}</b> [{kind}] — {status}"
            f"{f'<ul>{notes}</ul>' if notes else ''}</figcaption>"
            f"<img src='/debug/overlay/sample/{path.name}'></figure>"
        )
    return (
        "<!doctype html><meta charset='utf-8'><title>Localizer overlays</title>"
        "<style>body{background:#14161c;color:#dfe3ec;font:14px system-ui;margin:24px}"
        "img{max-width:100%;border:1px solid #333;display:block;margin:8px 0 32px}"
        "figcaption{margin-bottom:4px} ul{margin:4px 0;color:#ffb454}</style>"
        "<h1>Localizer overlays</h1>" + "".join(blocks)
    )
