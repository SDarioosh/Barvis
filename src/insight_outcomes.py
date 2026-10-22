"""Fork: the Today insight's outcome log (Phase 3.1).

Stub from the Phase 3 skeleton; stream A fills it in. Contract:
docs/fork/today-insight.md#outcome-log.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime


def observe(owner: str | None, insight: dict, inputs: dict, *,
            now: datetime | None = None) -> dict:
    return {}


def owner_marks(owner: str | None, today: Date) -> dict:
    """The owner's marks on today's insight: ``dismissed`` refs and ``promoted`` refs."""
    return {"dismissed": set(), "promoted": []}


def apply_owner_order(insight: dict, outcome: dict | None) -> dict:
    return dict(insight)


def pushed(owner: str | None, day: str) -> str | None:
    return None
