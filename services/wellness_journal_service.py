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
from services.system_support_delivery import all_legacy_neurofungi_ai_peer_ids, resolve_support_sender_id
from services.legacy_dm_chat_sync import sync_direct_messages_pair

logger = logging.getLogger(__name__)

PLATFORM_KEY = "wellness_journal_globally_enabled"
ALLOWED_INTERVALS = (1, 3, 5, 7)
MSG_PREFIX = "🍄 NeuroFungi AI · дневник\n\n"


async def _fetch_dm_thread_snippets(peer_ids: set[int], notify_uid: int, limit: int = 32) -> list[str]:
    ids = sorted({int(x) for x in peer_ids if int(x) > 0})
    if not ids:
        return []
    id_sql = ",".join(str(i) for i in ids)
    rows = await database.fetch_all(
        sa.text(
            f"""
            SELECT sender_id, text FROM direct_messages
            WHERE (
              (sender_id IN ({id_sql}) AND recipient_id = :uid) OR
              (sender_id = :uid AND recipient_id IN ({id_sql}) AND is_system = false)
            )
            ORDER BY id DESC
            LIMIT :lim
            """
        ),
        {"uid": int(notify_uid), "lim": int(limit)},
    )
    idset = set(ids)
    out: list[str] = []
    for r in rows:
        sid = int(r["sender_id"] or 0)
        who = "NeuroFungi AI" if sid in idset else "Пользователь"
        out.append(f"{who}: {(r.get('text') or '')[:720]}")
    return list(reversed(out))


async def _telegram_ping_wellness(notify_uid: int, coach_id: int) -> None:
    try:
        from services.notify_user_stub import notify_wellness_coach_telegram

        row = await database.fetch_one(users.select().where(users.c.id == int(notify_uid)))
        tg = None
        if row:
            tg = row.get("tg_id") or row.get("linked_tg_id")
        if not tg:
            fam = await database.fetch_one(
                sa.text(
                    """
                    SELECT tg_id, linked_tg_id FROM users
                    WHERE primary_user_id = :uid
                      AND (tg_id IS NOT NULL OR linked_tg_id IS NOT NULL)
                    ORDER BY id ASC LIMIT 1
                    """
                ),
                {"uid": int(notify_uid)},
            )
            if fam:
                tg = fam.get("tg_id") or fam.get("linked_tg_id")
        if tg:
            await notify_wellness_coach_telegram(
                int(tg),
                open_path=f"/chats?open_user={int(coach_id)}",
                coach_user_id=int(coach_id),
            )
    except Exception:
        logger.debug("wellness: telegram ping skipped", exc_info=True)


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
        "Привет. Это NeuroFungi AI — продолжаем дневник: и фунготерапия как самонаблюдение, и опора на КПТ и разговор в провокативном, но уважительном ключе "
        "(ответственность за свои реакции и шаги, а не «виноваты другие»).\n\n"
        "Коротко ответьте (можно тезисно), а если хотите — развёрнуто:\n"
        "• Что сегодня с телом и настроением (шкала 1–10 ок)?\n"
        "• Какие грибы / связки, доза, время суток?\n"
        "• Какая мысль или триггер сегодня зацепила сильнее всего? Что вы с этим делаете?\n"
        "• Насколько ваши действия сегодня ведут к той жизни, которую вы выбираете — а не к автопилоту старых программ?\n\n"
        f"📊 Сводка и графики: {results_url}\n"
        "Частота: «каждый день», «раз в 3 дня», «раз в 5 дней», «раз в неделю» или «отключить дневник».\n"
        "Если на сегодня достаточно диалога — напишите «хватит на сегодня».\n"
        "\n💰 Реферальная программа: приглашайте пользователей и смотрите сотрудничество с магазином — всё в меню-бургере → «Реферальная программа».\n"
        "Если что-то непонятно в интерфейсе — напишите, подскажу в рамках того, что доступно вам в приложении.\n"
    )
    if include_weekly_nudge:
        base += "\n📅 Раз в неделю пришлю короткую сводку здесь, в чате.\n"
    if prompt_index > 0 and prompt_index % 4 == 0:
        base += "\n💬 Удобна ли вам такая периодичность сообщений? Напишите одним предложением.\n"
    if prompt_index > 0 and prompt_index % 3 == 0:
        base += (
            "\n✨ Что бы вы добавили или улучшили на платформе? Одним сообщением — идеи передаются команде.\n"
        )
    return MSG_PREFIX + base


async def _compose_coach_message_body(
    uid: int,
    notify_uid: int,
    coach_id: int,
    *,
    prompt_index: int,
    include_weekly_nudge: bool,
) -> str:
    from ai.openai_client import _fetch_relevant_training_posts
    from services.wellness_coach_ai import generate_wellness_coach_message

    peers = await all_legacy_neurofungi_ai_peer_ids()
    thread = await _fetch_dm_thread_snippets(peers, notify_uid)
    entries_raw = await database.fetch_all(
        wellness_journal_entries.select()
        .where(wellness_journal_entries.c.user_id == notify_uid)
        .order_by(wellness_journal_entries.c.created_at.desc())
        .limit(90)
    )
    stats = aggregate_entries_for_display([dict(e) for e in entries_raw])
    stats["include_weekly_nudge"] = include_weekly_nudge
    stats["prompt_index"] = prompt_index

    last_u = await database.fetch_one(
        wellness_journal_entries.select()
        .where(wellness_journal_entries.c.user_id == notify_uid)
        .where(wellness_journal_entries.c.role == "user_reply")
        .order_by(wellness_journal_entries.c.id.desc())
        .limit(1)
    )
    qtxt = ((last_u.get("raw_text") if last_u else None) or "").strip()
    if len(qtxt) < 8:
        qtxt = "фунготерапия грибы КПТ душевное здоровье тревога сон дозировка самочувствие"
    try:
        posts = await _fetch_relevant_training_posts(qtxt[:500], top_k=12)
    except Exception:
        posts = []
    urow = await database.fetch_one(users.select().where(users.c.id == uid))
    uname = (urow.get("name") if urow else None) or "Участник"

    ai_body = await generate_wellness_coach_message(
        user_name=str(uname),
        thread_snippets=thread,
        knowledge_excerpts=posts,
        stats_summary=stats,
        prompt_index=prompt_index,
    )
    if ai_body:
        if not ai_body.lstrip().startswith("🍄"):
            return MSG_PREFIX + ai_body
        return ai_body
    return _build_prompt_text(include_weekly_nudge=include_weekly_nudge, prompt_index=prompt_index)


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

    if any(
        x in t
        for x in (
            "хватит на сегодня",
            "на сегодня хватит",
            "достаточно на сегодня",
            "на сегодня достаточно",
        )
    ):
        tomorrow = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        await database.execute(
            users.update().where(users.c.id == uid).values(wellness_coach_pause_until=tomorrow)
        )
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

    pause_until = row.get("wellness_coach_pause_until")
    if pause_until and now < pause_until:
        await database.execute(
            users.update()
            .where(users.c.id == uid)
            .values(wellness_next_prompt_at=pause_until)
        )
        return False

    idx = await _count_ai_prompts(uid)
    notify_uid = _notify_uid(dict(row))
    body = await _compose_coach_message_body(
        uid,
        notify_uid,
        int(coach_id),
        prompt_index=idx,
        include_weekly_nudge=(idx % 4 == 0),
    )

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
                wellness_coach_pause_until=None,
            )
        )
        await _telegram_ping_wellness(notify_uid, int(coach_id))
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
        await _telegram_ping_wellness(notify_uid, int(coach_id))
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
    await _maybe_record_platform_feedback_wish(uid, t)
    return eid


async def on_user_message_to_coach(
    sender_uid: int,
    recipient_id: int,
    text: str,
    *,
    direct_message_id: Optional[int] = None,
) -> Optional[int]:
    peers = await all_legacy_neurofungi_ai_peer_ids()
    if not peers or int(recipient_id) not in peers:
        return None
    plan = await check_subscription(int(sender_uid))
    if plan == "free":
        return None
    return await record_user_reply(sender_uid, text, direct_message_id=direct_message_id)


async def _maybe_record_platform_feedback_wish(user_id: int, text: str) -> None:
    t = (text or "").strip()
    if len(t) < 10:
        return
    low = t.lower()
    keys = (
        "пожелан",
        "добавьте",
        "добавить ",
        "предлож",
        "не хватает",
        "улучшить",
        "нужно в прилож",
        "хотел бы",
        "хотела бы",
        "функци",
        "раздел",
        "меню",
    )
    if not any(k in low for k in keys):
        return
    try:
        from services.platform_ai_feedback import record_platform_ai_feedback

        row = await database.fetch_one(users.select().where(users.c.id == int(user_id)))
        ur = "admin" if row and (row.get("role") or "") == "admin" else "user"
        await record_platform_ai_feedback(int(user_id), ur, t[:8000], source="user_reply_keywords")
    except Exception:
        logger.debug("platform feedback record skipped", exc_info=True)


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
  "life_goal_short": string|null,
  "trigger_or_distortion": string|null,
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
        SELECT u.id, u.name, u.email,
               COALESCE(u.wellness_journal_pdf_allowed, true) AS wellness_journal_pdf_allowed,
               COUNT(w.id) AS cnt
        FROM wellness_journal_entries w
        JOIN users u ON u.id = w.user_id
        WHERE w.role = 'user_reply' AND w.created_at >= :since
        GROUP BY u.id
        ORDER BY cnt DESC
        LIMIT :lim
        """
    )
    rows = await database.fetch_all(q, {"since": since, "lim": limit})
    return [dict(r) for r in rows]


RENEWAL_NUDGE_BODY = (
    MSG_PREFIX
    + "Через пару дней заканчивается срок вашего доступа (подписка или пробный «Старт»).\n\n"
    + "Если наш диалог реально помогает вам не сдаваться старым деструктивным убеждениям и держать фокус на целях — "
    + "имеет смысл продлить подписку: сохранятся чаты, дневник NeuroFungi AI и привычка разбирать триггеры в формате КПТ.\n\n"
    + "Оформить продление можно в разделе «Подписка» в приложении. Мы уже многое прояснили — логично продолжить путь."
)


async def _deliver_renewal_nudge(uid: int, coach_id: int, target_end: datetime) -> None:
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row or row.get("wellness_journal_opt_out"):
        return
    notify_uid = _notify_uid(dict(row))
    body = RENEWAL_NUDGE_BODY
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
                role="renewal_nudge",
                raw_text=body,
                extracted_json=None,
                direct_message_id=mid,
            )
        )
        await database.execute(
            users.update()
            .where(users.c.id == uid)
            .values(wellness_renewal_nudge_for_end=target_end)
        )
        await _telegram_ping_wellness(notify_uid, int(coach_id))
    except Exception:
        logger.exception("wellness: renewal nudge uid=%s", uid)


async def run_wellness_subscription_renewal_nudges_job() -> None:
    if not await wellness_journal_globally_enabled():
        return
    now = datetime.utcnow()
    lo = now + timedelta(days=2)
    hi = now + timedelta(days=4)
    coach_id = await resolve_support_sender_id()
    if not coach_id:
        return
    rows = await database.fetch_all(
        sa.text(
            """
            SELECT id, primary_user_id, name, subscription_end, start_trial_until,
                   wellness_renewal_nudge_for_end, subscription_plan, subscription_admin_granted,
                   wellness_journal_opt_out
            FROM users
            WHERE primary_user_id IS NULL
              AND wellness_journal_opt_out = false
              AND (
                (subscription_end IS NOT NULL AND subscription_end > NOW()
                 AND subscription_end >= :lo AND subscription_end <= :hi)
                OR
                (start_trial_until IS NOT NULL AND start_trial_until > NOW()
                 AND start_trial_until >= :lo AND start_trial_until <= :hi)
              )
            LIMIT 500
            """
        ),
        {"lo": lo, "hi": hi},
    )
    for row in rows:
        uid = int(row["primary_user_id"] or row["id"])
        plan = await check_subscription(uid)
        if plan == "free":
            continue
        sub_end = row.get("subscription_end")
        trial_end = row.get("start_trial_until")
        admin_gr = bool(row.get("subscription_admin_granted"))
        sp = (row.get("subscription_plan") or "free").lower()
        target: Optional[datetime] = None
        if sp in ("start", "pro", "maxi") and sub_end and not admin_gr:
            if lo <= sub_end <= hi:
                target = sub_end
        if target is None and trial_end and plan == "start":
            if lo <= trial_end <= hi:
                target = trial_end
        if target is None:
            continue
        prev = row.get("wellness_renewal_nudge_for_end")
        if prev is not None and target is not None:
            try:
                if abs((prev - target).total_seconds()) < 60:
                    continue
            except Exception:
                pass
        await _deliver_renewal_nudge(uid, int(coach_id), target)


async def admin_global_wellness_summary() -> dict[str, Any]:
    """Агрегаты по всем пользователям для админской сводки."""
    since30 = datetime.utcnow() - timedelta(days=30)
    nu = await database.fetch_val(
        sa.select(sa.func.count(sa.distinct(wellness_journal_entries.c.user_id)))
        .select_from(wellness_journal_entries)
        .where(wellness_journal_entries.c.role == "user_reply")
    )
    nu30 = await database.fetch_val(
        sa.select(sa.func.count(sa.distinct(wellness_journal_entries.c.user_id)))
        .select_from(wellness_journal_entries)
        .where(wellness_journal_entries.c.role == "user_reply")
        .where(wellness_journal_entries.c.created_at >= since30)
    )
    rep_all = await database.fetch_val(
        sa.select(sa.func.count())
        .select_from(wellness_journal_entries)
        .where(wellness_journal_entries.c.role == "user_reply")
    )
    rep30 = await database.fetch_val(
        sa.select(sa.func.count())
        .select_from(wellness_journal_entries)
        .where(wellness_journal_entries.c.role == "user_reply")
        .where(wellness_journal_entries.c.created_at >= since30)
    )
    pr30 = await database.fetch_val(
        sa.select(sa.func.count())
        .select_from(wellness_journal_entries)
        .where(wellness_journal_entries.c.role == "ai_prompt")
        .where(wellness_journal_entries.c.created_at >= since30)
    )
    rows = await database.fetch_all(
        wellness_journal_entries.select()
        .where(wellness_journal_entries.c.role == "user_reply")
        .where(wellness_journal_entries.c.extracted_json.isnot(None))
        .order_by(wellness_journal_entries.c.created_at.desc())
        .limit(900)
    )
    by_mushroom: dict[str, int] = {}
    moods: list[int] = []
    energies: list[int] = []
    for r in rows:
        ej = r.get("extracted_json")
        if not ej:
            continue
        try:
            parsed = json.loads(ej)
        except json.JSONDecodeError:
            continue
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
        "users_with_replies_ever": int(nu or 0),
        "users_with_replies_30d": int(nu30 or 0),
        "replies_ever": int(rep_all or 0),
        "replies_30d": int(rep30 or 0),
        "prompts_30d": int(pr30 or 0),
        "mood_avg_sample": round(sum(moods) / len(moods), 2) if moods else None,
        "energy_avg_sample": round(sum(energies) / len(energies), 2) if energies else None,
        "mushroom_top": sorted(by_mushroom.items(), key=lambda x: -x[1])[:20],
        "sample_moods_n": len(moods),
    }


async def set_user_wellness_pdf_allowed(user_id: int, allowed: bool) -> None:
    await database.execute(
        users.update()
        .where(users.c.id == int(user_id))
        .values(wellness_journal_pdf_allowed=bool(allowed))
    )
