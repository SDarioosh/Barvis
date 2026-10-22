"""Fork: open loops and this week's plans as Today insight candidates (Phase 3.4b, c).

Stub from the Phase 3 skeleton; stream D fills it in. Contract:
docs/fork/today-insight.md#plans-and-open-loops.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime


def extra_candidates(owner: str | None, today: Date, action_rows: list[dict], *,
                     now: datetime | None = None) -> list[dict]:
    return []
