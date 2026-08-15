"""OWTracker application entry point.

Binds to 127.0.0.1 only. Makes no outbound network calls of any kind.
"""

from __future__ import annotations

import threading
import webbrowser
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import paths
from . import __version__
from .db import init_db
from .routes import (
    debug,
    drafts,
    export,
    matches,
    players,
    reference,
    seasons,
    settings_routes,
    stats,
)

HOST = "127.0.0.1"
PORT = 8770


@asynccontextmanager
async def lifespan(app: FastAPI):
    result = init_db()
    if result["migrations_applied"]:
        print(f"[owtracker] applied migrations: {', '.join(result['migrations_applied'])}")
    seeded = {k: v for k, v in result["seeded"].items() if v}
    if seeded:
        print(f"[owtracker] seeded: {seeded}")
    print(f"[owtracker] ready at http://{HOST}:{PORT}")
    yield


app = FastAPI(title="OWTracker", version=__version__, lifespan=lifespan)

app.include_router(reference.router)
app.include_router(settings_routes.router)
app.include_router(drafts.router)
app.include_router(matches.router)
app.include_router(players.router)
app.include_router(seasons.router)
app.include_router(stats.router)
app.include_router(export.router)
app.include_router(debug.router)


@app.exception_handler(Exception)
async def unhandled(request, exc):
    """The server never crashes on bad input; it reports and keeps running."""
    return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": app.version}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(paths.STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=paths.STATIC_DIR), name="static")
# Archived screenshots, so the match view can link to them.
paths.ensure_data_dirs()
app.mount("/screenshots", StaticFiles(directory=paths.SCREENSHOTS_DIR), name="screenshots")


def open_browser_soon(delay: float = 1.0) -> None:
    threading.Timer(delay, lambda: webbrowser.open(f"http://{HOST}:{PORT}/")).start()


def main() -> None:
    import uvicorn

    open_browser_soon()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
