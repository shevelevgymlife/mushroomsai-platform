"""Оценка связки одним сообщением в ЛС: «связка anti_stress +»."""
from __future__ import annotations

import re
from typing import Optional

from db.database import database
from db.models import wellness_bundle_feedback
from services.mushroom_therapy_kb import BUNDLES

_R1 = re.compile(
    r"^\s*(?:связка|bundle)\s+([a-z0-9_]+)\s*([+\-]|👍|👎|плюс|минус)\s*$",
    re.IGNORECASE,
)
_R2 = re.compile(
    r"^\s*(?:связка|bundle)\s*([+\-]|👍|👎|плюс|минус)\s+([a-z0-9_]+)\s*$",
    re.IGNORECASE,
)


def parse_bundle_feedback_command(text: str) -> Optional[tuple[str, int]]:
    raw = (text or "").strip()
    if not raw or len(raw) > 120:
        return None
    m = _R1.match(raw)
    if m:
        bid, vv = m.group(1).lower(), m.group(2).lower()
    else:
        m2 = _R2.match(raw)
        if not m2:
            return None
        vv, bid = m2.group(1).lower(), m2.group(2).lower()
    if bid not in BUNDLES:
        return None
    if vv in ("+", "👍", "плюс"):
        vote = 1
    elif vv in ("-", "👎", "минус"):
        vote = -1
    else:
        return None
    return bid, vote


async def record_bundle_feedback(
    user_id: int,
    bundle_id: str,
    vote: int,
    *,
    direct_message_id: Optional[int] = None,
) -> None:
    await database.execute(
        wellness_bundle_feedback.insert().values(
            user_id=int(user_id),
            bundle_id=(bundle_id or "")[:64],
            vote=int(vote),
            source="dm_command",
            direct_message_id=direct_message_id,
        )
    )
