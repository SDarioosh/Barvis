"""Fork: transcript links on answers built from trusted module tools.

odysseus-sound (and later modules) return a signed transcript link with every
fact, and every result asks the model to put the link next to each fact used.
Called directly the same model cites almost every time; through Odysseus about
half the answers had the right fact and no link (the eval's 9/20).

:func:`with_module_sources` (one hook in the agent loop, at the final answer)
adds a short "Sources:" list to a final answer that used a trusted module's
results but cites none of their links. The links are the ones the tools
returned, ranked by how many words the answer shares with the result line each
came from; nothing is invented. Answers that already cite a link, that say
nothing was found, or that share too little with any result line are left
alone. It also covers answers from the tool-free "answer now" round after an
empty reply.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

MAX_SOURCES = 3
MIN_OVERLAP = 2
LABEL_CHARS = 70

# A module transcript link: http(s)://host/.../view/<id>[?query][#fragment]
_LINK = re.compile(r"https?://[^\s<>\"'()\[\]]+/view/[A-Za-z0-9_-]+[^\s<>\"'()\[\]]*")
_ANY_VIEW_LINK = re.compile(r"/view/[A-Za-z0-9_-]+")
_QUOTED = re.compile(r"[\"“]([^\"”]{3,})[\"”]")
_WORD = re.compile(r"[a-z0-9][a-z0-9'’-]*")
_MCP_NAME = re.compile(r"\bmcp__[\w-]+\b")
_NOTHING_FOUND = re.compile(
    r"\b(?:couldn'?t|could not|can'?t|cannot|didn'?t|did not|unable to)\s+find\b"
    r"|\bno (?:mention|record|conversations?|transcripts?|lines?|results?|information)\b"
    r"|\bnothing (?:about|on|was|in)\b"
    r"|\bthere (?:is|are|was|were) no\b"
    r"|\bdon'?t have any\b",
    re.IGNORECASE,
)
_STOP = frozenset("""
a about after again all also am an and any are as at be been before being but by can
did do does done for from had has have he her here him his how i if in into is it its
just last me more my no not now of off on once only or our out over own said say says
she so some such than that the their them then there these they this those to too up
very was we were what when where which while who whom why will with would you your
conversation conversations transcript transcripts line lines link links id untitled
""".split())


def _event_tool_name(event: dict) -> str:
    tool = str(event.get("tool") or "").strip()
    if tool != "mcp":
        return tool
    for key in ("desc", "command", "output"):
        m = _MCP_NAME.search(str(event.get(key) or ""))
        if m:
            return m.group(0)
    return tool


def _words(text: str) -> set[str]:
    return {w.strip("'’-") for w in _WORD.findall(str(text or "").casefold())
            if len(w) > 2 and w not in _STOP}


def _clean_url(url: str) -> str:
    return url.rstrip(".,;:!?")


def _label(context: str) -> str:
    quoted = _QUOTED.search(context)
    text = quoted.group(1) if quoted else context
    text = _LINK.sub("", text)
    text = re.sub(r"^\s*(?:[-*]\s+|#\d+\s+|Link:\s*)", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—:;,.")
    text = text.replace("[", "(").replace("]", ")")
    if len(text) > LABEL_CHARS:
        text = text[:LABEL_CHARS - 1].rstrip() + "…"
    return text or "transcript"


def links_from_output(output: str) -> list[tuple[str, str]]:
    """``(url, context)`` for each transcript link in one tool result, in order.

    A link's context is the result entry it belongs to: its own line, plus the
    entry's earlier lines when the link sits on an indented continuation line
    (``search_conversations`` puts title, summary and link on three lines). A
    bare ``Link: <url>`` line takes the result's first line (the title).
    """
    lines = str(output or "").splitlines()
    first = next((ln for ln in lines if ln.strip()), "")
    out: list[tuple[str, str]] = []
    entry: list[str] = []
    for line in lines:
        if not line.startswith((" ", "\t")):
            entry = []
        entry.append(line)
        for m in _LINK.finditer(line):
            own = _LINK.sub("", line).strip()
            if re.fullmatch(r"(?:Link:)?\s*", own):
                context = " ".join(entry[:-1]).strip() or first
            else:
                context = own if not line.startswith((" ", "\t")) else " ".join(entry)
            out.append((_clean_url(m.group(0)), context))
    return out


def module_links(tool_events: Iterable[Any], manager=None) -> list[tuple[str, str]]:
    """Transcript links returned by successful tools of trusted module servers."""
    from src.mcp_trust import is_trusted_server_tool
    from src.tool_capabilities import tool_result_is_successful

    out: list[tuple[str, str]] = []
    for event in tool_events or ():
        if not isinstance(event, dict) or not tool_result_is_successful(event):
            continue
        if not is_trusted_server_tool(_event_tool_name(event), manager):
            continue
        out += links_from_output(str(event.get("output") or ""))
    return out


def rank_sources(answer: str, links: Iterable[tuple[str, str]],
                 limit: int = MAX_SOURCES) -> list[tuple[str, str]]:
    """Up to ``limit`` ``(url, label)``, most answer words shared first; deduplicated."""
    answer_words = _words(answer)
    best: dict[str, tuple[int, int, str]] = {}
    for order, (url, context) in enumerate(links):
        score = len(answer_words & _words(_LINK.sub("", context)))
        if score < MIN_OVERLAP:
            continue
        if url not in best or score > best[url][0]:
            best[url] = (score, -order, context)
    ranked = sorted(best.items(), key=lambda kv: (kv[1][0], kv[1][1]), reverse=True)
    return [(url, _label(context)) for url, (_s, _o, context) in ranked[:limit]]


def with_module_sources(answer: str, tool_events: Iterable[Any], manager=None) -> str:
    """``answer`` plus a "Sources:" list of module links, or ``answer`` unchanged."""
    text = str(answer or "")
    if not text.strip() or _ANY_VIEW_LINK.search(text) or _NOTHING_FOUND.search(text):
        return text
    try:
        links = module_links(tool_events, manager)
    except Exception:
        return text
    sources = rank_sources(text, links)
    if not sources:
        return text
    logger.info("[module-sources] answer cited no link; added %d", len(sources))
    items = "\n".join(f"- [{label}]({url})" for url, label in sources)
    return f"{text.rstrip()}\n\nSources:\n{items}"
