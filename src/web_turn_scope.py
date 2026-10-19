"""Fork: keep web lookups working when the web toggle is on.

Upstream decides which tools a turn may run from the words of the message
(request authority). Two gaps made web lookups fail in chat:

1. ``model_lookup_wants_web``: "search mistral 7b vs qwen3.5-9b" (and even
   "search the web for ...") matched the Hugging Face model-discovery rule, so
   the turn was granted ``search_hf_models`` only. A comparison or an explicit
   web word asks for information about the models, not for a catalog entry,
   so the rule now leaves those clauses to the normal web routing.
   Hook: ``src/turn_contract.py`` (model-discovery rule).

2. ``with_unclassified_web_search``: a message with no action words
   ("mistral 7b vs qwen3.5-9b") got no tool grants at all, yet with the web
   toggle on the model was shown ``web_search``, called it, and was blocked
   ("The exact operation is outside server request authority"). When the user
   has turned web search on and the words name no tool, the turn now gets a
   ``web_search`` grant. Only ``web_search``: no fetch or browser, so a search
   result can't steer the model to an arbitrary URL. Toggle off, policy and
   per-tool disables still win.
   Hook: ``routes/chat_routes.py``, after the request authority is created.
"""

from __future__ import annotations

import re
from dataclasses import replace

# Words that turn "search <model> ..." into a question about the model rather
# than a request for a Hugging Face catalog entry.
_WEB_OR_COMPARISON = re.compile(
    r"\b(?:vs\.?|versus|compar\w*|benchmarks?|web|internet|online|google)\b",
    re.I,
)

# Tools that never need more than the turn's own words to be useful.
_ALWAYS_GRANTED = frozenset({"ask_user", "update_plan"})


def model_lookup_wants_web(clause: str) -> bool:
    """True when a model-discovery clause asks for web information instead."""
    return bool(_WEB_OR_COMPARISON.search(str(clause or "")))


def _granted_tools(authority) -> set[str]:
    return {g.tool for g in getattr(authority, "grants", ()) or ()}


def with_unclassified_web_search(authority, *, web_search_enabled: bool,
                                 client_runtime_context=None):
    """Grant ``web_search`` to a turn whose words named no tool, if web is on."""
    if authority is None or not web_search_enabled:
        return authority
    from src.agent_runtime.authority import OperationGrant, canonical_tool
    from src.mcp_trust import trusted_readonly_mcp_tools

    tool = canonical_tool("web_search")
    granted = _granted_tools(authority)
    trusted = {canonical_tool(name) for name in trusted_readonly_mcp_tools()}
    if granted - _ALWAYS_GRANTED - trusted:
        return authority  # the words already chose the turn's tools
    if tool in getattr(authority, "denied", frozenset()) or getattr(authority, "block_all", False):
        return authority
    from src.agent_runtime.remote_resources import resolve_backend
    try:
        backend = resolve_backend(tool, context=client_runtime_context,
                                  admission=True, owner=authority.owner)
    except (ValueError, TypeError, AttributeError):
        return authority
    backends = authority.backend_resources
    if backends is not None and backend not in backends:
        backends = (*backends, backend)
    return replace(authority, grants=(*authority.grants, OperationGrant(tool)),
                   backend_resources=backends)
