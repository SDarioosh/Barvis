"""Fork: the odysseus-sound page (conversations and "who is this?" cards).

``GET /modules/sound`` serves ``static/sound-module.html`` (the former ``/barvis/sound``
redirects there); its script calls:

* ``GET  /api/modules/sound/conversations``  (query, person, since, until, kind, limit)
* ``GET  /api/modules/sound/conversations/{id}``  one conversation (``get_conversation``)
* ``GET  /api/modules/sound/conversations/{id}/transcript``  (start_seconds; 200 lines a page)
* ``POST /api/modules/sound/lines/{line_id}``  {name}: ``reassign_line``
* ``POST /api/modules/sound/rename-speaker``   {old_name, new_name}:
  ``rename_speaker_everywhere`` (a merge when new_name exists; the page confirms first)
* ``GET  /api/modules/sound/unnamed``        (limit)
* ``POST /api/modules/sound/name-speaker``   {conversation_id, speaker_label, name}
* ``GET  /api/modules/sound/action-items``   (status)
* ``POST /api/modules/sound/action-items/{id}`` {status}

Action items are shown live from the module (no copies in Odysseus); Done/Dismiss call
the module's ``update_action_item``.

All of them go through the module's MCP tools (``src/sound_module_view.py``); the module's
database is never touched. Admin only: the data is the owner's recordings, and the MCP
server is admin-configured. Naming a speaker from a card is the user's explicit action,
so it doesn't go through the chat approval card.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from core.middleware import require_admin
from src import sound_module_view as view

logger = logging.getLogger(__name__)

SERVER_ENV = "SOUND_MODULE_SERVER"
LEGACY_SERVER_ENV = "BARVIS_SOUND_SERVER"  # former name, still read


class ItemStatus(BaseModel):
    status: str = Field(pattern="^(open|done|dismissed)$")


class NameSpeaker(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=200)
    speaker_label: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)


class ReassignLine(BaseModel):
    name: str  # 1-100 after trimming, checked by the view (400)


class RenameSpeaker(BaseModel):
    old_name: str
    new_name: str


def _caller() -> view.CallTool:
    from src.tool_utils import get_mcp_manager

    key = (os.environ.get(SERVER_ENV) or os.environ.get(LEGACY_SERVER_ENV) or "convo").strip()
    manager = get_mcp_manager()
    server_id = view.server_id(manager, key) if manager is not None else None
    if server_id is None:
        raise HTTPException(503, f"The odysseus-sound MCP server ({key!r}) is not connected.")

    async def call(tool: str, args: dict) -> str:
        res = await manager.call_tool(f"mcp__{server_id}__{tool}", args)
        if res.get("exit_code") or res.get("error"):
            logger.warning("odysseus-sound %s failed: %s", tool, res.get("error") or res.get("stderr"))
            raise HTTPException(502, f"odysseus-sound {tool} failed")
        return res.get("stdout") or ""

    return call


def setup_sound_module_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/barvis/sound", include_in_schema=False)
    async def former_page() -> RedirectResponse:
        return RedirectResponse("/modules/sound", status_code=301)

    @router.get("/modules/sound")
    async def page(request: Request):
        from src.app_helpers import serve_html_with_nonce
        from src.constants import BASE_DIR

        return serve_html_with_nonce(request, os.path.join(BASE_DIR, "static/sound-module.html"))

    @router.get("/api/modules/sound/conversations", dependencies=[Depends(require_admin)])
    async def conversations(query: str = "", person: str = "", since: str = "",
                            until: str = "", kind: str = "", limit: int = 30) -> dict[str, Any]:
        rows = await view.conversations(_caller(), query=query[:200], person=person[:100],
                                        since=since[:32], until=until[:32], kind=kind[:16],
                                        limit=limit)
        return {"conversations": rows}

    @router.get("/api/modules/sound/conversations/{conversation_id}",
                dependencies=[Depends(require_admin)])
    async def conversation(conversation_id: str) -> dict[str, Any]:
        try:
            view.check_conversation_id(conversation_id)
            return {"conversation": await view.conversation(_caller(), conversation_id)}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except LookupError as e:
            raise HTTPException(404, str(e)) from e

    @router.get("/api/modules/sound/conversations/{conversation_id}/transcript",
                dependencies=[Depends(require_admin)])
    async def transcript(conversation_id: str, start_seconds: int = 0) -> dict[str, Any]:
        try:
            view.check_conversation_id(conversation_id)
            return await view.transcript(_caller(), conversation_id, start_seconds)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except LookupError as e:
            raise HTTPException(404, str(e)) from e

    @router.post("/api/modules/sound/lines/{line_id}", dependencies=[Depends(require_admin)])
    async def reassign_line(line_id: int, body: ReassignLine) -> dict[str, Any]:
        try:
            if line_id < 1:
                raise ValueError("line id must be positive")
            view.check_name(body.name)
            return await view.reassign_line(_caller(), line_id, body.name)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @router.post("/api/modules/sound/rename-speaker", dependencies=[Depends(require_admin)])
    async def rename_speaker(body: RenameSpeaker) -> dict[str, Any]:
        try:
            view.check_name(body.old_name, "old_name")
            view.check_name(body.new_name, "new_name")
            return await view.rename_speaker(_caller(), body.old_name, body.new_name)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @router.get("/api/modules/sound/unnamed", dependencies=[Depends(require_admin)])
    async def unnamed(limit: int = 10) -> dict[str, Any]:
        return {"speakers": await view.unnamed_speakers(_caller(), limit)}

    @router.post("/api/modules/sound/name-speaker", dependencies=[Depends(require_admin)])
    async def name_speaker(body: NameSpeaker) -> dict[str, Any]:
        try:
            message = await view.name_speaker(_caller(), body.conversation_id,
                                              body.speaker_label, body.name)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": not message.startswith("No lines"), "message": message}

    @router.get("/api/modules/sound/action-items", dependencies=[Depends(require_admin)])
    async def action_items(status: str = "open") -> dict[str, Any]:
        try:
            return {"action_items": await view.action_items(_caller(), status)}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @router.post("/api/modules/sound/action-items/{item_id}",
                 dependencies=[Depends(require_admin)])
    async def set_action_item(item_id: int, body: ItemStatus) -> dict[str, Any]:
        message = await view.set_action_item(_caller(), item_id, body.status)
        return {"ok": message.startswith("Action item"), "message": message}

    return router
