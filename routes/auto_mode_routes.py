"""Fork: the auto mode switch for Settings → Tools (admin session).

* ``GET /api/fork/auto-mode``: ``{"enabled": bool}``.
* ``PUT /api/fork/auto-mode`` ``{"enabled": bool}``: turn it on or off; applies
  from the next tool call on.

What auto mode allows is in ``src/auto_mode.py``. The value is stored as the
``fork_auto_mode`` key in the normal settings file.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.middleware import require_admin
from src import auto_mode


class AutoModePut(BaseModel):
    enabled: bool


def setup_auto_mode_routes() -> APIRouter:
    router = APIRouter(tags=["fork-auto-mode"])

    @router.get("/api/fork/auto-mode", dependencies=[Depends(require_admin)])
    async def get_auto_mode():
        return {"enabled": auto_mode.enabled()}

    @router.put("/api/fork/auto-mode", dependencies=[Depends(require_admin)])
    async def put_auto_mode(body: AutoModePut):
        return {"enabled": auto_mode.set_enabled(body.enabled)}

    return router
