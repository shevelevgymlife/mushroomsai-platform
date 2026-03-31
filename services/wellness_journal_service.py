"""
Дневник фунготерапии: напоминания AI в ЛС (аккаунт техподдержки), сбор ответов, статистика.
Доступно при тарифе Старт / Про / Макси (включая пробный Старт), не на free.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import sqlalchemy as sa

from config import settings
from db.database import database
from db.models import users, wellness_journal_entries, platform_settings, direct_messages
from services.subscription_service import check_subscription
from services.system_support_delivery import resolve_support_sender_id
from services.legacy_dm_chat_sync import sync_direct_messages_pair

logger = logging.getLogger(__name__)

PLATFORM_KEY = "wellness_journal_globally_enabled"
ALLOWED_INTERVALS = (1, 3, 5, 7)
MSG_PREFIX = "🍄 Дневник NEUROFUNGI · AI\n\n"


async def wellness_journal_globally_enabled() -> bool:
    row = await database.fetch_one(
        platform_settings.select().where(platform_settings.c.key == PLATFORM_KEY)
    )
    if not row:
        return True
    v = (row["value"] or "").strip().lower()
    return v not in ("0", "false", "off", "no", "disabled")


async def set_wellness_journal_globally_enabled(on: bool) -> None:
    val = "true" if on else "false"
    ex = await database.fetch_one(
        platform_settings.select().where(platform_settings.c.key == PLATFORM_KEY)
    )
    if ex:
        await database.execute(
            platform_settings.update()
            .where(platform_settings.c.key == PLATFORM_KEY)
            .values(value=val)
        )
    else:
        await database.execute(platform_settings.insert().values(key=PLATFORM_KEY, value=val))


def _notify_uid(row: dict) -> int:
    return int(row.get("primary_user_id") or row["id"])


def _build_prompt_text(*, include_weekly_nudge: bool, prompt_index: int) -> str:
    site = (settings.SITE_URL or "").rstrip("/") or "https://mushroomsai.ru"
    results_url = f"{site}/account/wellness-results"
    base = (
        "Здравствуйте! Короткий опрос для вашего дневника терапии и самонаблюдения.\n\n"
        "Пожалуйста, ответьте одним сообщением (можно тезисно):\n"
        "• Как вы себя чувствуете сегодня (энергия, настроение 1–10)?\n"
        "• Какие грибы / связки сейчас принимаете (название, форма)?\n"
        "• Дозировка и время приёма (утро / день / вечер)?\n"
        "• Что замечаете в теле и в голове после приёма?\n"
        "• Были ли сдвиги по сну, тревоге, фокусу, физическим симптомам?\n"
        "• Если хотите — кратко: с чем пришли к практике и что хотите отследить.\n\n"
        f"📊 Вся история и сводки: {results_url}\n"
        "Частота напоминаний: напишите в ответ «каждый день», «раз в 3 дня», «раз в 5 дней», "
        "«раз в неделю» или «отключить дневник» (не реже одного раза в 7 дней — иначе предложим отключить сбор).\n"
    )
    if include_weekly_nudge:
        base += (
            "\n📅 Раз в неделю мы пришлём короткую сводку по вашим ответам здесь, в чате.\n"
        )
    if prompt_index > 0 and prompt_index % 5 == 0:
        base += (
            "\n💬 Напишите, пожалуйста: удобно ли вам такое общение? Хотите, чтобы я реже писала "
            "или наоборот чаще? (Можно одним словом.)\n"
        )
    return MSG_PREFIX + base


async def schedule_wellness_journal_if_paid(user_id: int) -> None:
    """Вызывать при активации платного тарифа или пробного Старт."""
    uid = int(user_id)
    plan = await check_subscription(uid)
    if plan == "free":
        return
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row or row.get("wellness_journal_opt_out"):
        return
    if row.get("wellness_next_prompt_at") is not None:
        return
    nxt = datetime.utcnow() + timedelta(hours=1)
    await database.execute(
        users.update()
        .where(users.c.id == uid)
        .values(
            wellness_next_prompt_at=nxt,
            wellness_journal_interval_days=row.get("wellness_journal_interval_days") or 1,
        )
    )


def _normalize_interval(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 1
    return n if n in ALLOWED_INTERVALS else 1


def _next_prompt_after(interval_days: int) -> datetime:
    d = _normalize_interval(interval_days)
    return datetime.utcnow() + timedelta(days=d)


async def parse_frequency_and_opt_out_from_text(user_id: int, text: str) -> None:
    """Ключевые фразы в ответе пользователя техподдержке / AI-дневнику."""
    t = (text or "").lower()
    uid = int(user_id)
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row:
        return

    opt_out = any(
        x in t
        for x in (
            "отключить дневник",
            "не собирай",
            "не пиши",
            "не надо писать",
            "отключи дневник",
            "хватит опрос",
            "не хочу дневник",
        )
    )
    if opt_out:
        await database.execute(
            users.update()
            .where(users.c.id == uid)
            .values(
                wellness_journal_opt_out=True,
                wellness_next_prompt_at=None,
            )
        )
        return

    interval = None
    if "каждый день" in t or "раз в день" in t or "ежедневно" in t:
        interval = 1
    elif "3 дня" in t or "три дня" in t or "раз в три" in t:
        interval = 3
    elif "5 дн" in t or "пять дн" in t or "раз в пять" in t:
        interval = 5
    elif "недел" in t or "раз в 7" in t:
        interval = 7

    if interval is not None:
        await database.execute(
            users.update()
            .where(users.c.id == uid)
            .values(
                wellness_journal_interval_days=interval,
                wellness_next_prompt_at=_next_prompt_after(interval),
            )
        )


async def _count_ai_prompts(user_id: int) -> int:
    r = await database.fetch_val(
        sa.select(sa.func.count())
        .select_from(wellness_journal_entries)
        .where(wellness_journal_entries.c.user_id == user_id)
        .where(wellness_journal_entries.c.role == "ai_prompt"),
    )
    return int(r or 0)


async def send_wellness_prompt_for_user(user_id: int) -> bool:
    uid = int(user_id)
    if not await wellness_journal_globally_enabled():
        return False
    plan = await check_subscription(uid)
    if plan == "free":
        return False
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row or row.get("wellness_journal_opt_out") or row.get("wellness_journal_admin_paused"):
        return False
    coach_id = await resolve_support_sender_id()
    if not coach_id:
        logger.warning("wellness: no coach/support user id")
        return False
    if int(coach_id) == int(uid):
        return False
    interval = _normalize_interval(row.get("wellness_journal_interval_days"))
    nxt = row.get("wellness_next_prompt_at")
    now = datetime.utcnow()
    if nxt and nxt > now:
        return False

    idx = await _count_ai_prompts(uid)
    body = _build_prompt_text(include_weekly_nudge=(idx % 4 == 0), prompt_index=idx)
    notify_uid = _notify_uid(dict(row))

    try:
        dm_row = await database.fetch_one_write(
            direct_messages.insert()
            .values(
                sender_id=int(coach_id),
                recipient_id=notify_uid,
                text=body,
                is_read=False,
                is_system=False,
            )
            .returning(direct_messages.c.id)
        )
        mid = int(dm_row["id"]) if dm_row and dm_row.get("id") is not None else None
        if mid:
            await sync_direct_messages_pair(int(coach_id), int(notify_uid), broadcast_legacy_dm_id=mid)
        await database.execute(
            wellness_journal_entries.insert().values(
                user_id=notify_uid,
                role="ai_prompt",
                raw_text=body,
                extracted_json=None,
                direct_message_id=mid,
            )
        )
        await database.execute(
            users.update()
            .where(users.c.id == uid)
            .values(
                wellness_last_prompt_at=now,
                wellness_next_prompt_at=_next_prompt_after(interval),
            )
        )
        return True
    except Exception:
        logger.exception("wellness: send prompt failed uid=%s", uid)
        return False


async def run_wellness_prompts_due_job() -> None:
    if not await wellness_journal_globally_enabled():
        return
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT id, primary_user_id, wellness_journal_interval_days, wellness_next_prompt_at,
                   wellness_journal_opt_out, wellness_journal_admin_paused
            FROM users
            WHERE wellness_journal_opt_out = false
              AND wellness_journal_admin_paused = false
              AND (wellness_next_prompt_at IS NULL OR wellness_next_prompt_at <= NOW())
            ORDER BY id ASC
            LIMIT 300
            """
        )
    )
    for row in rows:
        uid = int(row["primary_user_id"] or row["id"])
        plan = await check_subscription(uid)
        if plan == "free":
            continue
        if row["wellness_next_prompt_at"] is None:
            await database.execute(
                users.update()
                .where(users.c.id == uid)
                .values(wellness_next_prompt_at=datetime.utcnow())
            )
        await send_wellness_prompt_for_user(uid)


async def _maybe_send_weekly_digest(user_id: int) -> None:
    uid = int(user_id)
    plan = await check_subscription(uid)
    if plan == "free" or not await wellness_journal_globally_enabled():
        return
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row or row.get("wellness_journal_opt_out") or row.get("wellness_journal_admin_paused"):
        return
    last = row.get("wellness_weekly_digest_last_at")
    now = datetime.utcnow()
    if last and (now - last).total_seconds() < 6.5 * 24 * 3600:
        return
    prompts_ever = await database.fetch_val(
        sa.select(sa.func.count())
        .select_from(wellness_journal_entries)
        .where(wellness_journal_entries.c.user_id == uid)
        .where(wellness_journal_entries.c.role == "ai_prompt"),
    ) or 0
    if int(prompts_ever) < 1:
        await database.execute(
            users.update().where(users.c.id == uid).values(wellness_weekly_digest_last_at=now)
        )
        return

    replies = await database.fetch_val(
        sa.select(sa.func.count())
        .select_from(wellness_journal_entries)
        .where(wellness_journal_entries.c.user_id == uid)
        .where(wellness_journal_entries.c.role == "user_reply")
        .where(wellness_journal_entries.c.created_at >= now - timedelta(days=7)),
    ) or 0
    if int(replies) < 1:
        await database.execute(
            users.update().where(users.c.id == uid).values(wellness_weekly_digest_last_at=now)
        )
        return
    coach_id = await resolve_support_sender_id()
    if not coach_id:
        return
    site = (settings.SITE_URL or "").rstrip("/") or "https://mushroomsai.ru"
    results_url = f"{site}/account/wellness-results"
    body = (
        MSG_PREFIX
        + f"Недельная отбивка: за последние 7 дней у вас {int(replies)} ответ(ов) в дневнике.\n"
        + f"Откройте сводку и графики: {results_url}\n\n"
        + "Если хотите изменить частоту опросов — напишите «каждый день», «раз в 3 дня», «раз в 5 дней» или «раз в неделю»."
    )
    notify_uid = _notify_uid(dict(row))
    try:
        dm_row = await database.fetch_one_write(
            direct_messages.insert()
            .values(
                sender_id=int(coach_id),
                recipient_id=notify_uid,
                text=body,
                is_read=False,
                is_system=False,
            )
            .returning(direct_messages.c.id)
        )
        mid = int(dm_row["id"]) if dm_row and dm_row.get("id") else None
        if mid:
            await sync_direct_messages_pair(int(coach_id), int(notify_uid), broadcast_legacy_dm_id=mid)
        await database.execute(
            wellness_journal_entries.insert().values(
                user_id=notify_uid,
                role="weekly_digest",
                raw_text=body,
                extracted_json=None,
                direct_message_id=mid,
            )
        )
        await database.execute(
            users.update().where(users.c.id == uid).values(wellness_weekly_digest_last_at=now)
        )
    except Exception:
        logger.exception("wellness: weekly digest uid=%s", uid)


async def run_wellness_weekly_digests_job() -> None:
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT id, primary_user_id FROM users
            WHERE wellness_journal_opt_out = false AND wellness_journal_admin_paused = false
            ORDER BY id ASC LIMIT 400
            """
        )
    )
    for row in rows:
        uid = int(row["primary_user_id"] or row["id"])
        await _maybe_send_weekly_digest(uid)


async def record_user_reply(
    user_id: int,
    text: str,
    *,
    direct_message_id: Optional[int] = None,
) -> Optional[int]:
    uid = int(user_id)
    t = (text or "").strip()
    if not t:
        return None
    ins = (
        wellness_journal_entries.insert()
        .values(
            user_id=uid,
            role="user_reply",
            raw_text=t,
            extracted_json=None,
            direct_message_id=direct_message_id,
        )
        .returning(wellness_journal_entries.c.id)
    )
    row = await database.fetch_one_write(ins)
    eid = int(row["id"]) if row and row.get("id") is not None else None
    await parse_frequency_and_opt_out_from_text(uid, t)
    return eid


async def on_user_message_to_coach(
    sender_uid: int,
    recipient_id: int,
    text: str,
    *,
    direct_message_id: Optional[int] = None,
) -> Optional[int]:
    coach = await resolve_support_sender_id()
    if not coach or int(recipient_id) != int(coach):
        return None
    plan = await check_subscription(int(sender_uid))
    if plan == "free":
        return None
    return await record_user_reply(sender_uid, text, direct_message_id=direct_message_id)


_EXTRACTION_SYSTEM = """Ты помощник для структурирования дневника фунготерапии. По сообщению пользователя верни ТОЛЬКО JSON без markdown со полями:
{
  "mood_0_10": number|null,
  "energy_0_10": number|null,
  "sleep_note": string|null,
  "mushrooms": string[],
  "dose_notes": string|null,
  "timing": string|null,
  "physical_symptoms": string[],
  "mental_symptoms": string[],
  "substances_other": string[],
  "motivation_why_mushrooms": string|null,
  "free_summary": string|null
}
Используй пустые массивы если нет данных. Числа — целые 0–10 или null. Язык значений — русский."""


async def extract_wellness_json_async(entry_id: int, raw_text: str) -> None:
    if not getattr(settings, "OPENAI_API_KEY", None):
        return
    try:
        from openai import AsyncOpenAI

        cli = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        resp = await cli.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _EXTRACTION_SYSTEM},
                {"role": "user", "content": raw_text[:8000]},
            ],
            temperature=0.2,
            max_tokens=1200,
            response_format={"type": "json_object"},
        )
        content = (resp.choices[0].message.content or "").strip()
        json.loads(content)  # validate
        await database.execute(
            wellness_journal_entries.update()
            .where(wellness_journal_entries.c.id == int(entry_id))
            .values(extracted_json=content)
        )
    except Exception:
        logger.exception("wellness: extraction failed entry_id=%s", entry_id)


def aggregate_entries_for_display(rows: list[dict]) -> dict[str, Any]:
    """Агрегаты для страницы «Мои результаты»."""
    by_mushroom: dict[str, int] = {}
    moods: list[int] = []
    energies: list[int] = []
    timeline: list[dict] = []
    for r in rows:
        if r.get("role") != "user_reply":
            continue
        ej = r.get("extracted_json")
        parsed = None
        if ej:
            try:
                parsed = json.loads(ej)
            except json.JSONDecodeError:
                parsed = None
        item = {
            "at": r.get("created_at"),
            "raw": (r.get("raw_text") or "")[:500],
            "parsed": parsed,
        }
        timeline.append(item)
        if parsed:
            m = parsed.get("mood_0_10")
            if isinstance(m, (int, float)) and 0 <= float(m) <= 10:
                moods.append(int(round(float(m))))
            e = parsed.get("energy_0_10")
            if isinstance(e, (int, float)) and 0 <= float(e) <= 10:
                energies.append(int(round(float(e))))
            for sh in parsed.get("mushrooms") or []:
                if isinstance(sh, str) and sh.strip():
                    k = sh.strip().lower()[:80]
                    by_mushroom[k] = by_mushroom.get(k, 0) + 1
    return {
        "timeline": timeline[:120],
        "mushroom_counts": sorted(by_mushroom.items(), key=lambda x: -x[1])[:24],
        "mood_avg": round(sum(moods) / len(moods), 2) if moods else None,
        "energy_avg": round(sum(energies) / len(energies), 2) if energies else None,
        "reply_count": len([x for x in rows if x.get("role") == "user_reply"]),
        "prompt_count": len([x for x in rows if x.get("role") == "ai_prompt"]),
    }


async def top_wellness_responders(limit: int = 10, days: int = 30) -> list[dict]:
    since = datetime.utcnow() - timedelta(days=days)
    q = sa.text(
        """
        SELECT u.id, u.name, u.email, COUNT(w.id) AS cnt
        FROM wellness_journal_entries w
        JOIN users u ON u.id = w.user_id
        WHERE w.role = 'user_reply' AND w.created_at >= :since
        GROUP BY u.id, u.name, u.email
        ORDER BY cnt DESC
        LIMIT :lim
        """
    )
    rows = await database.fetch_all(q, {"since": since, "lim": limit})
    return [dict(r) for r in rows]
