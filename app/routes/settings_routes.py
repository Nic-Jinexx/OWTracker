"""Operator configuration."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import settings as settings_module
from ..db import get_conn

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def read_settings() -> dict:
    with get_conn() as conn:
        return settings_module.all(conn)


@router.patch("")
def update_settings(changes: dict) -> dict:
    unknown = set(changes) - set(settings_module.DEFAULTS)
    if unknown:
        raise HTTPException(400, f"Unknown setting(s): {', '.join(sorted(unknown))}")
    with get_conn() as conn:
        for key, value in changes.items():
            settings_module.set(conn, key, value)
        return settings_module.all(conn)
