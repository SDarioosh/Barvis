"""Fork: the "What do you know about me" review page over the belief ledger
(``src/evidence_intake.py``; plan stages 1.3 and 1.4, ``docs/fork/beliefs.md``).

* ``GET  /beliefs``: ``static/beliefs.html`` (sidebar: Beliefs).
* ``GET  /api/beliefs?status=<tentative|confirmed|stale|rejected|all>&subject=<str>``:
  ``{"beliefs": [...], "stats": {...}}``; without ``status``, all but rejected.
* ``GET  /api/beliefs/{id}``: one belief with its evidence and events.
* ``POST /api/beliefs/{id}/confirm``, ``POST /api/beliefs/{id}/restore``.
* ``POST /api/beliefs/{id}/correct`` {text}: 400 on empty text.
* ``POST /api/beliefs/{id}/forget`` {reason?}: rejected here, then the odysseus-sound
  facts behind it are retired in the module (``module_facts.propagate_forget``).
  Answers ``{"belief", "propagated": {"retired", "failed", "unsupported"}}``. The forget
  never fails because the module is down: the belief is rejected locally first.
* ``POST /api/beliefs/run``: the facts job for this owner now (one module call, at most
  ``RUN_TIMEOUT`` seconds); answers its counts line.

Same auth as the Today page: admin session (``require_admin``); API tokens are refused.
Another owner's id is a 404.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.middleware import require_admin
from src import evidence_intake as ledger
from src import module_facts as mf
from src.auth_helpers import effective_user

logger = logging.getLogger(__name__)

RUN_TIMEOUT = 60.0
FORGET_TIMEOUT = 30.0
DEFAULT_STATUSES = ("tentative", "confirmed", "stale")


class Correction(BaseModel):
    text: str = ""


class Forget(BaseModel):
    reason: str = ""


def _owner(request: Request) -> str:
    return effective_user(request) or ""


def _found(fn, *args, **kwargs):
    """A ledger call; another owner's or an unknown id is a 404."""
    try:
        return fn(*args, **kwargs)
    except ledger.BeliefNotFound as e:
        raise HTTPException(404, "belief not found") from e


def module_caller() -> mf.CallTool | None:
    """A ``list_facts``/``retire_fact`` caller for the connected odysseus-sound MCP
    server, or None when it isn't connected."""
    from src.tool_utils import get_mcp_manager

    manager = get_mcp_manager()
    server_id = mf.module_server_id(manager) if manager is not None else None
    return mf.mcp_caller(manager, server_id) if server_id else None


def _module_fact_count(evidence: list[dict]) -> int:
    return len({str(e.get("source_id")) for e in evidence or []
                if e.get("source") == mf.SOURCE})


async def forget(owner: str, belief_id: int, reason: str = "") -> dict[str, Any]:
    """Reject locally, then retire the module facts; propagation problems are counted,
    never raised."""
    evidence = _found(ledger.reject, owner, belief_id, reason)
    belief = ledger.get_belief(owner, belief_id)
    facts = _module_fact_count(evidence)
    propagated = {"retired": 0, "failed": 0, "unsupported": 0}
    if facts:
        try:
            call = module_caller()
            if call is None:
                propagated["unsupported"] = facts
            else:
                propagated = await asyncio.wait_for(
                    mf.propagate_forget(owner, evidence, call=call), FORGET_TIMEOUT)
        except Exception as e:  # noqa: BLE001 -- the forget stands; the page says so
            logger.warning("belief %s forgotten here; module not updated: %s", belief_id, e)
            propagated = {"retired": 0, "failed": facts, "unsupported": 0}
    return {"belief": belief, "propagated": propagated}


async def run_facts(owner: str) -> JSONResponse:
    if not mf.enabled():
        return JSONResponse(status_code=409, content={"error": "MODULE_FACTS_SYNC is off"})
    call = module_caller()
    if call is None:
        return JSONResponse(status_code=503, content={
            "error": f"The odysseus-sound MCP server ({mf.server_key()!r}) is not connected."})
    try:
        stats = await asyncio.wait_for(mf.sync(owner, call), RUN_TIMEOUT)
    except asyncio.TimeoutError:
        return JSONResponse(status_code=504, content={
            "error": f"odysseus-sound didn't answer within {int(RUN_TIMEOUT)} s"})
    except RuntimeError as e:  # a failed tool call, or MemoryStoreUnreadable
        logger.warning("facts sync from the review page failed: %s", e)
        return JSONResponse(status_code=502, content={"error": f"Facts sync failed: {e}"})
    if stats is None:
        return JSONResponse(content={"summary": "odysseus-sound answered with text; "
                                                "nothing synced (update the module)",
                                     "stats": None})
    return JSONResponse(content={"summary": mf.summary(stats), "stats": stats})


def setup_beliefs_routes() -> APIRouter:
    router = APIRouter()
    admin = [Depends(require_admin)]

    @router.get("/beliefs", include_in_schema=False)
    async def page(request: Request):
        from src.app_helpers import serve_html_with_nonce
        from src.constants import BASE_DIR

        return serve_html_with_nonce(request, os.path.join(BASE_DIR, "static/beliefs.html"))

    @router.get("/api/beliefs", dependencies=admin)
    async def list_beliefs(request: Request, status: str | None = None,
                           subject: str | None = None) -> dict[str, Any]:
        owner = _owner(request)
        wanted: Any = None if status == "all" else (status or DEFAULT_STATUSES)
        try:
            beliefs = ledger.list_beliefs(owner, wanted, (subject or "").strip() or None)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"beliefs": beliefs, "stats": ledger.stats(owner)}

    # /run before /{belief_id}: a literal path, not an id.
    @router.post("/api/beliefs/run", dependencies=admin)
    async def run(request: Request):
        return await run_facts(_owner(request))

    @router.get("/api/beliefs/{belief_id}", dependencies=admin)
    async def get_one(request: Request, belief_id: int) -> dict[str, Any]:
        return _found(ledger.get_belief, _owner(request), belief_id)

    @router.post("/api/beliefs/{belief_id}/confirm", dependencies=admin)
    async def confirm(request: Request, belief_id: int) -> dict[str, Any]:
        return _found(ledger.confirm, _owner(request), belief_id)

    @router.post("/api/beliefs/{belief_id}/restore", dependencies=admin)
    async def restore(request: Request, belief_id: int) -> dict[str, Any]:
        return _found(ledger.restore, _owner(request), belief_id)

    @router.post("/api/beliefs/{belief_id}/correct", dependencies=admin)
    async def correct(request: Request, belief_id: int, body: Correction) -> dict[str, Any]:
        owner = _owner(request)
        _found(ledger.get_belief, owner, belief_id)  # 404 before 400
        try:
            return _found(ledger.correct, owner, belief_id, body.text)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @router.post("/api/beliefs/{belief_id}/forget", dependencies=admin)
    async def forget_one(request: Request, belief_id: int,
                         body: Forget | None = None) -> dict[str, Any]:
        return await forget(_owner(request), belief_id, (body.reason if body else ""))

    return router
