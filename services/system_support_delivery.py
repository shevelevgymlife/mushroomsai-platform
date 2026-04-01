"""Системные уведомления от имени технической поддержки NEUROFUNGI: ЛС + Telegram."""
from __future__ import annotations

import html
import logging
from typing import Any, Optional

import sqlalchemy as sa

from config import settings
from db.database import database
from db.models import direct_messages, users

logger = logging.getLogger(__name__)

NEUROFUNGI_AI_DISPLAY_NAME = "NeuroFungi AI"


async def _fallback_support_user_id() -> Optional[int]:
    """TECH_SUPPORT_USER_ID → ADMIN_EMAIL → первый admin."""
    tid = int(getattr(settings, "TECH_SUPPORT_USER_ID", 0) or 0)
    if tid > 0:
        row = await database.fetch_one(sa.select(users.c.id).where(users.c.id == tid))
        if row:
            return int(row["id"])
    em = (settings.ADMIN_EMAIL or "").strip()
    if em:
        row = await database.fetch_one(sa.select(users.c.id).where(users.c.email == em).limit(1))
        if row:
            return int(row["id"])
    row = await database.fetch_one(
        sa.select(users.c.id).where(users.c.role == "admin").order_by(users.c.id.asc()).limit(1)
    )
    return int(row["id"]) if row else None


async def resolve_neurofungi_ai_user_id() -> Optional[int]:
    """
    Единый аккаунт NeuroFungi AI в ЛС (дневник, системные оповещения).
    NEUROFUNGI_AI_USER_ID → иначе как техподдержка.
    """
    nid = int(getattr(settings, "NEUROFUNGI_AI_USER_ID", 0) or 0)
    if nid > 0:
        row = await database.fetch_one(sa.select(users.c.id).where(users.c.id == nid))
        if row:
            return int(row["id"])
    return await _fallback_support_user_id()


async def resolve_support_sender_id() -> Optional[int]:
    """Совместимость: то же, что resolve_neurofungi_ai_user_id()."""
    return await resolve_neurofungi_ai_user_id()


async def resolve_wellness_dm_sender_id(recipient_user_id: int) -> Optional[int]:
    """
    users.id для исходящих ЛС дневника (промпты, подтверждения статистики, отбивки).
    Должен отличаться от получателя: иначе sync_direct_messages_pair не переносит сообщение
    в chat_messages (условие uid != other_id), и в мессенджере /chats пусто, хотя строка в
    direct_messages есть и Telegram-пинг мог сработать.
    """
    rid = int(recipient_user_id)
    base = await resolve_neurofungi_ai_user_id()
    if base and int(base) != rid:
        return int(base)

    nid = int(getattr(settings, "NEUROFUNGI_AI_USER_ID", 0) or 0)
    tid = int(getattr(settings, "TECH_SUPPORT_USER_ID", 0) or 0)
    for cand in (nid, tid):
        if cand > 0 and cand != rid:
            row = await database.fetch_one(sa.select(users.c.id).where(users.c.id == cand))
            if row:
                logger.warning(
                    "wellness_dm_sender: дефолтный AI id совпадает с получателем %s; используем из env user id=%s",
                    rid,
                    cand,
                )
                return int(row["id"])

    alt = await database.fetch_one(
        sa.select(users.c.id)
        .where(users.c.id != rid)
        .where(users.c.role == "admin")
        .order_by(users.c.id.asc())
        .limit(1)
    )
    if alt:
        logger.warning(
            "wellness_dm_sender: аккаунт AI совпадает с получателем %s; ЛС уходит от другого admin id=%s. "
            "Надёжно: создайте отдельного пользователя NeuroFungi AI и NEUROFUNGI_AI_USER_ID в Render.",
            rid,
            alt["id"],
        )
        return int(alt["id"])

    logger.error(
        "wellness_dm_sender: нет отправителя ЛС — получатель %s и единственный AI это один users.id. "
        "Создайте второй аккаунт на сайте для NeuroFungi AI и укажите NEUROFUNGI_AI_USER_ID.",
        rid,
    )
    return None


async def all_legacy_neurofungi_ai_peer_ids() -> set[int]:
    """Все id, которые могли быть «ботом» — для свёртки чатов и ответов дневнику."""
    ids: set[int] = set()
    n = int(getattr(settings, "NEUROFUNGI_AI_USER_ID", 0) or 0)
    if n > 0:
        ids.add(n)
    t = int(getattr(settings, "TECH_SUPPORT_USER_ID", 0) or 0)
    if t > 0:
        ids.add(t)
    em = (settings.ADMIN_EMAIL or "").strip()
    if em:
        row = await database.fetch_one(sa.select(users.c.id).where(users.c.email == em).limit(1))
        if row:
            ids.add(int(row["id"]))
    row = await database.fetch_one(
        sa.select(users.c.id).where(users.c.role == "admin").order_by(users.c.id.asc()).limit(1)
    )
    if row:
        ids.add(int(row["id"]))
    coach = await resolve_neurofungi_ai_user_id()
    if coach:
        ids.add(int(coach))
    return {x for x in ids if x > 0}


def _parse_last_at(item: dict[str, Any]) -> str:
    return (item.get("last_at") or "") or ""


async def collapse_neurofungi_personal_chats_in_api_list(items: list[dict], _viewer_uid: int) -> list[dict]:
    """Один ряд NeuroFungi AI вместо нескольких ЛС с разными legacy-аккаунтами."""
    peer_ids = await all_legacy_neurofungi_ai_peer_ids()
    if not peer_ids:
        return items
    coach = await resolve_neurofungi_ai_user_id()
    non_ai: list[dict] = []
    ai_items: list[dict] = []
    for item in items:
        if (item.get("type") or "") == "personal" and item.get("partner_id") in peer_ids:
            ai_items.append(item)
        else:
            non_ai.append(item)
    if not ai_items:
        return items
    best = max(ai_items, key=_parse_last_at)
    ur = sum(int(x.get("unread") or 0) for x in ai_items)
    best = dict(best)
    best["name"] = NEUROFUNGI_AI_DISPLAY_NAME
    best["unread"] = ur
    best["is_neurofungi_ai"] = True
    if coach:
        best["partner_id"] = int(coach)
    merged = non_ai + [best]
    merged.sort(key=_parse_last_at, reverse=True)
    return merged


async def deliver_system_support_notification(
    *,
    recipient_user_id: int,
    body_plain: str,
    telegram_html: Optional[str] = None,
    send_telegram: bool = True,
) -> dict:
    """
    Дублирует в ЛС внутри приложения (от техподдержки) и при необходимости в Telegram.
    body_plain — текст без префикса; в ЛС добавится шапка «Системные оповещения · NEUROFUNGI AI».
    Запись в direct_messages синхронизируется в мессенджер (/chats).
    """
    body = (body_plain or "").strip()
    if not body:
        return {"ok": False, "error": "empty"}

    target = await database.fetch_one(users.select().where(users.c.id == int(recipient_user_id)))
    if not target:
        return {"ok": False, "error": "user not found"}

    notify_uid = int(target.get("primary_user_id") or recipient_user_id)
    sid = await resolve_neurofungi_ai_user_id()
    dm_mid = None
    if not sid:
        logger.warning("system_support_delivery: no support sender id, skipping DM")
    else:
        dm_text = "Системные оповещения · NEUROFUNGI AI\n\n" + body
        try:
            dm_row = await database.fetch_one_write(
                direct_messages.insert()
                .values(
                    sender_id=sid,
                    recipient_id=notify_uid,
                    text=dm_text,
                    is_read=False,
                    is_system=True,
                )
                .returning(direct_messages.c.id)
            )
            dm_mid = int(dm_row["id"]) if dm_row and dm_row.get("id") is not None else None
            if dm_mid:
                try:
                    from services.legacy_dm_chat_sync import sync_direct_messages_pair

                    await sync_direct_messages_pair(
                        int(sid), int(notify_uid), broadcast_legacy_dm_id=dm_mid
                    )
                except Exception:
                    logger.exception(
                        "system_support_delivery: chat sync failed sid=%s uid=%s", sid, notify_uid
                    )
        except Exception:
            logger.exception("system_support_delivery: DM insert failed uid=%s", recipient_user_id)

    tg_id = target.get("tg_id") or target.get("linked_tg_id")
    if not tg_id:
        fam = await database.fetch_one(
            users.select()
            .where(users.c.primary_user_id == notify_uid)
            .where(sa.or_(users.c.tg_id.is_not(None), users.c.linked_tg_id.is_not(None)))
            .order_by(users.c.id.asc())
            .limit(1)
        )
        if fam:
            tg_id = fam.get("tg_id") or fam.get("linked_tg_id")

    tg_ok = False
    if send_telegram and tg_id:
        tg_msg = telegram_html
        if not tg_msg:
            esc = html.escape(body)
            tg_msg = (
                "<b>Системные оповещения · NEUROFUNGI AI</b>\n\n"
                + esc.replace("\n", "<br/>")
            )
        try:
            from services.notify_user_stub import notify_user

            await notify_user(int(tg_id), tg_msg)
            tg_ok = True
        except Exception as e:
            logger.warning("system_support_delivery: telegram failed: %s", e)

    return {"ok": True, "telegram_sent": tg_ok, "dm_sent": bool(sid), "dm_id": dm_mid}
