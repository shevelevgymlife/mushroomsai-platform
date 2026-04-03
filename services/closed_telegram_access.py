"""
Закрытые Telegram-канал и чаты по подписке: настройки в platform_settings,
флаги по тарифам (closed_access), синхронизация участия через основного бота.
"""
from __future__ import annotations

import html as html_module
import json
import logging
from typing import Any

import sqlalchemy as sa

from db.database import database
from db.models import platform_settings, users

logger = logging.getLogger(__name__)

CONFIG_KEY = "closed_telegram_access_config"

# Подписи кнопок главного бота (совпадают с bot/handlers/closed_telegram.py)
TG_BTN_CLOSED_CHANNEL = "📢 Закрытый канал (библиотека)"
TG_BTN_CLOSED_GROUP = "👥 Закрытая группа"
TG_BTN_CLOSED_CONSULT = "💬 Закрытый чат (консультации)"

DEFAULT_CONFIG: dict[str, Any] = {
    "channel_enabled": False,
    "channel_invite_url": "",
    "channel_chat_id": "",
    "group_enabled": False,
    "group_invite_url": "",
    "group_chat_id": "",
    "consult_enabled": False,
    "consult_invite_url": "",
    "consult_chat_id": "",
    "instructions": "",
}


def _parse_chat_id(raw: str | None) -> int | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def normalize_closed_telegram_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(DEFAULT_CONFIG)
    if not raw or not isinstance(raw, dict):
        return out
    for k in DEFAULT_CONFIG:
        if k not in raw:
            continue
        v = raw[k]
        if k.endswith("_enabled"):
            out[k] = bool(v)
        elif k.endswith("_chat_id"):
            out[k] = str(v).strip()[:32] if v is not None else ""
        elif k in ("channel_invite_url", "group_invite_url", "consult_invite_url"):
            out[k] = str(v).strip()[:2048] if v is not None else ""
        elif k == "instructions":
            out[k] = str(v).strip()[:12000] if v is not None else ""
    return out


async def load_closed_telegram_config() -> dict[str, Any]:
    try:
        row = await database.fetch_one(
            platform_settings.select().where(platform_settings.c.key == CONFIG_KEY)
        )
        if not row or not row.get("value"):
            return normalize_closed_telegram_config(None)
        return normalize_closed_telegram_config(json.loads(row["value"]))
    except Exception:
        logger.debug("load_closed_telegram_config failed", exc_info=True)
        return normalize_closed_telegram_config(None)


async def save_closed_telegram_config(cfg: dict[str, Any]) -> None:
    raw = json.dumps(normalize_closed_telegram_config(cfg), ensure_ascii=False)
    exists = await database.fetch_one(
        platform_settings.select().where(platform_settings.c.key == CONFIG_KEY)
    )
    if exists:
        await database.execute(
            platform_settings.update()
            .where(platform_settings.c.key == CONFIG_KEY)
            .values(value=raw)
        )
    else:
        await database.execute(platform_settings.insert().values(key=CONFIG_KEY, value=raw))


def plan_closed_access(plan_meta: dict[str, Any] | None) -> dict[str, bool]:
    raw = (plan_meta or {}).get("closed_access")
    if not isinstance(raw, dict):
        return {"channel": False, "group": False, "consult": False}
    return {
        "channel": bool(raw.get("channel")),
        "group": bool(raw.get("group")),
        "consult": bool(raw.get("consult")),
    }


def closed_resource_invite_ready(cfg: dict[str, Any], key: str) -> bool:
    en = f"{key}_enabled"
    url = f"{key}_invite_url"
    return bool(cfg.get(en)) and bool((cfg.get(url) or "").strip())


async def closed_access_entitlement_for_user(
    _user_id: int,
    *,
    is_staff: bool,
    plan_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Возвращает для канала / группы / консультаций: entitled (bool), url (str|None).
    """
    cfg = await load_closed_telegram_config()
    ca = plan_closed_access(plan_meta)
    keys = ("channel", "group", "consult")
    out: dict[str, dict[str, Any]] = {}
    any_entitled = False
    for k in keys:
        url = ((cfg.get(f"{k}_invite_url") or "").strip() or None)
        ready = closed_resource_invite_ready(cfg, k)
        if is_staff:
            ent = ready and bool(url)
        else:
            ent = bool(ready and url and ca.get(k))
        if ent:
            any_entitled = True
        out[k] = {"entitled": ent, "url": url if ent else None}
    return {"resources": out, "any_entitled": any_entitled, "config": cfg}


async def attach_closed_telegram_to_user(u: dict) -> None:
    """Поля для шаблонов: closed_tg_drawer, closed_tg, closed_tg_any."""
    u["closed_tg_drawer"] = False
    u["closed_tg_any"] = False
    u["closed_tg"] = {"channel": None, "group": None, "consult": None}
    uid = u.get("id")
    if uid is None:
        return
    uid = int(u.get("primary_user_id") or uid)
    role = (u.get("role") or "user").lower()
    is_staff = role in ("admin", "moderator")
    try:
        from services.subscription_service import check_subscription
        from services.payment_plans_catalog import get_effective_plans, drawer_menu_effective

        eff_plan = await check_subscription(uid)
        plans = await get_effective_plans()
        plan_meta = plans.get(eff_plan) or plans.get("free") or {}
        ent = await closed_access_entitlement_for_user(
            uid,
            is_staff=is_staff,
            plan_meta=plan_meta,
        )
        if not is_staff:
            if eff_plan == "free":
                ent["any_entitled"] = False
                for k in ent["resources"]:
                    ent["resources"][k] = {"entitled": False, "url": None}
            else:
                pdm = drawer_menu_effective(plan_meta)
                if pdm.get("closed_telegram") is False:
                    ent["any_entitled"] = False
                    for k in ent["resources"]:
                        ent["resources"][k] = {"entitled": False, "url": None}

        res = ent["resources"]
        u["closed_tg"] = {
            "channel": res["channel"]["url"],
            "group": res["group"]["url"],
            "consult": res["consult"]["url"],
        }
        u["closed_tg_any"] = bool(ent["any_entitled"])
        if is_staff:
            any_cfg = any(
                closed_resource_invite_ready(ent["config"], k) and (ent["config"].get(f"{k}_invite_url") or "").strip()
                for k in ("channel", "group", "consult")
            )
            u["closed_tg_drawer"] = any_cfg
            u["closed_tg_staff_preview"] = True
        else:
            u["closed_tg_staff_preview"] = False
            u["closed_tg_drawer"] = bool(ent["any_entitled"])
    except Exception:
        logger.debug("attach_closed_telegram_to_user failed uid=%s", uid, exc_info=True)


_REENTRY_LABELS = {
    "channel": "Закрытый канал (библиотека)",
    "group": "Закрытая группа",
    "consult": "Закрытый чат консультаций",
}


async def _send_closed_chats_reentry_dm(tg_user_id: int, items: list[tuple[str, str]]) -> None:
    """Личка: сняли бан — ссылки для повторного входа."""
    from config import settings

    token = (getattr(settings, "TELEGRAM_TOKEN", None) or "").strip()
    if not token or not items:
        return
    lines: list[str] = []
    for key, url in items:
        label = _REENTRY_LABELS.get(key, key)
        u = (url or "").strip()
        if not u:
            continue
        safe_href = html_module.escape(u, quote=True)
        safe_lbl = html_module.escape(label)
        lines.append(f"• {safe_lbl} — <a href=\"{safe_href}\">войти по ссылке</a>")
    if not lines:
        return
    body = (
        "✅ <b>Ограничения сняты</b>\n\n"
        "По вашей подписке бот снял блокировку в закрытых чатах. "
        "Зайдите <b>с этого же аккаунта Telegram</b> по ссылке:\n\n"
        + "\n".join(lines)
        + "\n\nЕсли откроется заявка на вступление — бот может одобрить её автоматически."
    )
    try:
        from telegram import Bot

        bot = Bot(token=token)
        await bot.send_message(chat_id=int(tg_user_id), text=body, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.info("closed chats reentry DM failed tg_id=%s: %s", tg_user_id, e)


def _tg_id_for_user_row(row: dict | None) -> int | None:
    if not row:
        return None
    for k in ("tg_id", "linked_tg_id"):
        v = row.get(k)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return None


async def sync_user_telegram_closed_chats(user_id: int, *, notify_reentry: bool = False) -> None:
    """
    Выдать/забрать доступ: ban при отсутствии права, unban при появлении (супергруппа/канал).
    Нужны chat_id в настройках и права бота (бан участников / одобрение заявок).

    notify_reentry: после успешного unban отправить в личку ссылки для входа
    (включать при активации/продлении подписки, не при истечении).
    """
    from config import settings

    token = (getattr(settings, "TELEGRAM_TOKEN", None) or "").strip()
    if not token:
        return

    uid = int(user_id)
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        return
    row = dict(row)
    tg_user_id = _tg_id_for_user_row(row)
    if not tg_user_id:
        return

    role = (row.get("role") or "user").lower()
    is_staff = role in ("admin", "moderator")

    from services.subscription_service import check_subscription
    from services.payment_plans_catalog import get_effective_plans, drawer_menu_effective

    eff_plan = await check_subscription(uid)
    plans = await get_effective_plans()
    plan_meta = plans.get(eff_plan) or plans.get("free") or {}

    if not is_staff:
        if eff_plan == "free":
            ca = {"channel": False, "group": False, "consult": False}
        else:
            ca = plan_closed_access(plan_meta)
            pdm = drawer_menu_effective(plan_meta)
            if pdm.get("closed_telegram") is False:
                ca = {"channel": False, "group": False, "consult": False}
    else:
        ca = {"channel": True, "group": True, "consult": True}

    cfg = await load_closed_telegram_config()

    try:
        from telegram import Bot

        bot = Bot(token=token)
    except Exception:
        return

    reentry_links: list[tuple[str, str]] = []

    for key in ("channel", "group", "consult"):
        chat_id = _parse_chat_id(cfg.get(f"{key}_chat_id"))
        if chat_id is None:
            continue
        if not cfg.get(f"{key}_enabled"):
            should_member = False
        elif is_staff:
            should_member = True
        else:
            should_member = bool(
                ca.get(key)
                and (cfg.get(f"{key}_invite_url") or "").strip()
            )
        invite = (cfg.get(f"{key}_invite_url") or "").strip()
        try:
            if should_member:
                await bot.unban_chat_member(chat_id, tg_user_id, only_if_banned=True)
                if notify_reentry and invite:
                    reentry_links.append((key, invite))
            else:
                await bot.ban_chat_member(chat_id, tg_user_id)
        except Exception as e:
            logger.info(
                "sync closed tg %s chat=%s user=%s should=%s: %s",
                key,
                chat_id,
                tg_user_id,
                should_member,
                e,
            )

    if notify_reentry and reentry_links and tg_user_id:
        await _send_closed_chats_reentry_dm(int(tg_user_id), reentry_links)


async def approve_chat_join_request_if_entitled(chat_id: int, from_user_id: int) -> bool:
    """True если одобрили или уже ок; False если отклонили или пропуск."""
    from config import settings

    token = (getattr(settings, "TELEGRAM_TOKEN", None) or "").strip()
    if not token:
        return False

    row = await database.fetch_one(
        users.select().where(
            sa.or_(users.c.tg_id == from_user_id, users.c.linked_tg_id == from_user_id)
        )
    )
    if not row:
        await _decline_join(token, chat_id, from_user_id)
        return False
    row = dict(row)
    uid = int(row.get("primary_user_id") or row["id"])
    if uid != int(row["id"]):
        primary = await database.fetch_one(users.select().where(users.c.id == uid))
        if primary:
            row = dict(primary)

    role = (row.get("role") or "user").lower()
    is_staff = role in ("admin", "moderator")

    from services.subscription_service import check_subscription
    from services.payment_plans_catalog import get_effective_plans, drawer_menu_effective

    eff_plan = await check_subscription(uid)
    plans = await get_effective_plans()
    plan_meta = plans.get(eff_plan) or plans.get("free") or {}
    cfg = await load_closed_telegram_config()

    key = _which_resource_for_chat(cfg, chat_id)
    if not key:
        return False

    if not cfg.get(f"{key}_enabled"):
        await _decline_join(token, chat_id, from_user_id)
        return False

    if is_staff:
        ok = True
    elif eff_plan == "free":
        ok = False
    else:
        ca = plan_closed_access(plan_meta)
        pdm = drawer_menu_effective(plan_meta)
        if pdm.get("closed_telegram") is False:
            ok = False
        else:
            ok = bool(ca.get(key))

    try:
        from telegram import Bot

        bot = Bot(token=token)
        if ok:
            await bot.approve_chat_join_request(chat_id, from_user_id)
            return True
        await bot.decline_chat_join_request(chat_id, from_user_id)
    except Exception as e:
        logger.info("join request chat=%s user=%s ok=%s: %s", chat_id, from_user_id, ok, e)
    return False


async def _decline_join(token: str, chat_id: int, user_id: int) -> None:
    try:
        from telegram import Bot

        bot = Bot(token=token)
        await bot.decline_chat_join_request(chat_id, user_id)
    except Exception:
        pass


def _which_resource_for_chat(cfg: dict[str, Any], chat_id: int) -> str | None:
    for key in ("channel", "group", "consult"):
        cid = _parse_chat_id(cfg.get(f"{key}_chat_id"))
        if cid == chat_id:
            return key
    return None


async def closed_telegram_keyboard_rows(_internal_user_id: int) -> list[list[Any]]:
    """Три кнопки закрытых ресурсов — у всех пользователей бота (доступ по подписке проверяется по нажатию)."""
    from telegram import KeyboardButton

    return [
        [KeyboardButton(TG_BTN_CLOSED_CHANNEL)],
        [KeyboardButton(TG_BTN_CLOSED_GROUP)],
        [KeyboardButton(TG_BTN_CLOSED_CONSULT)],
    ]
