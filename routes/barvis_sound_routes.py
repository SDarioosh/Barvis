"""Barvis: the barvis-sound page (conversations and "who is this?" cards).

``GET /barvis/sound`` serves ``static/barvis-sound.html``; its script calls:

* ``GET  /api/barvis/sound/conversations``  (query, person, since, until, limit)
* ``GET  /api/barvis/sound/unnamed``        (limit)
* ``POST /api/barvis/sound/name-speaker``   {conversation_id, speaker_label, name}

All three go through the module's MCP tools (``src/barvis_sound_view.py``); the module's
database is never touched. Admin only: the data is the owner's recordings, and the MCP
server is admin-configured. Naming a speaker from a card is the user's explicit action,
so it doesn't go through the chat approval card.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core.middleware import require_admin
from src import barvis_sound_view as view

logger = logging.getLogger(__name__)

SERVER_ENV = "BARVIS_SOUND_SERVER"


class NameSpeaker(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=200)
    speaker_label: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)


def _caller() -> view.CallTool:
    from src.tool_utils import get_mcp_manager

    key = (os.environ.get(SERVER_ENV) or "convo").strip()
    manager = get_mcp_manager()
    server_id = view.server_id(manager, key) if manager is not None else None
    if server_id is None:
        raise HTTPException(503, f"The barvis-sound MCP server ({key!r}) is not connected.")

    async def call(tool: str, args: dict) -> str:
        res = await manager.call_tool(f"mcp__{server_id}__{tool}", args)
        if res.get("exit_code") or res.get("error"):
            logger.warning("barvis-sound %s failed: %s", tool, res.get("error") or res.get("stderr"))
            raise HTTPException(502, f"barvis-sound {tool} failed")
        return res.get("stdout") or ""

    return call


def setup_barvis_sound_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/barvis/sound")
    async def page(request: Request):
        from src.app_helpers import serve_html_with_nonce
        from src.constants import BASE_DIR

        return serve_html_with_nonce(request, os.path.join(BASE_DIR, "static/barvis-sound.html"))

    @router.get("/api/barvis/sound/conversations", dependencies=[Depends(require_admin)])
    async def conversations(query: str = "", person: str = "", since: str = "",
                            until: str = "", limit: int = 30) -> dict[str, Any]:
        rows = await view.conversations(_caller(), query=query[:200], person=person[:100],
                                        since=since[:32], until=until[:32], limit=limit)
        return {"conversations": rows}

    @router.get("/api/barvis/sound/unnamed", dependencies=[Depends(require_admin)])
    async def unnamed(limit: int = 10) -> dict[str, Any]:
        return {"speakers": await view.unnamed_speakers(_caller(), limit)}

    @router.post("/api/barvis/sound/name-speaker", dependencies=[Depends(require_admin)])
    async def name_speaker(body: NameSpeaker) -> dict[str, Any]:
        try:
            message = await view.name_speaker(_caller(), body.conversation_id,
                                              body.speaker_label, body.name)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": not message.startswith("No lines"), "message": message}

    return router
