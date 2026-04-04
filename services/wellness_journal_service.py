"""
Дневник фунготерапии: напоминания AI в ЛС (аккаунт техподдержки), сбор ответов, статистика.
Доступно при тарифе Старт / Про / Макси (включая пробный Старт) и у пользователей с role=admin.
Обычные пользователи: ответ в статистику после «да» в чате.
Админ: авто-включение в статистику + цепочка уточняющих вопросов (тест); опция «тихий режим» — только разбор JSON, без сообщений бота.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

import sqlalchemy as sa

from config import settings
from db.database import database
from db.models import users, wellness_journal_entries, platform_settings, direct_messages, wellness_scheme_effect_stats
from services.ai_multichannel_settings import get_ai_multichannel_settings
from services.subscription_service import check_subscription
from services.system_support_delivery import all_legacy_neurofungi_ai_peer_ids, resolve_wellness_dm_sender_id
from services.legacy_dm_chat_sync import sync_direct_messages_pair

logger = logging.getLogger(__name__)

PLATFORM_KEY = "wellness_journal_globally_enabled"
ALLOWED_INTERVALS = (1, 3, 5, 7)
ALLOWED_PROMPTS_PER_DAY = (1, 2, 3)
MSG_PREFIX = "🍄 NeuroFungi AI · дневник\n\n"

# Цепочка вопросов для админа после каждого ответа (наполнение дневника для теста системы).
WELLNESS_ADMIN_CHAIN_QUESTIONS: tuple[str, ...] = (
    "Какой гриб или связку использовали в последний раз (название и форма: порошок, настойка, капсулы)?",
    "Разовая дозировка и как её измеряли (капли, мг, «на глаз» — опишите кратко)?",
    "Во сколько по вашему времени был последний приём?",
    "Были ли ещё приёмы за последние сутки — что именно и когда?",
    "Что чувствовали в теле в первые 1–2 часа после приёма?",
    "Как спали после приёма? Оцените сон одним числом 0–10.",
    "Тревога в тот день / сейчас — число 0–10?",
    "Настроение и энергия — два числа через запятую (0–10, 0–10).",
    "Была ли паника или сильный приступ тревоги (да/нет; если да — когда примерно)?",
    "Концентрация / «туман в голове» — 0–10?",
    "Что ели за 3 часа до и после приёма (кратко)?",
    "Кофеин, алкоголь, лекарства в тот день — перечислите или «нет».",
    "Какая мысль или триггер зацепил сильнее всего сегодня?",
    "Физические симптомы одной строкой (или «нет особых»).",
    "Зачем вам сейчас грибы — одна короткая фраза (цель)?",
    "«Иммунитет по ощущениям» — 0–10?",
    "Метаболика / вес в фокусе сегодня (да/нет + комментарий по желанию)?",
    "Свободная строка: что ещё важно зафиксировать в дневнике?",
)


def _user_row_is_admin(row: Any) -> bool:
    return str((row.get("role") if row else None) or "").strip().lower() == "admin"


async def set_wellness_admin_ai_silent(user_id: int, silent: bool) -> None:
    await database.execute(
        users.update()
        .where(users.c.id == int(user_id))
        .values(wellness_admin_ai_silent=bool(silent))
    )


async def reset_wellness_admin_chain_index(user_id: int) -> None:
    await database.execute(
        users.update().where(users.c.id == int(user_id)).values(wellness_admin_q_index=0)
    )


async def kickoff_admin_wellness_chain_after_enable(user_id: int) -> None:
    """Первый вопрос цепочки после включения режима с вопросами (не тихий)."""
    await _send_admin_chain_dm_for_user(int(user_id), kickoff=True)


async def _send_admin_chain_dm_for_user(
    user_id: int,
    *,
    after_extraction_failed: bool = False,
    kickoff: bool = False,
) -> None:
    """Следующий шаблонный вопрос админу; сдвигает wellness_admin_q_index."""
    uid = int(user_id)
    urow = await database.fetch_one(users.select().where(users.c.id == uid))
    if not urow or not _user_row_is_admin(urow) or urow.get("wellness_admin_ai_silent"):
        return
    notify_uid = _notify_uid(dict(urow))
    coach_id = await resolve_wellness_dm_sender_id(notify_uid)
    if not coach_id:
        return
    n = len(WELLNESS_ADMIN_CHAIN_QUESTIONS)
    if n == 0:
        return
    q_idx = int(urow.get("wellness_admin_q_index") or 0) % n
    q_text = WELLNESS_ADMIN_CHAIN_QUESTIONS[q_idx]
    nxt = (q_idx + 1) % n
    await database.execute(
        users.update().where(users.c.id == uid).values(wellness_admin_q_index=int(nxt))
    )
    if kickoff:
        mid = "Режим вопросов включён. Ответьте одним сообщением:\n\n"
    elif after_extraction_failed:
        mid = (
            "Не удалось сохранить разбор в JSON (нет OPENAI_API_KEY или ошибка модели). "
            "Текст записи в дневнике уже учитывается в статистике.\n\n"
            "Следующий вопрос:\n\n"
        )
    else:
        mid = "Следующий пункт дневника (для теста статистики — ответьте одним сообщением):\n\n"
    await _insert_coach_dm(int(coach_id), notify_uid, MSG_PREFIX + mid + q_text)
    await _telegram_ping_wellness(notify_uid, int(coach_id))


async def _send_admin_chain_followup(entry_id: int, *, extraction_ok: bool) -> None:
    """После попытки разбора ответа админа — следующий вопрос (и при сбое JSON)."""
    ent = await database.fetch_one(
        wellness_journal_entries.select().where(wellness_journal_entries.c.id == int(entry_id))
    )
    if not ent or (ent.get("role") or "") != "user_reply":
        return
    uid = int(ent["user_id"])
    await _send_admin_chain_dm_for_user(
        uid,
        after_extraction_failed=not extraction_ok,
        kickoff=False,
    )


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


async def user_has_wellness_journal_access(uid: int) -> bool:
    """Платный тариф или роль admin — дневник и ЛС с NeuroFungi AI."""
    plan = await check_subscription(int(uid))
    if plan != "free":
        return True
    row = await database.fetch_one(users.select().where(users.c.id == int(uid)))
    return bool(row and (row.get("role") or "").strip().lower() == "admin")


def _parse_stats_confirmation_reply(text: str) -> Optional[str]:
    """Ответ на «включить в статистику?»: 'yes', 'no' или None (не распознано)."""
    t = (text or "").strip().lower()
    if not t or len(t) > 240:
        return None
    for phrase in ("не надо", "не включай", "не нужно", "не сохраняй", "не записывай"):
        if phrase in t:
            return "no"
    toks = t.split()
    first = (toks[0] if toks else "").strip(".,!?")
    if first in ("нет", "no", "n") or t in ("нет", "нет.", "no", "n"):
        return "no"
    yes_ok = frozenset(
        {
            "да",
            "+",
            "yes",
            "y",
            "ага",
            "угу",
            "ок",
            "lf",
            "конечно",
            "подтверждаю",
            "включай",
            "давай",
            "сохрани",
            "запиши",
        }
    )
    if first in yes_ok or t.strip(".,!?") in yes_ok:
        return "yes"
    return None


def _parse_which_stats_none_reply(text: str) -> bool:
    """После отказа: пользователь подтвердил, что в статистику ничего не включать."""
    t = (text or "").strip().lower()
    if not t or len(t) > 96:
        return False
    if t in frozenset(
        {
            "никакое",
            "никаких",
            "ни одно",
            "ни одного",
            "ничего",
            "нет",
            "неа",
            "не надо",
            "не нужно",
            "пусто",
            "ни одного сообщения",
            "никаких сообщений",
            "ничего не надо",
            "ничего не нужно",
            "остановись",
            "хватит",
        }
    ):
        return True
    if len(t) < 48 and (t.startswith("никакое") or t.startswith("ни одно")):
        return True
    if len(t) < 44 and "никакое" in t:
        return True
    return False


def _is_vague_other_only_reply(text: str) -> bool:
    """Только «другое» без текста — просим прислать формулировку или «никакое»."""
    t = (text or "").strip().lower()
    if len(t) > 40:
        return False
    return t in (
        "другое",
        "другой",
        "другая",
        "другое сообщение",
        "вот другое",
        "есть другое",
        "другую",
        "другие",
        "не то",
        "не это",
    )


async def _insert_coach_dm(coach_id: int, recipient_user_id: int, body: str) -> None:
    dm_row = await database.fetch_one_write(
        direct_messages.insert()
        .values(
            sender_id=int(coach_id),
            recipient_id=int(recipient_user_id),
            text=body,
            is_read=False,
            is_system=False,
        )
        .returning(direct_messages.c.id)
    )
    mid = int(dm_row["id"]) if dm_row and dm_row.get("id") else None
    if mid:
        await sync_direct_messages_pair(int(coach_id), int(recipient_user_id), broadcast_legacy_dm_id=mid)


def _build_prompt_text(*, include_weekly_nudge: bool, prompt_index: int) -> str:
    site = (settings.SITE_URL or "").rstrip("/") or "https://mushroomsai.ru"
    results_url = f"{site}/account/wellness-results"
    base = (
        "Привет, это я — NeuroFungi AI, пишу как подруга в переписке 🙂 Продолжаем дневник: фунготерапия как самонаблюдение + мягкая КПТ и разговор «на равных» "
        "(ты выбираешь реакции и шаги, без обвинений себя и других).\n\n"
        "Сегодня хочу спросить по-новому (ответь как удобно — коротко или развёрнуто):\n"
        "• Что сегодня с телом и настроением (если хочешь — шкала 1–10)?\n"
        "• Грибы / связки, доза, время — если было что отметить.\n"
        "• Какая мысль или триггер сегодня зацепил сильнее всего? Что ты с этим делаешь?\n"
        "• Насколько твои действия сегодня ведут к той жизни, которую ты выбираешь, а не к автопилоту старых программ?\n\n"
        "📋 Чтобы графики в «Мои результаты» были полнее, по возможности в том же ответе отметь (шкалы 0–10, где не знаешь — прочерк):\n"
        "Психика: тревога, настроение, энергия, концентрация, качество сна.\n"
        "Физиология: усталость, напряжение в теле, либидо, аппетит.\n"
        "Симптомы: паника сегодня (да/нет); апатия; раздражительность; стресс 0–10; «иммунитет по ощущениям» 0–10.\n"
        "Метаболика: если актуально — акцент на весе/инсулинорезистентности (да/нет).\n"
        "Приём сегодня: да/нет; если да — тип гриба, дозировка, время.\n\n"
        "Важно: я твой ответ записываю в базу дневника. Подтверждение включения в статистику — отдельно «да/нет», чтобы ничего не перепутать.\n\n"
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

    therapy_context = ""
    try:
        from services.wellness_ai_profile_service import load_wellness_ai_profile_dict
        from services.mushroom_therapy_kb import format_therapy_context_for_coach

        prof = await load_wellness_ai_profile_dict(int(notify_uid))
        therapy_context = format_therapy_context_for_coach(prof)
    except Exception:
        logger.debug("wellness: therapy context for coach skipped", exc_info=True)

    recent_ai_prompt_excerpts: list[str] = []
    try:
        prev_rows = await database.fetch_all(
            wellness_journal_entries.select()
            .where(wellness_journal_entries.c.user_id == int(notify_uid))
            .where(wellness_journal_entries.c.role == "ai_prompt")
            .order_by(wellness_journal_entries.c.created_at.desc())
            .limit(10)
        )
        for pr in prev_rows:
            raw_p = (pr.get("raw_text") or "").strip()
            if raw_p:
                recent_ai_prompt_excerpts.append(raw_p[:650])
    except Exception:
        logger.debug("wellness: recent ai_prompt fetch skipped", exc_info=True)

    ai_body = await generate_wellness_coach_message(
        user_name=str(uname),
        thread_snippets=thread,
        knowledge_excerpts=posts,
        stats_summary=stats,
        prompt_index=prompt_index,
        therapy_context=therapy_context,
        recent_ai_prompt_excerpts=recent_ai_prompt_excerpts,
    )
    if ai_body:
        if not ai_body.lstrip().startswith("🍄"):
            return MSG_PREFIX + ai_body
        return ai_body
    return _build_prompt_text(include_weekly_nudge=include_weekly_nudge, prompt_index=prompt_index)


async def _coach_confirm_phrase() -> str:
    """Единая фраза-подтверждение для да/нет, как просил админ."""
    try:
        cfg = await get_ai_multichannel_settings()
    except Exception:
        cfg = {}
    algo = (cfg.get("dm_algorithm_prompt") or "").lower()
    if "да/нет" in algo:
        return "Я твой ответ записываю в базу. Подтверди «да» или «нет», чтобы не перепутать вопрос."
    return "Подтверди «да» или «нет», чтобы я корректно записал ответ в статистику."


async def schedule_wellness_journal_if_paid(user_id: int) -> None:
    """Вызывать при активации платного тарифа или пробного Старт; для admin — тоже."""
    uid = int(user_id)
    plan = await check_subscription(uid)
    if plan == "free":
        row0 = await database.fetch_one(users.select().where(users.c.id == uid))
        if not row0 or (row0.get("role") or "").strip().lower() != "admin":
            return
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row or row.get("wellness_journal_opt_out"):
        return
    if row.get("wellness_next_prompt_at") is not None:
        return
    ppp = _normalize_prompts_per_day(row.get("wellness_journal_prompts_per_day"))
    nxt = await _compute_bootstrap_next_prompt(datetime.utcnow(), prompts_per_day=ppp)
    await database.execute(
        users.update()
        .where(users.c.id == uid)
        .values(
            wellness_next_prompt_at=nxt,
            wellness_journal_interval_days=row.get("wellness_journal_interval_days") or 1,
            wellness_journal_prompts_per_day=ppp,
        )
    )


def _normalize_interval(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 1
    return n if n in ALLOWED_INTERVALS else 1


def _normalize_prompts_per_day(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 1
    return n if n in ALLOWED_PROMPTS_PER_DAY else 1


async def _dm_interval_config() -> tuple[bool, int]:
    """Глобальная периодичность ЛС из админки AI: по умолчанию раз в 60 минут."""
    try:
        cfg = await get_ai_multichannel_settings()
    except Exception:
        logger.debug("wellness: failed to read ai multichannel settings", exc_info=True)
        cfg = {}
    enabled = bool(cfg.get("dm_interval_enabled", True))
    try:
        minutes = int(cfg.get("dm_interval_minutes") or 60)
    except (TypeError, ValueError):
        minutes = 60
    minutes = max(15, min(1440, minutes))
    return enabled, minutes


async def _compute_bootstrap_next_prompt(now: datetime, *, prompts_per_day: int) -> datetime:
    enabled, minutes = await _dm_interval_config()
    if enabled:
        return now + timedelta(minutes=minutes)
    return wellness_bootstrap_next_prompt_at(now, prompts_per_day=prompts_per_day)


async def _compute_next_prompt_after_send(
    *,
    send_time: datetime,
    scheduled_fire: datetime,
    interval_days: int,
    prompts_per_day: int,
) -> datetime:
    enabled, minutes = await _dm_interval_config()
    if enabled:
        return send_time + timedelta(minutes=minutes)
    return next_wellness_prompt_after_send(
        send_time=send_time,
        scheduled_fire=scheduled_fire,
        interval_days=interval_days,
        prompts_per_day=prompts_per_day,
    )


def _wellness_slot_hours(ppp: int) -> tuple[int, ...]:
    p = _normalize_prompts_per_day(ppp)
    m = int(getattr(settings, "WELLNESS_SLOT_MORNING_HOUR_UTC", 8) or 8)
    mid = int(getattr(settings, "WELLNESS_SLOT_MIDDAY_HOUR_UTC", 13) or 13)
    eve = int(getattr(settings, "WELLNESS_SLOT_EVENING_HOUR_UTC", 19) or 19)
    m, mid, eve = max(0, min(23, m)), max(0, min(23, mid)), max(0, min(23, eve))
    if p == 1:
        return (m,)
    if p == 2:
        return (m, mid)
    return (m, mid, eve)


def _utc_slot_datetime(d: date, hour: int, minute: int = 5) -> datetime:
    return datetime(d.year, d.month, d.day, max(0, min(23, hour)), minute, 0, 0)


def _slots_on_day(d: date, ppp: int) -> list[datetime]:
    return [_utc_slot_datetime(d, h, 5) for h in _wellness_slot_hours(ppp)]


def _slot_index_from_scheduled(scheduled: datetime, ppp: int) -> int:
    hours = _wellness_slot_hours(ppp)
    sh = int(scheduled.hour)
    for i, hh in enumerate(hours):
        if sh == hh:
            return i
    if sh < hours[0]:
        return 0
    for i in range(len(hours) - 1):
        if hours[i] <= sh < hours[i + 1]:
            return i
    return len(hours) - 1


def _scheduled_fire_datetime(now: datetime, stored_next: Optional[datetime], ppp: int) -> datetime:
    if stored_next is not None:
        return stored_next
    slots = _slots_on_day(now.date(), ppp)
    chosen = slots[0]
    for s in slots:
        if s <= now:
            chosen = s
    return chosen


def first_upcoming_wellness_prompt_at(now: datetime, *, prompts_per_day: int) -> datetime:
    """Ближайший слот строго после текущего момента (сегодня или завтра)."""
    ppp = _normalize_prompts_per_day(prompts_per_day)
    today = now.date()
    for dt in _slots_on_day(today, ppp):
        if dt > now:
            return dt
    nxt = today + timedelta(days=1)
    return _slots_on_day(nxt, ppp)[0]


def wellness_bootstrap_next_prompt_at(now: datetime, *, prompts_per_day: int) -> datetime:
    """Первое напоминание: не позже чем через час, либо ближайший слот — что наступит раньше."""
    ppp = _normalize_prompts_per_day(prompts_per_day)
    soon = now + timedelta(hours=1)
    slot = first_upcoming_wellness_prompt_at(now, prompts_per_day=ppp)
    return min(soon, slot)


def next_wellness_prompt_after_send(
    *,
    send_time: datetime,
    scheduled_fire: datetime,
    interval_days: int,
    prompts_per_day: int,
) -> datetime:
    """После отправки: следующий слот в этот же день или первый слот через interval_days после последнего слота дня."""
    iv = _normalize_interval(interval_days)
    ppp = _normalize_prompts_per_day(prompts_per_day)
    hours = _wellness_slot_hours(ppp)
    idx = _slot_index_from_scheduled(scheduled_fire, ppp)
    day = send_time.date()
    if idx < ppp - 1:
        nh = hours[idx + 1]
        return _utc_slot_datetime(day, nh, 5)
    next_cycle = day + timedelta(days=iv)
    return _utc_slot_datetime(next_cycle, hours[0], 5)


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

    if any(x in t for x in ("каждый час", "ежечасно", "раз в час", "1 раз в час", "каждый 1 час")):
        await database.execute(
            users.update()
            .where(users.c.id == uid)
            .values(wellness_next_prompt_at=datetime.utcnow() + timedelta(hours=1))
        )
        return

    ppp_new: Optional[int] = None
    if any(
        x in t
        for x in (
            "три раза в день",
            "три раза",
            "3 раза в день",
            "3 раза",
            "трёх раз в день",
            "трех раз в день",
        )
    ):
        ppp_new = 3
    elif any(
        x in t
        for x in (
            "два раза в день",
            "два раза",
            "2 раза в день",
            "2 раза",
            "утром и вечером",
            "утром и днём",
            "утром и днем",
            "дважды",
        )
    ):
        ppp_new = 2
    elif any(x in t for x in ("один раз в день", "только утром", "только раз в день")):
        ppp_new = 1

    interval = None
    if "каждый день" in t or "раз в день" in t or "ежедневно" in t:
        interval = 1
    elif "3 дня" in t or "три дня" in t or "раз в три" in t:
        interval = 3
    elif "5 дн" in t or "пять дн" in t or "раз в пять" in t:
        interval = 5
    elif "недел" in t or "раз в 7" in t:
        interval = 7

    if ppp_new is not None or interval is not None:
        upd: dict[str, Any] = {}
        if ppp_new is not None:
            upd["wellness_journal_prompts_per_day"] = ppp_new
        if interval is not None:
            upd["wellness_journal_interval_days"] = interval
        p_eff = upd.get("wellness_journal_prompts_per_day")
        if p_eff is None:
            p_eff = _normalize_prompts_per_day(row.get("wellness_journal_prompts_per_day"))
        upd["wellness_next_prompt_at"] = await _compute_bootstrap_next_prompt(
            datetime.utcnow(), prompts_per_day=int(p_eff)
        )
        await database.execute(users.update().where(users.c.id == uid).values(**upd))


async def _count_ai_prompts(user_id: int) -> int:
    r = await database.fetch_val(
        sa.select(sa.func.count())
        .select_from(wellness_journal_entries)
        .where(wellness_journal_entries.c.user_id == user_id)
        .where(wellness_journal_entries.c.role == "ai_prompt"),
    )
    return int(r or 0)


async def send_wellness_prompt_for_user(user_id: int, *, admin_self_test: bool = False) -> bool:
    """Отправить промпт дневника. admin_self_test=True — только для кнопки в админке: без сдвига расписания."""
    uid = int(user_id)
    if not admin_self_test:
        if not await wellness_journal_globally_enabled():
            return False
    if not await user_has_wellness_journal_access(uid):
        return False
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row or row.get("wellness_journal_opt_out"):
        return False
    if not admin_self_test and row.get("wellness_journal_admin_paused"):
        return False
    if not admin_self_test and _user_row_is_admin(row):
        return False
    interval = _normalize_interval(row.get("wellness_journal_interval_days"))
    ppp = _normalize_prompts_per_day(row.get("wellness_journal_prompts_per_day"))
    nxt = row.get("wellness_next_prompt_at")
    now = datetime.utcnow()
    if not admin_self_test:
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
    coach_id = await resolve_wellness_dm_sender_id(notify_uid)
    if not coach_id:
        logger.warning("wellness: no DM sender id for recipient notify_uid=%s", notify_uid)
        return False
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
        if not admin_self_test:
            fired = _scheduled_fire_datetime(now, nxt, ppp)
            nxt_after = await _compute_next_prompt_after_send(
                send_time=now,
                scheduled_fire=fired,
                interval_days=interval,
                prompts_per_day=ppp,
            )
            await database.execute(
                users.update()
                .where(users.c.id == uid)
                .values(
                    wellness_last_prompt_at=now,
                    wellness_next_prompt_at=nxt_after,
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
            SELECT id, primary_user_id, role, wellness_journal_interval_days, wellness_journal_prompts_per_day,
                   wellness_next_prompt_at, wellness_journal_opt_out, wellness_journal_admin_paused
            FROM users
            WHERE wellness_journal_opt_out = false
              AND wellness_journal_admin_paused = false
              AND (wellness_next_prompt_at IS NULL OR wellness_next_prompt_at <= NOW())
            ORDER BY id ASC
            LIMIT 300
            """
        )
    )
    n_scanned = len(rows)
    n_sent = 0
    for row in rows:
        uid = int(row["primary_user_id"] or row["id"])
        if (row.get("role") or "").strip().lower() == "admin":
            continue
        if not await user_has_wellness_journal_access(uid):
            continue
        if row["wellness_next_prompt_at"] is None:
            ppp = _normalize_prompts_per_day(row.get("wellness_journal_prompts_per_day"))
            nxt0 = await _compute_bootstrap_next_prompt(datetime.utcnow(), prompts_per_day=ppp)
            await database.execute(
                users.update().where(users.c.id == uid).values(wellness_next_prompt_at=nxt0)
            )
        if await send_wellness_prompt_for_user(uid):
            n_sent += 1
    logger.info(
        "wellness_prompts_due_job: candidates=%s prompts_sent=%s",
        n_scanned,
        n_sent,
    )


async def _maybe_send_weekly_digest(user_id: int) -> None:
    uid = int(user_id)
    if not await user_has_wellness_journal_access(uid) or not await wellness_journal_globally_enabled():
        return
    row = await database.fetch_one(users.select().where(users.c.id == uid))
    if not row or row.get("wellness_journal_opt_out") or row.get("wellness_journal_admin_paused"):
        return
    if _user_row_is_admin(row):
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
        .where(wellness_journal_entries.c.statistics_excluded == False)
        .where(wellness_journal_entries.c.created_at >= now - timedelta(days=7)),
    ) or 0
    if int(replies) < 1:
        await database.execute(
            users.update().where(users.c.id == uid).values(wellness_weekly_digest_last_at=now)
        )
        return
    notify_uid = _notify_uid(dict(row))
    coach_id = await resolve_wellness_dm_sender_id(notify_uid)
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
    include_in_stats: bool = False,
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
            statistics_excluded=not bool(include_in_stats),
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
    """Сообщение пользователя в ЛС NeuroFungi AI: запись ответа и запрос подтверждения для статистики."""
    peers = await all_legacy_neurofungi_ai_peer_ids()
    if not peers or int(recipient_id) not in peers:
        return None
    uid = int(sender_uid)
    if not await user_has_wellness_journal_access(uid):
        return None

    urow = await database.fetch_one(users.select().where(users.c.id == uid))
    if not urow:
        return None
    notify_dm = _notify_uid(dict(urow))
    coach_id = await resolve_wellness_dm_sender_id(notify_dm)
    if not coach_id:
        return None

    try:
        from services.wellness_bundle_feedback_service import parse_bundle_feedback_command, record_bundle_feedback

        bf = parse_bundle_feedback_command(text)
        if bf:
            bid, vote = bf
            await record_bundle_feedback(uid, bid, vote, direct_message_id=direct_message_id)
            await _insert_coach_dm(
                int(coach_id),
                notify_dm,
                MSG_PREFIX + f"Записал оценку связки «{bid}» ({vote:+d}). Учтём в сводной статистике.",
            )
            return None
    except Exception:
        logger.debug("wellness bundle feedback cmd skipped", exc_info=True)

    if _user_row_is_admin(urow):
        await database.execute(
            users.update()
            .where(users.c.id == uid)
            .values(
                wellness_pending_stats_entry_id=None,
                wellness_awaiting_which_stats_after_decline=False,
            )
        )
        silent = bool(urow.get("wellness_admin_ai_silent"))
        eid = await record_user_reply(
            uid,
            text,
            direct_message_id=direct_message_id,
            include_in_stats=True,
        )
        if not eid:
            return None
        asyncio.create_task(
            extract_wellness_json_async(
                int(eid),
                (text or "").strip(),
                after_admin_chain=not silent,
            )
        )
        return int(eid)

    awaiting_which = bool(urow.get("wellness_awaiting_which_stats_after_decline"))
    pending_raw = urow.get("wellness_pending_stats_entry_id")

    # После «нет» на включение в статистику: ждём «никакое» или новый текст (снова да/нет)
    if awaiting_which and pending_raw is None:
        if _parse_which_stats_none_reply(text):
            await database.execute(
                users.update()
                .where(users.c.id == uid)
                .values(wellness_awaiting_which_stats_after_decline=False)
            )
            await _insert_coach_dm(
                int(coach_id),
                notify_dm,
                MSG_PREFIX + "Хорошо — в статистику из этой переписки ничего не добавляю.",
            )
            return None
        if _is_vague_other_only_reply(text):
            await _insert_coach_dm(
                int(coach_id),
                notify_dm,
                MSG_PREFIX
                + "Напиши **одним сообщением** полный текст, который нужно включить в статистику, или **никакое**, если не включать ничего.",
            )
            return None
        eid_new = await record_user_reply(uid, text, direct_message_id=direct_message_id)
        if not eid_new:
            return None
        await database.execute(
            users.update()
            .where(users.c.id == uid)
            .values(
                wellness_pending_stats_entry_id=int(eid_new),
                wellness_awaiting_which_stats_after_decline=False,
            )
        )
        snippet = (text or "").strip()
        if len(snippet) > 900:
            snippet = snippet[:900] + "…"
        body = (
            MSG_PREFIX
            + "Твой ответ (для статистики):\n\n«"
            + snippet
            + "»\n\nВключить это сообщение в статистику дневника? Напиши «да» или «нет»."
        )
        await _insert_coach_dm(int(coach_id), notify_dm, body)
        return int(eid_new)

    if pending_raw is not None:
        pending = int(pending_raw)
        verdict = _parse_stats_confirmation_reply(text)
        ent = await database.fetch_one(
            wellness_journal_entries.select().where(wellness_journal_entries.c.id == pending)
        )
        if ent and int(ent["user_id"] or 0) == uid:
            if verdict == "yes":
                raw_prev = (ent.get("raw_text") or "").strip()
                await database.execute(
                    wellness_journal_entries.update()
                    .where(wellness_journal_entries.c.id == pending)
                    .values(statistics_excluded=False)
                )
                await database.execute(
                    users.update()
                    .where(users.c.id == uid)
                    .values(
                        wellness_pending_stats_entry_id=None,
                        wellness_awaiting_which_stats_after_decline=False,
                    )
                )
                asyncio.create_task(extract_wellness_json_async(pending, raw_prev))
                await _insert_coach_dm(
                    int(coach_id),
                    notify_dm,
                    MSG_PREFIX + "Хорошо — включаю твой предыдущий ответ в статистику дневника.",
                )
                return None
            if verdict == "no":
                await database.execute(
                    wellness_journal_entries.update()
                    .where(wellness_journal_entries.c.id == pending)
                    .values(statistics_excluded=True)
                )
                await database.execute(
                    users.update()
                    .where(users.c.id == uid)
                    .values(
                        wellness_pending_stats_entry_id=None,
                        wellness_awaiting_which_stats_after_decline=True,
                    )
                )
                await _insert_coach_dm(
                    int(coach_id),
                    notify_dm,
                    MSG_PREFIX
                    + "Понял — не включаю это сообщение в статистику.\n\n"
                    + "Какое сообщение тогда включить в статистику? Пришли его **целиком одним следующим сообщением**.\n"
                    + "Если ничего включать не нужно — напиши **никакое** — на этом закончим.",
                )
                return None
        # Не распознали да/нет или запись чужая — снимаем ожидание со старой записи
        if ent and int(ent["user_id"] or 0) == uid:
            await database.execute(
                wellness_journal_entries.update()
                .where(wellness_journal_entries.c.id == pending)
                .values(statistics_excluded=True)
            )
        await database.execute(
            users.update()
            .where(users.c.id == uid)
            .values(
                wellness_pending_stats_entry_id=None,
                wellness_awaiting_which_stats_after_decline=False,
            )
        )

    eid = await record_user_reply(uid, text, direct_message_id=direct_message_id)
    if not eid:
        return None
    await database.execute(
        users.update()
        .where(users.c.id == uid)
        .values(
            wellness_pending_stats_entry_id=int(eid),
            wellness_awaiting_which_stats_after_decline=False,
        )
    )
    snippet = (text or "").strip()
    if len(snippet) > 900:
        snippet = snippet[:900] + "…"
    body = (
        MSG_PREFIX
        + "Твой ответ (для статистики):\n\n«"
        + snippet
        + "»\n\nВключить это сообщение в статистику дневника? Напиши «да» или «нет»."
    )
    await _insert_coach_dm(int(coach_id), notify_dm, body)
    return int(eid)


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
  "anxiety_0_10": number|null,
  "concentration_0_10": number|null,
  "sleep_quality_0_10": number|null,
  "sleep_note": string|null,
  "fatigue_0_10": number|null,
  "body_tension_0_10": number|null,
  "libido_0_10": number|null,
  "appetite_0_10": number|null,
  "panic_today": boolean|null,
  "apathy_0_10": number|null,
  "irritability_0_10": number|null,
  "stress_0_10": number|null,
  "immunity_perceived_0_10": number|null,
  "metabolic_weight_focus": boolean|null,
  "took_mushrooms_today": boolean|null,
  "mushrooms": string[],
  "dose_notes": string|null,
  "dosage_amount_text": string|null,
  "timing": string|null,
  "physical_symptoms": string[],
  "mental_symptoms": string[],
  "substances_other": string[],
  "motivation_why_mushrooms": string|null,
  "life_goal_short": string|null,
  "trigger_or_distortion": string|null,
  "free_summary": string|null
}
Используй пустые массивы если нет данных. Числа — целые 0–10 или null. boolean — true/false/null если не сказано. Язык значений — русский."""


async def extract_wellness_json_async(
    entry_id: int, raw_text: str, *, after_admin_chain: bool = False
) -> None:
    extraction_ok = False
    if getattr(settings, "OPENAI_API_KEY", None):
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
                max_tokens=2000,
                response_format={"type": "json_object"},
            )
            content = (resp.choices[0].message.content or "").strip()
            json.loads(content)  # validate
            await database.execute(
                wellness_journal_entries.update()
                .where(wellness_journal_entries.c.id == int(entry_id))
                .values(extracted_json=content)
            )
            try:
                from services.wellness_insights_service import upsert_daily_snapshot_from_extracted_entry

                await upsert_daily_snapshot_from_extracted_entry(int(entry_id), content)
            except Exception:
                logger.debug("wellness: snapshot upsert skipped", exc_info=True)
            extraction_ok = True
        except Exception:
            logger.exception("wellness: extraction failed entry_id=%s", entry_id)
    elif after_admin_chain:
        logger.debug("wellness: OPENAI_API_KEY missing, skip extraction entry_id=%s", entry_id)

    if after_admin_chain:
        try:
            await _send_admin_chain_followup(int(entry_id), extraction_ok=extraction_ok)
        except Exception:
            logger.debug("wellness: admin chain followup skipped", exc_info=True)


def aggregate_entries_for_display(rows: list[dict]) -> dict[str, Any]:
    """Агрегаты для страницы «Мои результаты»."""
    by_mushroom: dict[str, int] = {}
    moods: list[int] = []
    energies: list[int] = []
    timeline: list[dict] = []
    for r in rows:
        if r.get("role") != "user_reply":
            continue
        if r.get("statistics_excluded"):
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
        "reply_count": len(
            [
                x
                for x in rows
                if x.get("role") == "user_reply" and not x.get("statistics_excluded")
            ]
        ),
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
        WHERE w.role = 'user_reply' AND w.statistics_excluded = false AND w.created_at >= :since
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
        if sp != "free" and sub_end and not admin_gr:
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
        ufull = await database.fetch_one(users.select().where(users.c.id == uid))
        nu = _notify_uid(dict(ufull)) if ufull else uid
        cid = await resolve_wellness_dm_sender_id(nu)
        if not cid:
            continue
        await _deliver_renewal_nudge(uid, int(cid), target)


def _safe_cluster_model_for_template(cm: dict[str, Any] | None) -> dict[str, Any] | None:
    if not cm:
        return None
    k = cm.get("k")
    try:
        k_i = int(k) if k is not None else None
    except (TypeError, ValueError):
        k_i = None
    uc = cm.get("user_count")
    try:
        uc_i = int(uc) if uc is not None else None
    except (TypeError, ValueError):
        uc_i = None
    mv = cm.get("model_version")
    try:
        mv_i = int(mv) if mv is not None else None
    except (TypeError, ValueError):
        mv_i = None
    return {"k": k_i, "user_count": uc_i, "model_version": mv_i}


def _safe_scheme_effect_rows(rows: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for x in rows:
        d = dict(x)
        v = d.get("avg_progress_score")
        try:
            d["avg_progress_score"] = float(v) if v is not None else None
        except (TypeError, ValueError):
            d["avg_progress_score"] = None
        out.append(d)
    return out


def _safe_rec_arm_rows(rows: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for x in rows:
        d = dict(x)
        out.append(
            {
                "bundle_key": str(d.get("bundle_key") or "")[:128],
                "segment": str(d.get("segment") or "")[:160],
                "successes": int(d.get("successes") or 0),
                "trials": int(d.get("trials") or 0),
            }
        )
    return out


EMPTY_ADMIN_WELLNESS_SUMMARY: dict[str, Any] = {
    "users_with_replies_ever": 0,
    "users_with_replies_30d": 0,
    "replies_ever": 0,
    "replies_30d": 0,
    "prompts_30d": 0,
    "mood_avg_sample": None,
    "energy_avg_sample": None,
    "mushroom_top": [],
    "mushroom_bars": [],
    "engagement_pct_30": 0,
    "sample_moods_n": 0,
    "therapy_profiles_n": 0,
    "scheme_effect_rows": [],
    "rec_arm_rows": [],
    "cluster_model": None,
    "kmeans_cluster_dist": [],
    "automation_high_retention": 0,
}


async def admin_global_wellness_summary() -> dict[str, Any]:
    """Агрегаты по всем пользователям для админской сводки. При любой ошибке — пустая сводка (страница не падает)."""
    try:
        return await _admin_global_wellness_summary_impl()
    except Exception:
        logger.exception("admin_global_wellness_summary: fatal, returning empty summary")
        return dict(EMPTY_ADMIN_WELLNESS_SUMMARY)


async def _admin_global_wellness_summary_impl() -> dict[str, Any]:
    """Реализация сводки (без внешнего try)."""
    since30 = datetime.utcnow() - timedelta(days=30)
    # == False совместимее с SQLite/старыми Postgres, чем IS FALSE
    _in_stats = wellness_journal_entries.c.statistics_excluded == False
    nu = await database.fetch_val(
        sa.select(sa.func.count(sa.distinct(wellness_journal_entries.c.user_id)))
        .select_from(wellness_journal_entries)
        .where(wellness_journal_entries.c.role == "user_reply")
        .where(_in_stats)
    )
    nu30 = await database.fetch_val(
        sa.select(sa.func.count(sa.distinct(wellness_journal_entries.c.user_id)))
        .select_from(wellness_journal_entries)
        .where(wellness_journal_entries.c.role == "user_reply")
        .where(_in_stats)
        .where(wellness_journal_entries.c.created_at >= since30)
    )
    rep_all = await database.fetch_val(
        sa.select(sa.func.count())
        .select_from(wellness_journal_entries)
        .where(wellness_journal_entries.c.role == "user_reply")
        .where(_in_stats)
    )
    rep30 = await database.fetch_val(
        sa.select(sa.func.count())
        .select_from(wellness_journal_entries)
        .where(wellness_journal_entries.c.role == "user_reply")
        .where(_in_stats)
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
        .where(_in_stats)
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
    prof_n = 0
    try:
        # Без TRIM: на части драйверов/типов trim(json) давал ошибку; достаточно length > 2.
        prof_n = await database.fetch_val(
            sa.select(sa.func.count())
            .select_from(users)
            .where(users.c.wellness_ai_profile_json.isnot(None))
            .where(sa.func.coalesce(sa.func.length(users.c.wellness_ai_profile_json), 0) > 2)
        )
    except Exception:
        logger.warning("admin_global_wellness_summary: therapy_profiles_n query failed", exc_info=True)
    scheme_rows: list[dict] = []
    try:
        scheme_rows = await database.fetch_all(
            wellness_scheme_effect_stats.select()
            .order_by(wellness_scheme_effect_stats.c.sample_n.desc())
            .limit(18)
        )
    except Exception:
        logger.warning("admin_global_wellness_summary: scheme_effect_stats query failed", exc_info=True)
    rec_arm_rows: list[dict] = []
    cluster_model: dict | None = None
    kmeans_cluster_dist: list[dict] = []
    automation_high_retention = 0
    try:
        from db.models import users as users_t, wellness_user_automation

        from services.wellness_bandit_service import list_rec_arm_stats
        from services.wellness_clustering_service import latest_cluster_model_summary

        rec_arm_rows = _safe_rec_arm_rows(await list_rec_arm_stats(12))
        _raw_cm = await latest_cluster_model_summary()
        cluster_model = _safe_cluster_model_for_template(_raw_cm)
        _cnt = sa.func.count().label("n")
        cd = await database.fetch_all(
            sa.select(users_t.c.wellness_kmeans_cluster_id, _cnt)
            .where(users_t.c.primary_user_id.is_(None))
            .where(users_t.c.wellness_kmeans_cluster_id.isnot(None))
            .group_by(users_t.c.wellness_kmeans_cluster_id)
            .order_by(sa.desc(_cnt))
        )
        kmeans_cluster_dist = []
        for r in cd:
            cid = r.get("wellness_kmeans_cluster_id")
            try:
                cid_out = int(cid) if cid is not None else None
            except (TypeError, ValueError):
                cid_out = None
            kmeans_cluster_dist.append({"cluster_id": cid_out, "n": int(r.get("n") or 0)})
        automation_high_retention = int(
            await database.fetch_val(
                sa.select(sa.func.count())
                .select_from(wellness_user_automation)
                .where(wellness_user_automation.c.retention_risk == "high")
            )
            or 0
        )
    except Exception:
        logger.warning("admin_global_wellness_summary: bandit/cluster/automation block failed", exc_info=True)
    mushroom_top = sorted(by_mushroom.items(), key=lambda x: -x[1])[:20]
    max_cnt = max((int(c) for _, c in mushroom_top), default=1)
    if max_cnt < 1:
        max_cnt = 1
    mushroom_bars: list[dict[str, Any]] = []
    for name, cnt in mushroom_top:
        c = int(cnt)
        mushroom_bars.append(
            {"name": str(name), "cnt": c, "width_pct": min(100, (c * 100) // max_cnt)}
        )
    rep_ever_i = int(rep_all or 0)
    rep_30_i = int(rep30 or 0)
    den_eng = max(1, rep_ever_i)
    engagement_pct_30 = min(100, (rep_30_i * 100) // den_eng)
    return {
        "users_with_replies_ever": int(nu or 0),
        "users_with_replies_30d": int(nu30 or 0),
        "replies_ever": rep_ever_i,
        "replies_30d": rep_30_i,
        "prompts_30d": int(pr30 or 0),
        "mood_avg_sample": round(sum(moods) / len(moods), 2) if moods else None,
        "energy_avg_sample": round(sum(energies) / len(energies), 2) if energies else None,
        "mushroom_top": mushroom_top,
        "mushroom_bars": mushroom_bars,
        "engagement_pct_30": engagement_pct_30,
        "sample_moods_n": len(moods),
        "therapy_profiles_n": int(prof_n or 0),
        "scheme_effect_rows": _safe_scheme_effect_rows([dict(x) for x in scheme_rows]),
        "rec_arm_rows": rec_arm_rows,
        "cluster_model": cluster_model,
        "kmeans_cluster_dist": kmeans_cluster_dist,
        "automation_high_retention": automation_high_retention,
    }


async def set_user_wellness_pdf_allowed(user_id: int, allowed: bool) -> None:
    await database.execute(
        users.update()
        .where(users.c.id == int(user_id))
        .values(wellness_journal_pdf_allowed=bool(allowed))
    )
