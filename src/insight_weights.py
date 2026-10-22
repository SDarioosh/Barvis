"""Fork: rule weights for the Today insight, learned from the outcome log (Phase 3.2).

Stub from the Phase 3 skeleton; stream B fills it in. Contract:
docs/fork/today-insight.md#weights.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Any


def current(owner: str | None, today: Date, *, now: datetime | None = None) -> dict:
    """Today's weights (recomputed at most once a day); empty ``keys`` when none."""
    return {"version": 1, "date": today.isoformat(), "keys": {}}


def candidate_weight(candidate: dict, weights: dict | None) -> int:
    """The sum of the weights of the candidate's keys, clamped to [-6, 6]."""
    return 0


def contributions(candidate: dict, weights: dict | None) -> list[dict[str, Any]]:
    return []
