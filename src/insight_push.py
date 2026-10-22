"""Fork: one phone push a day for the Today insight (Phase 3.3).

Stub from the Phase 3 skeleton; stream D fills it in. Contract:
docs/fork/today-insight.md#push.
"""

from __future__ import annotations

from datetime import datetime


async def maybe_push(owner: str | None, insight: dict, *, now: datetime | None = None,
                     push=None) -> bool:
    return False
