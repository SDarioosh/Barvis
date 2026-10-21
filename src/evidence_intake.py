"""Fork: the belief ledger, between every source and Odysseus memory.

Personal-context plan stages 1.1 (evidence intake) and 1.5 (expiry and decay); the
design is in ``docs/fork/beliefs.md``.

* A **source** (the odysseus-sound facts job, later notes, chat ...) submits
  :class:`Evidence`: what it observed, with its own id for it and a link to where it
  came from. :func:`submit` files each item under a **belief**: the one this source id
  already maps to, else a belief of the same owner with the same normalized text, else
  a new ``tentative`` belief.
* A belief is ``tentative``, ``confirmed``, ``stale`` or ``rejected``.
  :func:`confirmation_for` is the rule (the source's own confirmation, or enough
  distinct sources, or enough sightings on enough days); the owner's own decisions
  (:func:`confirm`, :func:`correct`, :func:`reject`) always win and the rule never
  undoes them.
* :func:`reconcile_source` marks evidence a source no longer reports as gone; a belief
  left without live evidence goes ``stale`` unless the owner confirmed it.
  :func:`apply_decay` stales confirmed beliefs not heard of for ``BELIEF_STALE_DAYS``
  and any belief past its ``valid_until``.
* :func:`sync_memory` keeps exactly one Odysseus memory per confirmed belief (text
  ``<fact> (source: <link>)``), updated in place (memory id and ``uses`` kept), and
  deletes the copies of beliefs that are no longer confirmed. Phase 0 copies (a
  ``module_fact_id`` but no ``belief_id``) are adopted, not duplicated.

Storage: ``<DATA_DIR>/beliefs.db`` (stdlib sqlite3, WAL), one file for all owners.
Writes run under one process-wide lock; each public call is one transaction. Calls are
synchronous and short: async callers may call them directly, as the facts job does.

Settings (environment): ``BELIEF_CONFIRM_SOURCES`` (2), ``BELIEF_CONFIRM_TIMES`` (3),
``BELIEF_CONFIRM_DAYS`` (2), ``BELIEF_STALE_DAYS`` (180).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import threading
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_NAME = "beliefs.db"
MODULE_SOURCE = "odysseus-sound"
ABOUT = ("owner", "person", "other")
CATEGORIES = ("fact", "preference", "contact")
STATUSES = ("tentative", "confirmed", "stale", "rejected")
CONFIRMED_BY = ("source", "owner", "rule")
EVENT_KINDS = ("added", "evidence", "confirmed", "stale", "rejected", "corrected",
               "restored", "retired_upstream")
NORM_VERSION = 1  # PRAGMA user_version: matching keys of this version
EVENTS_KEPT = 50  # per belief: the history stays small
PREFERENCE_WORDS = frozenset({
    "like", "likes", "prefer", "prefers", "love", "loves", "hate", "hates", "enjoy",
    "enjoys", "favorite", "favourite", "want", "wants", "dislike",
})
# Memory fields only a belief with odysseus-sound evidence carries (the Phase 0 shape).
MODULE_FIELDS = ("module_fact_id", "times_said", "days", "first_said", "last_said")
_RESERVED_PAYLOAD = ("gone", "gone_at")
_WRITE = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS beliefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    about TEXT NOT NULL DEFAULT 'other' CHECK (about IN ('owner', 'person', 'other')),
    text TEXT NOT NULL,
    norm TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'fact'
        CHECK (category IN ('fact', 'preference', 'contact')),
    status TEXT NOT NULL DEFAULT 'tentative'
        CHECK (status IN ('tentative', 'confirmed', 'stale', 'rejected')),
    confirmed_by TEXT CHECK (confirmed_by IN ('source', 'owner', 'rule')),
    first_seen TEXT,
    last_seen TEXT,
    valid_until TEXT,
    memory_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    owner_text TEXT,
    owner_norm TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS beliefs_owner_status ON beliefs (owner, status);
CREATE INDEX IF NOT EXISTS beliefs_owner_norm ON beliefs (owner, norm);
CREATE INDEX IF NOT EXISTS beliefs_owner_owner_norm ON beliefs (owner, owner_norm);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    belief_id INTEGER NOT NULL REFERENCES beliefs (id) ON DELETE CASCADE,
    owner TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    about TEXT NOT NULL DEFAULT 'other',
    category TEXT NOT NULL DEFAULT 'fact',
    text TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '',
    seen_first TEXT,
    seen_last TEXT,
    times INTEGER NOT NULL DEFAULT 1,
    days INTEGER NOT NULL DEFAULT 1,
    source_confirmed INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    UNIQUE (belief_id, source, source_id),
    UNIQUE (owner, source, source_id)
);
CREATE INDEX IF NOT EXISTS evidence_belief ON evidence (belief_id);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    belief_id INTEGER NOT NULL REFERENCES beliefs (id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('added', 'evidence', 'confirmed', 'stale',
        'rejected', 'corrected', 'restored', 'retired_upstream')),
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_belief ON events (belief_id);
"""


class BeliefNotFound(LookupError):
    """No belief with that id for that owner."""


# ----------------------------------------------------------------------- settings
@dataclass(frozen=True)
class Settings:
    confirm_sources: int = 2
    confirm_times: int = 3
    confirm_days: int = 2
    stale_days: int = 180


def _env_int(name: str, default: int) -> int:
    try:
        value = int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        value = default
    return max(1, value)


def settings() -> Settings:
    """The confirmation and decay thresholds from the environment."""
    return Settings(
        confirm_sources=_env_int("BELIEF_CONFIRM_SOURCES", 2),
        confirm_times=_env_int("BELIEF_CONFIRM_TIMES", 3),
        confirm_days=_env_int("BELIEF_CONFIRM_DAYS", 2),
        stale_days=_env_int("BELIEF_STALE_DAYS", 180),
    )


_read_settings = settings  # confirmation_for's ``settings`` argument shadows the name


# ------------------------------------------------------------------------ evidence
@dataclass
class Evidence:
    """One thing a source observed. ``source_id`` is the source's own stable id for it
    (one source id maps to one belief per owner); ``link`` points to where it came from
    (required: evidence without a link is skipped); ``seen_first``/``seen_last`` are ISO
    dates or datetimes; ``times``/``days`` are the source's own totals (distinct
    sightings, distinct days); ``source_confirmed`` is the source's own verdict.
    ``valid_until`` (ISO date, optional) ends the belief on that day."""

    source: str
    source_id: str
    subject: str
    about: str
    text: str
    link: str
    seen_first: str
    seen_last: str
    times: int = 1
    days: int = 1
    source_confirmed: bool = False
    category: str | None = None
    payload: dict | None = None
    valid_until: str | None = None


_OWNER_LEADS = ("the owner", "owner", "i am", "i'm", "i've", "me", "my", "i")
_OWNER_VERBS = {"am": "is", "have": "has"}


def normalize(text: str, subject: str | None = None, about: str | None = None) -> str:
    """Matching key: lowercase, whitespace collapsed, trailing punctuation dropped.

    For a fact about the owner (``about="owner"``) a leading mention of the owner (``the
    owner``, ``owner``, ``me``, ``i``, ``i'm``, ``i am``, ``my`` or the evidence's own
    ``subject``, e.g. the owner's name) becomes the token ``owner``, and ``am``/``have``
    right after it become ``is``/``has``: "I am vegetarian", "Me is vegetarian" and "The
    owner is vegetarian." share one key. A possessive (``my``, ``the owner's``,
    ``<name>'s``) becomes ``owner's``. Facts about people and things keep the plain key."""
    norm = " ".join(str(text or "").lower().split()).rstrip(" .,;:!?…")
    if str(about or "").strip().lower() != "owner":
        return norm
    return _owner_key(norm.replace("\u2019", "'"), subject)


def _owner_key(norm: str, subject: str | None) -> str:
    name = " ".join(str(subject or "").lower().split()).replace("\u2019", "'")
    leads = sorted({*_OWNER_LEADS, *([name] if name else [])}, key=len, reverse=True)
    for lead in leads:
        if not norm.startswith(lead):
            continue
        rest = norm[len(lead):]
        if lead == "my":
            if rest.startswith(" "):
                return "owner's" + rest
            continue
        if rest.startswith("'s ") or rest == "'s":
            return "owner" + rest
        if rest and not rest.startswith(" "):
            continue  # "meals …", "ice …": not a subject mention
        if lead == "i'm":
            rest = " is" + rest
        elif lead == "i've":
            rest = " has" + rest
        elif lead == "i am":
            rest = " is" + rest
        words = rest.split()
        if words and words[0] in _OWNER_VERBS:
            words[0] = _OWNER_VERBS[words[0]]
        return " ".join(["owner", *words])
    return norm


def category_for(about: str, text: str) -> str:
    """The memory category (fact|preference|contact). About the owner -> preference when
    the text has a preference word, else fact; a person -> contact; else fact."""
    about = str(about or "").strip().lower()
    if about == "owner":
        words = set(re.findall(r"[a-z]+", str(text or "").lower()))
        return "preference" if words & PREFERENCE_WORDS else "fact"
    if about == "person":
        return "contact"
    return "fact"


def _day(value: Any) -> str | None:
    """An ISO date (YYYY-MM-DD) from a date, datetime, epoch or ISO string."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return (value.astimezone(timezone.utc) if value.tzinfo else value).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).date().isoformat()
    try:
        return date.fromisoformat(str(value).strip()[:10]).isoformat()
    except ValueError:
        return None


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _stamp(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean(item: Evidence, today: str) -> dict | None:
    """The stored shape of one evidence item, or None when it can't be filed."""
    source = str(item.source or "").strip()
    source_id = str(item.source_id if item.source_id is not None else "").strip()
    text = " ".join(str(item.text or "").split())
    link = str(item.link or "").strip()
    if not (source and source_id and text and link):
        return None
    about = str(item.about or "").strip().lower()
    about = about if about in ABOUT else "other"
    cat = str(item.category or "").strip().lower()
    cat = cat if cat in CATEGORIES else category_for(about, text)
    first, last = _day(item.seen_first), _day(item.seen_last)
    first = first or last or today
    last = last or first
    first, last = min(first, last), max(first, last)
    payload = {k: v for k, v in (item.payload or {}).items() if k not in _RESERVED_PAYLOAD}
    valid_until = _day(item.valid_until)
    if valid_until:
        payload["valid_until"] = valid_until
    return {
        "source": source, "source_id": source_id, "subject": str(item.subject or "").strip(),
        "about": about, "text": text, "norm": normalize(text, item.subject, about),
        "category": cat,
        "link": link, "seen_first": first, "seen_last": last,
        "times": max(1, _int(item.times, 1)), "days": max(1, _int(item.days, 1)),
        "source_confirmed": 1 if item.source_confirmed else 0,
        "payload": json.dumps(payload, sort_keys=True, ensure_ascii=False),
    }


# ------------------------------------------------------------------------ storage
def db_path() -> Path:
    from src.constants import DATA_DIR

    return Path(DATA_DIR) / DB_NAME


@contextmanager
def _connect(write: bool = False) -> Iterator[sqlite3.Connection]:
    """One transaction on beliefs.db (``BEGIN IMMEDIATE`` for writes)."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(SCHEMA)
        if db.execute("PRAGMA user_version").fetchone()[0] < NORM_VERSION:
            _migrate_norms(db)
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield db
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")
    finally:
        db.close()


def _migrate_norms(db: sqlite3.Connection) -> None:
    """Once per ledger file: recompute the matching keys of owner beliefs (the owner's
    leading mention became the ``owner`` token)."""
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("PRAGMA user_version").fetchone()[0] < NORM_VERSION:
            for row in db.execute("SELECT id, subject, about, text, owner_text FROM beliefs "
                                  "WHERE about = 'owner'").fetchall():
                owner_norm = (normalize(row["owner_text"], row["subject"], row["about"])
                              if row["owner_text"] else None)
                db.execute("UPDATE beliefs SET norm = ?, owner_norm = ? WHERE id = ?",
                           (normalize(row["text"], row["subject"], row["about"]), owner_norm,
                            row["id"]))
            db.execute(f"PRAGMA user_version = {NORM_VERSION}")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


def _event(db: sqlite3.Connection, belief_id: int, kind: str, detail: str, stamp: str) -> None:
    db.execute("INSERT INTO events (belief_id, at, kind, detail) VALUES (?, ?, ?, ?)",
               (belief_id, stamp, kind, detail[:300]))
    db.execute("DELETE FROM events WHERE belief_id = ? AND id NOT IN (SELECT id FROM events "
               "WHERE belief_id = ? ORDER BY id DESC LIMIT ?)",
               (belief_id, belief_id, EVENTS_KEPT))


def _payload(raw: Any) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _evidence_dict(row: Mapping) -> dict:
    payload = _payload(row["payload"])
    return {
        "id": row["id"], "belief_id": row["belief_id"], "source": row["source"],
        "source_id": row["source_id"], "subject": row["subject"], "about": row["about"],
        "category": row["category"], "text": row["text"], "link": row["link"],
        "seen_first": row["seen_first"], "seen_last": row["seen_last"],
        "times": row["times"], "days": row["days"],
        "source_confirmed": bool(row["source_confirmed"]),
        "gone": bool(payload.get("gone")), "payload": payload,
        "updated_at": row["updated_at"],
    }


def _evidence_rows(db: sqlite3.Connection, belief_ids: Iterable[int]) -> dict[int, list[dict]]:
    ids = sorted(set(belief_ids))
    out: dict[int, list[dict]] = {i: [] for i in ids}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        for row in db.execute(f"SELECT * FROM evidence WHERE belief_id IN ({marks}) "
                              "ORDER BY id", chunk):
            out[row["belief_id"]].append(_evidence_dict(row))
    return out


def primary_evidence(evidence: list[dict]) -> dict | None:
    """The most recent evidence (live before gone): its link and source represent the
    belief in memory."""
    if not evidence:
        return None
    pool = [e for e in evidence if not e.get("gone")] or evidence
    return max(pool, key=lambda e: (e.get("seen_last") or "", e.get("id") or 0))


def _belief_dict(row: Mapping, evidence: list[dict]) -> dict:
    main = primary_evidence(evidence)
    return {
        "id": row["id"], "owner": row["owner"], "subject": row["subject"],
        "about": row["about"], "text": row["text"], "owner_text": row["owner_text"],
        "shown_text": row["owner_text"] or row["text"], "category": row["category"],
        "status": row["status"], "confirmed_by": row["confirmed_by"],
        "first_seen": row["first_seen"], "last_seen": row["last_seen"],
        "valid_until": row["valid_until"], "memory_id": row["memory_id"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "note": row["note"], "link": main["link"] if main else "",
        "source": main["source"] if main else "", "evidence": evidence,
    }


def _belief(db: sqlite3.Connection, owner: str, belief_id: int) -> sqlite3.Row:
    row = db.execute("SELECT * FROM beliefs WHERE id = ? AND owner = ?",
                     (_int(belief_id, -1), owner)).fetchone()
    if row is None:
        raise BeliefNotFound(f"belief {belief_id} not found")
    return row


def _set(db: sqlite3.Connection, belief_id: int, stamp: str, **fields: Any) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE beliefs SET {cols}, updated_at = ? WHERE id = ?",
               (*fields.values(), stamp, belief_id))


# ----------------------------------------------------------------- the rule
def confirmation_for(belief: Mapping, evidence_rows: Iterable[Mapping],
                     settings: Settings | None = None) -> str | None:
    """How ``belief`` counts as confirmed: ``"owner"``, ``"source"``, ``"rule"``, or None.

    The owner's decisions win: an owner-confirmed belief is ``"owner"`` and a rejected
    one None, whatever the evidence says. Otherwise only live (not gone) evidence counts:
    (a) any item the source itself calls confirmed -> ``"source"``; (b) at least
    ``confirm_sources`` distinct sources -> ``"rule"``; (c) at least ``confirm_times``
    sightings summed across sources on at least ``confirm_days`` distinct days ->
    ``"rule"``. Distinct days across sources is the larger of the biggest per-source
    ``days`` and the number of distinct first/last-seen dates (sources report totals,
    not every date, so this never over-counts)."""
    st = settings if settings is not None else _read_settings()
    if belief["status"] == "rejected":
        return None
    if belief["confirmed_by"] == "owner":
        return "owner"
    live = [e for e in evidence_rows if not _is_gone(e)]
    if not live:
        return None
    if any(bool(e["source_confirmed"]) for e in live):
        return "source"
    if len({e["source"] for e in live}) >= st.confirm_sources:
        return "rule"
    times = sum(max(1, _int(e["times"], 1)) for e in live)
    dates = {d for e in live for d in (e["seen_first"], e["seen_last"]) if d}
    days = max(max(max(1, _int(e["days"], 1)) for e in live), len(dates))
    if times >= st.confirm_times and days >= st.confirm_days:
        return "rule"
    return None


def _is_gone(e: Mapping) -> bool:
    if isinstance(e, dict) and "gone" in e:
        return bool(e["gone"])
    return bool(_payload(e["payload"]).get("gone"))


def _expired(row: Mapping, today: date, st: Settings) -> str | None:
    """Why a belief is past it ("valid until ...", "not seen since ..."), or None."""
    valid_until = _day(row["valid_until"])
    if valid_until and valid_until < today.isoformat():
        return f"valid until {valid_until}"
    last = _day(row["last_seen"])
    if (row["confirmed_by"] != "owner" and last
            and last < (today - timedelta(days=st.stale_days)).isoformat()):
        return f"not seen since {last}"
    return None


def _refresh(db: sqlite3.Connection, belief_id: int, stamp: str, st: Settings, today: date,
             *, revive: bool = False, stale_when_empty: bool = True) -> str | None:
    """Recompute dates and status of one belief from its evidence. Returns the new status
    when it changed, else None. ``revive``: new evidence may bring a stale belief back."""
    row = db.execute("SELECT * FROM beliefs WHERE id = ?", (belief_id,)).fetchone()
    if row is None:
        return None
    evidence = _evidence_rows(db, [belief_id])[belief_id]
    fields: dict[str, Any] = {}
    if evidence:
        first = min(e["seen_first"] or e["seen_last"] for e in evidence)
        last = max(e["seen_last"] or e["seen_first"] for e in evidence)
        if (first, last) != (row["first_seen"], row["last_seen"]):
            fields.update(first_seen=first, last_seen=last)
        until = [e["payload"]["valid_until"] for e in evidence
                 if not e["gone"] and e["payload"].get("valid_until")]
        if until and max(until) != row["valid_until"]:
            fields["valid_until"] = max(until)
    if fields:
        _set(db, belief_id, stamp, **fields)
        row = db.execute("SELECT * FROM beliefs WHERE id = ?", (belief_id,)).fetchone()
    status, by = row["status"], row["confirmed_by"]
    if status == "rejected" or by == "owner":
        return None
    live = [e for e in evidence if not e["gone"]]
    if not live:
        if stale_when_empty and status != "stale":
            _set(db, belief_id, stamp, status="stale", confirmed_by=None)
            _event(db, belief_id, "stale", "no source reports it any more", stamp)
            return "stale"
        return None
    if status == "stale":
        if not revive or _expired(row, today, st):
            return None
        _set(db, belief_id, stamp, status="tentative", confirmed_by=None)
        _event(db, belief_id, "evidence", "seen again", stamp)
        status, by = "tentative", None
        changed: str | None = "tentative"
    else:
        changed = None
    rule = confirmation_for(row, live, st)
    if rule and status != "confirmed":
        _set(db, belief_id, stamp, status="confirmed", confirmed_by=rule)
        _event(db, belief_id, "confirmed", f"by {rule}", stamp)
        return "confirmed"
    if rule and rule != by:
        _set(db, belief_id, stamp, confirmed_by=rule)
    elif not rule and status == "confirmed":
        _set(db, belief_id, stamp, status="tentative", confirmed_by=None)
        _event(db, belief_id, "retired_upstream", "no longer meets the rule", stamp)
        return "tentative"
    return changed


# ----------------------------------------------------------------------- intake
def _match(db: sqlite3.Connection, owner: str, norm: str, exclude: int | None = None
           ) -> int | None:
    row = db.execute("SELECT id FROM beliefs WHERE owner = ? AND (norm = ? OR owner_norm = ?) "
                     "AND id != ? ORDER BY id LIMIT 1",
                     (owner, norm, norm, exclude or -1)).fetchone()
    return row["id"] if row else None


def _new_belief(db: sqlite3.Connection, owner: str, ev: dict, stamp: str) -> int:
    cur = db.execute(
        "INSERT INTO beliefs (owner, subject, about, text, norm, category, status, "
        "first_seen, last_seen, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'tentative', ?, ?, ?, ?)",
        (owner, ev["subject"], ev["about"], ev["text"], ev["norm"], ev["category"],
         ev["seen_first"], ev["seen_last"], stamp, stamp))
    _event(db, cur.lastrowid, "added", f"from {ev['source']}", stamp)
    return cur.lastrowid


_EV_COLS = ("subject", "about", "category", "text", "link", "seen_first", "seen_last",
            "times", "days", "source_confirmed", "payload")


def _attach(db: sqlite3.Connection, owner: str, ev: dict, stamp: str
            ) -> tuple[str, set[int], int, bool]:
    """File one item. Returns (count key, beliefs touched, beliefs added, changed)."""
    row = db.execute("SELECT * FROM evidence WHERE owner = ? AND source = ? AND source_id = ?",
                     (owner, ev["source"], ev["source_id"])).fetchone()
    if row is None:
        added = 0
        belief_id = _match(db, owner, ev["norm"])
        if belief_id is None:
            belief_id, added = _new_belief(db, owner, ev, stamp), 1
        else:
            _event(db, belief_id, "evidence", f"from {ev['source']}", stamp)
        db.execute(
            f"INSERT INTO evidence (belief_id, owner, source, source_id, {', '.join(_EV_COLS)}, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (belief_id, owner, ev["source"], ev["source_id"], *(ev[c] for c in _EV_COLS), stamp))
        return "evidence_added", {belief_id}, added, True
    old_payload = _payload(row["payload"])
    was_gone = bool(old_payload.get("gone"))
    same = (not was_gone and all(row[c] == ev[c] for c in _EV_COLS if c != "payload")
            and json.dumps({k: v for k, v in old_payload.items() if k not in _RESERVED_PAYLOAD},
                           sort_keys=True, ensure_ascii=False) == ev["payload"])
    belief_id = row["belief_id"]
    if same:
        return "unchanged", {belief_id}, 0, False
    touched, added = {belief_id}, 0
    belief = db.execute("SELECT * FROM beliefs WHERE id = ?", (belief_id,)).fetchone()
    others = db.execute("SELECT COUNT(*) FROM evidence WHERE belief_id = ? AND id != ?",
                        (belief_id, row["id"])).fetchone()[0]
    decided = belief["status"] == "rejected" or belief["confirmed_by"] == "owner"
    target = None
    # the source changed what it says
    if normalize(row["text"], row["subject"], row["about"]) != ev["norm"]:
        target = _match(db, owner, ev["norm"], exclude=belief_id)
        if others or decided or target is not None:
            # a different claim now: move it to the belief it matches, or a new one
            if target is None:
                target, added = _new_belief(db, owner, ev, stamp), 1
            else:
                _event(db, target, "evidence", f"from {ev['source']}", stamp)
            db.execute("UPDATE evidence SET belief_id = ? WHERE id = ?", (target, row["id"]))
            belief_id = target
            touched.add(target)
    if target is None and not others and not decided:
        # the belief's only evidence: it follows the source (an edit, a renamed speaker)
        new = {k: ev[k] for k in ("text", "norm", "subject", "about", "category")}
        if any(belief[k] != v for k, v in new.items()):
            _set(db, belief_id, stamp, **new)
    if was_gone:
        _event(db, belief_id, "evidence", f"back in {ev['source']}", stamp)
    cols = ", ".join(f"{c} = ?" for c in _EV_COLS)
    db.execute(f"UPDATE evidence SET {cols}, updated_at = ? WHERE id = ?",
               (*(ev[c] for c in _EV_COLS), stamp, row["id"]))
    return "evidence_updated", touched, added, True


def submit(owner: str | None, evidence: list[Evidence], *, now: datetime | None = None,
           mirror: bool = True) -> dict[str, int]:
    """File evidence from a source, apply the rule, and (``mirror``) update memory.

    Returns counts: ``beliefs_added``, ``evidence_added``, ``evidence_updated``,
    ``confirmed`` (beliefs that became confirmed), ``unchanged`` (items identical to what
    the ledger had) and ``skipped`` (items without source, id, text or link)."""
    owner = owner or ""
    now = _now(now)
    stamp, today, st = _stamp(now), now.date(), settings()
    counts = dict.fromkeys(("beliefs_added", "evidence_added", "evidence_updated",
                            "confirmed", "unchanged", "skipped"), 0)
    with _WRITE:
        with _connect(write=True) as db:
            seen: set[int] = set()
            fresh: set[int] = set()
            for item in evidence:
                ev = _clean(item, today.isoformat())
                if ev is None:
                    counts["skipped"] += 1
                    continue
                key, touched, added, changed = _attach(db, owner, ev, stamp)
                counts[key] += 1
                counts["beliefs_added"] += added
                seen |= touched
                if changed:
                    fresh |= touched
            for belief_id in sorted(seen):
                if _refresh(db, belief_id, stamp, st, today,
                            revive=belief_id in fresh) == "confirmed":
                    counts["confirmed"] += 1
        if mirror:
            sync_memory(owner, now=now)
    return counts


def reconcile_source(owner: str | None, source: str, present_ids: set[str], *,
                     now: datetime | None = None, mirror: bool = True) -> dict[str, int]:
    """Mark this source's evidence whose id is not in ``present_ids`` as gone.

    A belief left without live evidence goes stale (and leaves memory) unless the owner
    confirmed it; a rule-confirmed belief that no longer meets the rule goes back to
    tentative. Owner-confirmed and rejected beliefs keep their status. Returns counts:
    ``gone`` (evidence newly gone), ``stale`` and ``unconfirmed`` (beliefs)."""
    owner = owner or ""
    now = _now(now)
    stamp, today, st = _stamp(now), now.date(), settings()
    present = {str(i) for i in present_ids}
    counts = {"gone": 0, "stale": 0, "unconfirmed": 0}
    with _WRITE:
        with _connect(write=True) as db:
            touched: set[int] = set()
            for row in db.execute("SELECT * FROM evidence WHERE owner = ? AND source = ?",
                                  (owner, source)).fetchall():
                payload = _payload(row["payload"])
                if row["source_id"] in present or payload.get("gone"):
                    continue
                payload.update(gone=True, gone_at=stamp)
                db.execute("UPDATE evidence SET payload = ?, updated_at = ? WHERE id = ?",
                           (json.dumps(payload, sort_keys=True, ensure_ascii=False), stamp,
                            row["id"]))
                _event(db, row["belief_id"], "retired_upstream",
                       f"{source} {row['source_id']}", stamp)
                counts["gone"] += 1
                touched.add(row["belief_id"])
            for belief_id in sorted(touched):
                result = _refresh(db, belief_id, stamp, st, today)
                if result == "stale":
                    counts["stale"] += 1
                elif result == "tentative":
                    counts["unconfirmed"] += 1
        if mirror:
            sync_memory(owner, now=now)
    return counts


def apply_decay(owner: str | None, *, now: datetime | None = None,
                stale_days: int | None = None, mirror: bool = True) -> dict[str, int]:
    """Expiry (stage 1.5). A belief confirmed by its source or the rule whose last
    sighting is older than ``stale_days`` (``BELIEF_STALE_DAYS``) goes stale; so does any
    tentative or confirmed belief whose ``valid_until`` has passed. Owner-confirmed
    beliefs never decay (only an explicit ``valid_until`` ends them). Returns
    ``{"stale": n}``."""
    owner = owner or ""
    now = _now(now)
    stamp, today = _stamp(now), now.date()
    st = settings()
    if stale_days is not None:
        st = Settings(st.confirm_sources, st.confirm_times, st.confirm_days,
                      max(1, int(stale_days)))
    counts = {"stale": 0}
    with _WRITE:
        with _connect(write=True) as db:
            for row in db.execute("SELECT * FROM beliefs WHERE owner = ? AND status IN "
                                  "('tentative', 'confirmed')", (owner,)).fetchall():
                why = _expired(row, today, st)
                if why is None:
                    continue
                if why.startswith("not seen") and row["status"] != "confirmed":
                    continue  # decay demotes confirmed beliefs; tentative ones just wait
                _set(db, row["id"], stamp, status="stale", confirmed_by=None)
                _event(db, row["id"], "stale", why, stamp)
                counts["stale"] += 1
        if mirror:
            sync_memory(owner, now=now)
    return counts


# ---------------------------------------------------------------- owner actions
def _owner_action(owner: str | None, belief_id: int, now: datetime | None, apply) -> dict:
    owner = owner or ""
    now = _now(now)
    with _WRITE:
        with _connect(write=True) as db:
            row = _belief(db, owner, belief_id)
            apply(db, row, _stamp(now), now.date())
        try:
            sync_memory(owner, now=now)
        except RuntimeError as e:  # MemoryStoreUnreadable: the next facts run mirrors it
            logger.error("belief %s saved, memory not updated: %s", row["id"], e)
        return get_belief(owner, row["id"])


def confirm(owner: str | None, belief_id: int, *, now: datetime | None = None) -> dict:
    """The owner confirms a belief: confirmed by the owner, in memory, never decays."""
    def apply(db, row, stamp, _today):
        if row["status"] == "confirmed" and row["confirmed_by"] == "owner":
            return
        _set(db, row["id"], stamp, status="confirmed", confirmed_by="owner")
        _event(db, row["id"], "confirmed", "by owner", stamp)
    return _owner_action(owner, belief_id, now, apply)


def reject(owner: str | None, belief_id: int, reason: str = "", *,
           now: datetime | None = None) -> list[dict]:
    """The owner forgets a belief: rejected, out of memory, and new evidence for it stays
    rejected. Returns its evidence, so the caller can retire it at the sources too
    (``src.module_facts.propagate_forget``)."""
    reason = " ".join(str(reason or "").split())[:300]

    def apply(db, row, stamp, _today):
        if row["status"] == "rejected":
            return
        _set(db, row["id"], stamp, status="rejected", confirmed_by=None,
             note=reason or row["note"])
        _event(db, row["id"], "rejected", reason or "by owner", stamp)
    return _owner_action(owner, belief_id, now, apply)["evidence"]


def correct(owner: str | None, belief_id: int, text: str, *,
            now: datetime | None = None) -> dict:
    """The owner corrects a belief: ``owner_text`` is what memory shows from now on; the
    belief is confirmed by the owner. Raises ValueError for empty text."""
    text = " ".join(str(text or "").split())
    if not text:
        raise ValueError("a correction needs text")

    def apply(db, row, stamp, _today):
        _set(db, row["id"], stamp, owner_text=text,
             owner_norm=normalize(text, row["subject"], row["about"]),
             status="confirmed", confirmed_by="owner")
        _event(db, row["id"], "corrected", "by owner", stamp)
    return _owner_action(owner, belief_id, now, apply)


def restore(owner: str | None, belief_id: int, *, now: datetime | None = None) -> dict:
    """A rejected or stale belief goes back to tentative, then the rule applies (it may
    confirm it again). To keep an old belief for good, confirm it instead."""
    def apply(db, row, stamp, today):
        if row["status"] not in ("rejected", "stale"):
            return
        _set(db, row["id"], stamp, status="tentative", confirmed_by=None)
        _event(db, row["id"], "restored", "by owner", stamp)
        _refresh(db, row["id"], stamp, settings(), today, stale_when_empty=False)
    return _owner_action(owner, belief_id, now, apply)


# ------------------------------------------------------------------------- reads
def list_beliefs(owner: str | None, status: str | Iterable[str] | None = None,
                 subject: str | None = None, limit: int = 500) -> list[dict]:
    """This owner's beliefs with their evidence, by subject then most recently seen.
    ``status``: one status or several; ``subject``: case-insensitive exact match."""
    owner = owner or ""
    where, args = ["owner = ?"], [owner]
    if status:
        wanted = [status] if isinstance(status, str) else list(status)
        bad = [s for s in wanted if s not in STATUSES]
        if bad:
            raise ValueError(f"unknown status: {', '.join(map(str, bad))}")
        where.append(f"status IN ({','.join('?' * len(wanted))})")
        args += wanted
    if subject:
        where.append("subject = ? COLLATE NOCASE")
        args.append(subject.strip())
    with _connect() as db:
        rows = db.execute(f"SELECT * FROM beliefs WHERE {' AND '.join(where)} "
                          "ORDER BY subject COLLATE NOCASE, last_seen DESC, id "
                          "LIMIT ?", (*args, max(1, int(limit)))).fetchall()
        evidence = _evidence_rows(db, [r["id"] for r in rows])
    return [_belief_dict(r, evidence[r["id"]]) for r in rows]


def get_belief(owner: str | None, belief_id: int) -> dict:
    """One belief with its evidence and recent events (newest first). Raises
    :class:`BeliefNotFound`."""
    owner = owner or ""
    with _connect() as db:
        row = _belief(db, owner, belief_id)
        out = _belief_dict(row, _evidence_rows(db, [row["id"]])[row["id"]])
        out["events"] = [dict(e) for e in db.execute(
            "SELECT at, kind, detail FROM events WHERE belief_id = ? ORDER BY id DESC LIMIT 20",
            (row["id"],))]
    return out


def stats(owner: str | None) -> dict[str, int]:
    """Counts per status, plus ``total``."""
    out = dict.fromkeys(STATUSES, 0)
    with _connect() as db:
        for row in db.execute("SELECT status, COUNT(*) AS n FROM beliefs WHERE owner = ? "
                              "GROUP BY status", (owner or "",)):
            out[row["status"]] = row["n"]
    out["total"] = sum(out[s] for s in STATUSES)
    return out


# ------------------------------------------------------------------------- memory
def memory_fields(belief: dict) -> dict[str, Any]:
    """The memory fields that mirror a confirmed belief (besides id, owner, uses ...)."""
    evidence = belief.get("evidence") or []
    main = primary_evidence(evidence)
    link = main["link"] if main else ""
    shown = belief.get("owner_text") or belief["text"]
    fields: dict[str, Any] = {
        "text": f"{shown} (source: {link})" if link else shown,
        "category": belief["category"],
        "source": main["source"] if main else "beliefs",
        "belief_id": belief["id"],
        "subject": belief["subject"],
        "about": belief["about"],
        "link": link,
        "first_seen": belief["first_seen"] or "",
        "last_seen": belief["last_seen"] or "",
    }
    module = primary_evidence([e for e in evidence if e["source"] == MODULE_SOURCE])
    if module is not None:  # the Phase 0 copy's fields, kept for older readers
        fields.update(module_fact_id=_int(module["source_id"]), times_said=module["times"],
                      days=module["days"], first_said=module["seen_first"] or "",
                      last_said=module["seen_last"] or "")
    return fields


def sync_memory(owner: str | None, *, now: datetime | None = None) -> dict[str, int]:
    """Exactly one Odysseus memory per confirmed belief of this owner.

    A mirrored entry carries ``belief_id``; a Phase 0 copy (``source: odysseus-sound``,
    ``module_fact_id``, no ``belief_id``) is adopted by the belief that has that fact id
    as evidence. Entries are updated in place (memory id and ``uses`` kept), copies of
    beliefs that are no longer confirmed are deleted, and nothing is written when
    nothing changed. The owner's own memories and other owners' entries are never
    touched. Same discipline as upstream's memory writers: a strict read
    (``load_all_for_update``) and the save right after. Returns
    kept/added/updated/removed."""
    from src.constants import DATA_DIR
    from src.memory import MemoryManager

    owner = owner or ""
    now = _now(now)
    stamp = _stamp(now)
    counts = {"kept": 0, "added": 0, "updated": 0, "removed": 0}
    with _WRITE:
        with _connect() as db:
            rows = db.execute("SELECT * FROM beliefs WHERE owner = ? AND status = 'confirmed' "
                              "ORDER BY id", (owner,)).fetchall()
            evidence = _evidence_rows(db, [r["id"] for r in rows])
            want = {r["id"]: memory_fields(_belief_dict(r, evidence[r["id"]])) for r in rows}
            by_fact = {e["source_id"]: e["belief_id"] for e in db.execute(
                "SELECT source_id, belief_id FROM evidence WHERE owner = ? AND source = ?",
                (owner, MODULE_SOURCE))}
            linked = {r["id"]: r["memory_id"] for r in db.execute(
                "SELECT id, memory_id FROM beliefs WHERE owner = ? AND memory_id IS NOT NULL",
                (owner,))}
        manager = MemoryManager(DATA_DIR)
        entries = manager.load_all_for_update()
        out: list[dict] = []
        indexed: list[dict] = []
        removed: list[str] = []
        placed: dict[int, str] = {}
        for entry in entries:
            if (entry.get("owner") or "") != owner:
                out.append(entry)
                continue
            belief_id = entry.get("belief_id")
            if belief_id is None:
                if entry.get("source") != MODULE_SOURCE or "module_fact_id" not in entry:
                    out.append(entry)  # the owner's own memory
                    continue
                belief_id = by_fact.get(str(_int(entry.get("module_fact_id"))))
                if belief_id is None and not by_fact:
                    out.append(entry)  # the ledger hasn't heard from the module yet
                    continue
            belief_id = _int(belief_id, -1)
            if belief_id not in want or belief_id in placed:
                counts["removed"] += 1
                removed.append(str(entry.get("id") or ""))
                continue
            fields = want[belief_id]
            stale_keys = [k for k in MODULE_FIELDS if k in entry and k not in fields]
            if stale_keys or any(entry.get(k) != v for k, v in fields.items()):
                if entry.get("text") != fields["text"]:
                    indexed.append(entry)
                for key in stale_keys:
                    entry.pop(key)
                entry.update(fields, synced_at=stamp, timestamp=int(now.timestamp()))
                counts["updated"] += 1
            else:
                counts["kept"] += 1
            placed[belief_id] = str(entry.get("id") or "")
            out.append(entry)
        for belief_id, fields in want.items():
            if belief_id in placed:
                continue
            entry = manager.add_entry(fields["text"], source=fields["source"],
                                      category=fields["category"], owner=owner or None)
            entry.update(fields, synced_at=stamp, timestamp=int(now.timestamp()))
            out.append(entry)
            indexed.append(entry)
            placed[belief_id] = entry["id"]
            counts["added"] += 1
        if counts["added"] or counts["updated"] or counts["removed"]:
            manager.save(out)
            update_vector_index(indexed, removed)
        changes = {b: m for b, m in placed.items() if linked.get(b) != m}
        dropped = [b for b in linked if b not in placed]
        if changes or dropped:
            with _connect(write=True) as db:
                for belief_id, memory_id in changes.items():
                    db.execute("UPDATE beliefs SET memory_id = ? WHERE id = ?",
                               (memory_id, belief_id))
                for belief_id in dropped:
                    db.execute("UPDATE beliefs SET memory_id = NULL WHERE id = ?",
                               (belief_id,))
    return counts


def update_vector_index(indexed: list[dict], removed: list[str]) -> None:
    """Best effort, like upstream's manage_memory: the index Odysseus loaded, if any."""
    ai = sys.modules.get("src.ai_interaction")
    vector = getattr(ai, "_memory_vector", None) if ai is not None else None
    if vector is None or not getattr(vector, "healthy", False):
        return
    for entry in indexed:
        try:
            vector.add(entry["id"], entry["text"])
        except Exception:
            logger.debug("memory vector add failed", exc_info=True)
    for memory_id in removed:
        try:
            vector.remove(memory_id)
        except Exception:
            logger.debug("memory vector remove failed", exc_info=True)
