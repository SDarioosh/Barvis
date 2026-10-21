"""Fork: pasted text with a rewrite request is text to edit, not a tool request.

Upstream's ``inline_text_transformation`` (``src/turn_contract.py``) treats
"fix the grammar: <text>" as data to edit, so the turn gets no tools. It only
knows that one form: an instruction first, then a colon. People more often
paste the text first and ask afterwards:

    Hi Alex, I wanted to follow up on the meeting ...
    Thanks, Sam

    Make this sound grammatically correct and with a good flow

That message was read word by word. "meeting" and "email" in the pasted draft
put it in the calendar and email families, the chat was promoted to agent
mode, and a small model looked up the contact and searched the mailbox
instead of rewriting the text.

:func:`pasted_text_rewrite` recognises that shape: the first or last line is a
short request to edit, rewrite, proofread or translate "this" (or "the email",
"the text" ...), and the rest of the message is the text. A request that also
asks for an action (send, reply to, save, schedule, search ...) is not
matched, so it keeps its tools. Called from ``inline_text_transformation``,
so every upstream caller treats the turn the same way as the colon form.
"""

from __future__ import annotations

import re
from typing import Any

# The instruction line is short; the pasted text is not.
_MAX_INSTRUCTION_CHARS = 240
_MIN_PAYLOAD_CHARS = 80

_LEAD = (
    r"(?:(?:hey|hi|ok(?:ay)?|so)\b[,!.]?\s*)?"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|please\s+|pls\s+|"
    r"i\s+(?:want|need|would\s+like|'d\s+like)\s+(?:you\s+)?to\s+|"
    r"help\s+me\s+(?:to\s+)?|try\s+to\s+)*"
)
_EDIT_VERB = (
    r"(?:re-?write|re-?phrase|re-?word|proof-?read|polish|edit|revise|improve|"
    r"clean\s+up|tidy(?:\s+up)?|tighten(?:\s+up)?|shorten|simplify|"
    r"paraphrase|refine|smooth(?:\s+out)?|punch\s+up|translate)"
)
_TEXT_NOUN = (
    r"(?:e-?mail|text|message|draft|letter|note|paragraphs?|post|reply|response|"
    r"wording|writing|sentences?|copy|bio|essay|cover\s+letter|intro(?:duction)?)"
)
_REFERENT = (
    r"(?:this|it|that|the\s+(?:above|following|below)|above|below|following|"
    r"(?:the|my|this|that)\s+(?:\w+\s+)?" + _TEXT_NOUN + r")"
)
# "Rewrite the email to be ...", "Proofread the above", "Make this sound ...".
_VERB_ON_TEXT = re.compile(
    r"^\W*" + _LEAD + r"(?:" + _EDIT_VERB + r"\b(?:\s+\w+){0,2}?\s+" + _REFERENT
    + r"|make\s+" + _REFERENT + r"\s+(?:sound|read|flow|more|less|better|clearer|"
    r"shorter|longer|simpler|nicer|smoother|grammatical))\b",
    re.I,
)
# "...and make it sound better" continues a request; it doesn't stand alone.
_CONTINUATION = re.compile(r"^\W*(?:and|then|also|but|plus|after\s+that)\b", re.I)
# "Grammar check please", "Better flow?", "Can you fix the grammar and flow".
_WRITING_QUALITY = re.compile(
    r"\b(?:grammar|grammatical(?:ly)?|spelling|typos?|punctuation|flow|tone|"
    r"wording|phrasing|clarity|readab(?:le|ility)|concise|professional|formal|"
    r"polite|friendl(?:y|ier)|proofread)\b",
    re.I,
)
_QUALITY_VERB = re.compile(
    r"\b(?:fix|correct|check|improve|make|sound|read|better|clean|polish|"
    r"proofread|rewrite|edit|tweak|adjust|smooth|tighten)\b",
    re.I,
)
# Asked together with the edit, these need tools: leave the turn to upstream.
_ACTION = re.compile(
    r"\b(?:send|forward|reply\s+to|respond\s+to|save|store|schedule|remind|"
    r"search|look\s+up|find|attach|upload|post\s+(?:it|this)|share|file\s+it|"
    r"add\s+(?:it|this)\s+to|create|calendar|"
    r"e-?mail\s+(?:it|this|that|him|her|them)|"
    r"(?:open|in)\s+(?:the\s+)?(?:editor|document)|"
    r"web|online|internet|google)\b",
    re.I,
)


def _is_rewrite_instruction(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > _MAX_INSTRUCTION_CHARS or _ACTION.search(line):
        return False
    if _CONTINUATION.search(line):
        return False
    if _VERB_ON_TEXT.search(line):
        return True
    return bool(_WRITING_QUALITY.search(line) and _QUALITY_VERB.search(line))


def pasted_text_rewrite(message: Any) -> bool:
    """True when ``message`` is pasted text plus a request to rewrite it.

    The request is the first or the last non-empty line. The other lines are
    the text, which must be longer than a one-line request.
    """
    if not isinstance(message, str):
        return False
    lines = [ln for ln in message.replace("\r\n", "\n").split("\n") if ln.strip()]
    if len(lines) < 2:
        return False
    for instruction, payload in ((lines[-1], lines[:-1]), (lines[0], lines[1:])):
        text = "\n".join(payload).strip()
        if len(text) < _MIN_PAYLOAD_CHARS:
            continue
        if _is_rewrite_instruction(instruction.rstrip(":")):
            return True
    return False
