"""Слияние нескольких личных чатов с разными legacy-аккаунтами NeuroFungi AI в один thread."""
from __future__ import annotations

import logging

import sqlalchemy as sa

from db.database import database
from services.legacy_dm_chat_sync import (
    _create_personal_chat,
    _find_personal_chat,
    _recompute_last_read_for_members,
)
from services.system_support_delivery import all_legacy_neurofungi_ai_peer_ids, resolve_neurofungi_ai_user_id

logger = logging.getLogger(__name__)


async def merge_all_neurofungi_ai_personal_chats() -> None:
    coach = await resolve_neurofungi_ai_user_id()
    peers = await all_legacy_neurofungi_ai_peer_ids()
    if not coach or not peers:
        logger.info("merge_neurofungi_ai_chats: skip (no coach or no peer set)")
        return
    peer_sql = ",".join(str(int(x)) for x in sorted(peers))
    rows = await database.fetch_all(
        sa.text(
            f"""
            SELECT cm.user_id AS uid, c.id AS chat_id, cm2.user_id AS partner_id
            FROM chat_members cm
            JOIN chats c ON c.id = cm.chat_id AND c.type = 'personal'
            JOIN chat_members cm2 ON cm2.chat_id = c.id AND cm2.user_id <> cm.user_id
            WHERE cm2.user_id IN ({peer_sql})
            ORDER BY cm.user_id, c.id
            """
        )
    )
    by_uid: dict[int, list[tuple[int, int]]] = {}
    for r in rows:
        uid = int(r["uid"])
        cid = int(r["chat_id"])
        pid = int(r["partner_id"])
        by_uid.setdefault(uid, []).append((cid, pid))

    merged_n = 0
    for uid, chats in by_uid.items():
        if len(chats) <= 1:
            continue
        canonical: int | None = None
        for cid, pid in chats:
            if pid == coach:
                canonical = cid
                break
        if canonical is None:
            existing = await _find_personal_chat(uid, coach)
            if existing:
                canonical = existing
            else:
                canonical = await _create_personal_chat(uid, coach)
        for cid, _pid in chats:
            if cid == canonical:
                continue
            await database.execute(
                sa.text("UPDATE chat_messages SET chat_id = :canon WHERE chat_id = :old"),
                {"canon": canonical, "old": cid},
            )
            await database.execute(sa.text("DELETE FROM chat_members WHERE chat_id = :old"), {"old": cid})
            await database.execute(sa.text("DELETE FROM chats WHERE id = :old"), {"old": cid})
            merged_n += 1
        mems = await database.fetch_all(
            sa.text("SELECT user_id FROM chat_members WHERE chat_id = :c"),
            {"c": canonical},
        )
        uids = [int(m["user_id"]) for m in mems]
        if uids:
            await _recompute_last_read_for_members(canonical, uids)

    if merged_n:
        logger.info("merge_neurofungi_ai_chats: merged %s duplicate chat rows", merged_n)
