"""Barvis: data for the barvis-sound page (conversations, "who is this?" cards).

Everything comes from the module's MCP tools (``search_conversations``,
``list_unnamed_speakers``, ``name_speaker``) through Odysseus' MCP manager; Barvis never
opens the module's database. The tools return compact text for models, so this file
parses their documented line shapes (see docs/barvis/modules/barvis-sound.md). Lines
that don't match are skipped, so a wording change degrades to fewer rows, not an error.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

CallTool = Callable[[str, dict], Awaitable[str]]

_CONV = re.compile(
    r"^- (?P<start>\d{4}-\d{2}-\d{2} \d{2}:\d{2})–(?P<end>\d{2}:\d{2}) \"(?P<title>.*)\" "
    r"id (?P<id>\S+); people: (?P<people>.*)$")
_UNNAMED = re.compile(
    r"^- (?P<label>.+?) in \"(?P<title>.*)\" (?P<when>\d{4}-\d{2}-\d{2} \d{2}:\d{2}) "
    r"\(conversation id (?P<conv>[^)]+)\), (?P<lines>\d+) lines"
    r"(?:; sounds like (?P<guess>.+) \((?P<score>[0-9.]+)\))?$")
_QUOTE = re.compile(r"^#(?P<id>\d+) \"(?P<text>.*)\" (?P<link>https?://\S+)$")


@dataclass
class Conversation:
    id: str
    start: str
    end: str
    title: str
    people: list[str]
    summary: str = ""
    link: str = ""


@dataclass
class Quote:
    line_id: int
    text: str
    link: str


@dataclass
class UnnamedSpeaker:
    label: str
    conversation_id: str
    conversation_title: str
    when: str
    lines: int
    guess: str | None = None
    guess_score: float | None = None
    quotes: list[Quote] = field(default_factory=list)


def parse_conversations(text: str) -> list[Conversation]:
    out: list[Conversation] = []
    for raw in (text or "").splitlines():
        m = _CONV.match(raw)
        if m:
            people = [] if m["people"].strip() in {"", "—"} else [
                p.strip() for p in m["people"].split(",") if p.strip()]
            out.append(Conversation(m["id"], m["start"], m["end"], m["title"], people))
            continue
        if not out or not raw.startswith("  "):
            continue
        line = raw.strip()
        if line.startswith(("http://", "https://")) and not out[-1].link:
            out[-1].link = line
        elif line and not out[-1].summary and not out[-1].link:
            out[-1].summary = line
    return out


def parse_unnamed(text: str) -> list[UnnamedSpeaker]:
    out: list[UnnamedSpeaker] = []
    for raw in (text or "").splitlines():
        m = _UNNAMED.match(raw)
        if m:
            out.append(UnnamedSpeaker(
                m["label"], m["conv"], m["title"], m["when"], int(m["lines"]),
                m["guess"], float(m["score"]) if m["score"] else None))
            continue
        q = _QUOTE.match(raw.strip())
        if q and out and raw.startswith("  "):
            out[-1].quotes.append(Quote(int(q["id"]), q["text"], q["link"]))
    return out


def as_dicts(rows: list[Any]) -> list[dict]:
    return [asdict(r) for r in rows]


def server_id(manager: Any, key: str) -> str | None:
    """Id of the connected MCP server called ``key`` (name or id)."""
    key = key.casefold()
    for sid, conn in (getattr(manager, "_connections", {}) or {}).items():
        conn = conn or {}
        if conn.get("status") != "connected":
            continue
        if str(sid).casefold() == key or str(conn.get("name") or "").strip().casefold() == key:
            return sid
    return None


async def conversations(call: CallTool, *, query: str = "", person: str = "",
                        since: str = "", until: str = "", limit: int = 30) -> list[dict]:
    text = await call("search_conversations", {
        "query": query, "person": person, "since": since, "until": until,
        "limit": max(1, min(int(limit), 50))})
    return as_dicts(parse_conversations(text))


async def unnamed_speakers(call: CallTool, limit: int = 10) -> list[dict]:
    text = await call("list_unnamed_speakers", {"limit": max(1, min(int(limit), 50))})
    return as_dicts(parse_unnamed(text))


async def name_speaker(call: CallTool, conversation_id: str, speaker_label: str,
                       name: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > 100:
        raise ValueError("name must be 1-100 characters")
    return await call("name_speaker", {"conversation_id": conversation_id,
                                       "speaker_label": speaker_label, "name": name,
                                       "remember_voice": True})
