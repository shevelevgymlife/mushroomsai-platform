"""
Генерация ежедневных сообщений NeuroFungi AI (дневник): тон «подруга в переписке», КПТ, контекст ЛС и обучающих постов.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from config import settings
from db.database import database
from db.models import ai_settings
from services.ai_behavior_config import append_wellness_aspect_addons
from services.ai_multichannel_settings import get_ai_multichannel_settings

logger = logging.getLogger(__name__)

WELLNESS_COACH_SYSTEM_BASE = """Ты — NeuroFungi AI: девушка, которая пишет в личку как близкая подруга — тепло, по-человечески, на «ты», с лёгким юмором там, где уместно. Ты не холодный бот и не сухой опросник.

СТИЛЬ ПЕРЕПИСКИ:
- Общайся как в мессенджере с другом: короткие абзацы, живые обороты, можно эмодзи очень умеренно (не больше одного-двух на сообщение).
- Сначала отзовись на контекст того, что человек уже писал (если в данных есть переписка) — хотя бы одной фразой, чтобы было видно, что ты читала историю.
- В каждом новом ежедневном сообщении придумывай СВЕЖИЙ вопрос для сбора данных дневника: другая тема, другой угол, другая формулировка. Не повторяй вопросы из прошлых твоих сообщений буквально и не клонируй структуру «вчерашнего» блока.
- Совмещай это с мягкой провокативной терапией (в духе Франкла/разговорных провокаций) и КПТ: ситуация → мысль → эмоция → поведение; «за/против» по мыслям — без унижения и морали.
- Фунготерапия и грибы — только образовательно и как самонаблюдение; не медицинские назначения, не обещай исцеление, не спорь с врачом.
- Если передан «внутренний контекст» по грибам и связкам — используй как повод для вопросов, не как рецепт и не называй дозы как инструкцию из приложения.

ПЛАТФОРМА (только то, что видит пользователь):
- Можешь кратко предложить помощь в навигации: чаты, сообщество, магазин, подписка, личный кабинет.
- Если что-то непонятно в интерфейсе — предложи спросить; подскажешь в рамках пользовательского функционала.

РЕФЕРАЛЫ И СОТРУДНИЧЕСТВО:
- Время от времени (не в каждом сообщении) мягко напоминай про реферальную программу и сотрудничество с магазином — меню-бургер → «Реферальная программа».

ПЕРИОДИЧЕСКИ:
- Примерно раз в несколько сообщений спроси, что улучшить на платформе — ответ уйдёт команде.
- Предложи помочь с сервисом, если человеку нужно.

ЦЕЛЬ ДИАЛОГА:
- Мягко держи в фокусе главную цель человека; задавай открытые вопросы; не обрывай диалог — в конце зови ответить; можно «если на сегодня хватит — напиши: хватит на сегодня».
- Для статистики платформы иногда отдельной короткой строкой: «связка anti_stress +» или «связка energy_brain -» (латиница, как в «Мои результаты»).
- Спроси про удобство частоты сообщений коротко, если давно не спрашивала.
- Сводка и графики — в «Мои результаты» в кабинете (без выдуманных ссылок).

ФОРМАТ ОТВЕТА:
- Только текст на русском для пользователя, без Markdown-заголовков и без «Роль:».
- Примерно 400–1800 символов: по делу, по-приятельски, без канцелярита.
"""


async def _get_admin_chat_system_prompt() -> str:
    try:
        row = await database.fetch_one(
            ai_settings.select().order_by(ai_settings.c.updated_at.desc()).limit(1)
        )
        if not row:
            return ""
        txt = (row.get("system_prompt") or "").strip()
        return txt[:12000]
    except Exception:
        logger.debug("wellness_coach_ai: load admin system prompt failed", exc_info=True)
        return ""


async def build_wellness_coach_system_prompt() -> str:
    ch = await get_ai_multichannel_settings()
    dm_prompt = (ch.get("dm_prompt") or "").strip()
    dm_algo = (ch.get("dm_algorithm_prompt") or "").strip()
    admin_prompt = await _get_admin_chat_system_prompt()
    parts = [
        WELLNESS_COACH_SYSTEM_BASE,
        "ВАЖНО: для фактов по фунготерапии, грибам, дозировкам и рекомендациям используй только обучающие посты платформы из переданного контекста. "
        "Если факта нет в обучающих постах — честно скажи, что данных в базе нет, и запроси уточнение.",
    ]
    if admin_prompt:
        parts.append("ГЛОБАЛЬНЫЙ СТИЛЬ ИЗ АДМИНКИ AI (соблюдай строго):\n" + admin_prompt)
    if dm_prompt:
        parts.append("КАНАЛЬНЫЙ ПРОМПТ ЛС (соблюдай строго):\n" + dm_prompt)
    if dm_algo:
        parts.append("АЛГОРИТМ ЛИЧНЫХ СООБЩЕНИЙ (соблюдай строго):\n" + dm_algo)
    try:
        await append_wellness_aspect_addons(parts)
    except Exception:
        logger.debug("append_wellness_aspect_addons failed", exc_info=True)
    return "\n\n".join(parts)


async def generate_wellness_coach_message(
    *,
    user_name: str,
    thread_snippets: list[str],
    knowledge_excerpts: list[dict[str, Any]],
    stats_summary: dict[str, Any],
    prompt_index: int,
    therapy_context: str = "",
    recent_ai_prompt_excerpts: list[str] | None = None,
) -> Optional[str]:
    """Вернуть текст сообщения или None — тогда используется шаблон из wellness_journal_service."""
    if not getattr(settings, "OPENAI_API_KEY", None):
        return None
    system_prompt = await build_wellness_coach_system_prompt()
    k_parts = []
    for p in knowledge_excerpts[:14]:
        title = (p.get("title") or "")[:120]
        body = (p.get("content") or "")[:900]
        if title or body:
            k_parts.append(f"### {title}\n{body}")
    kb = "\n\n---\n\n".join(k_parts)[:12000]
    thread = "\n".join(f"- {s[:500]}" for s in thread_snippets[:24])[:8000]
    stats_json = json.dumps(stats_summary, ensure_ascii=False, default=str)[:4000]
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    prev_prompts = recent_ai_prompt_excerpts or []
    prev_block = ""
    if prev_prompts:
        prev_lines = "\n".join(f"--- предыдущее напоминание #{i+1} (не копируй) ---\n{p[:520]}" for i, p in enumerate(prev_prompts[:8]))
        prev_block = (
            "Твои недавние напоминания-дневник (НЕ повторяй темы и формулировки, придумай новый угол на сегодня):\n"
            f"{prev_lines}\n\n"
        )
    user_msg = (
        f"Сегодня по календарю (ориентир для новизны вопросов): {today_utc}\n"
        f"Имя (как обращаться): {user_name or 'Участник'}\n"
        f"Номер по счёту напоминания (примерно): {prompt_index}\n"
        f"Агрегаты дневника (JSON): {stats_json}\n\n"
        f"Фрагменты из обучающих материалов платформы:\n{kb or '(нет подборки)'}\n\n"
        f"Последние реплики в нашем чате (читай внимательно и отталкивайся от них — как подруга, которая помнит переписку):\n"
        f"{thread or '(пока мало переписки)'}\n\n"
        f"{prev_block}"
        "Сформируй ОДНО новое сообщение для лички: тон — девушка-друг, не канцелярит.\n"
        "Центральный блок: один главный НОВЫЙ вопрос дня для сбора данных (другая смысловая ось, чем в предыдущих напоминаниях выше). "
        "Можно связать с тем, что человек уже писал в переписке.\n"
        "Вариативно меняй подачу: иногда теплее, иногда короче, иногда с лёгким юмором — но всегда уважительно.\n\n"
        "Обязательно короткая фраза про фиксацию в статистике: "
        "«Я твой ответ записываю в базу, подтвердим отдельно да/нет».\n\n"
        "Мягко напомни про самонаблюдение в ответе (шкалы 0–10 или да/нет), без занудного списка каждый раз — "
        "чередуй акценты: тревога, настроение, энергия, концентрация, сон; тело; стресс; иммунитет по ощущениям; "
        "паника; грибы сегодня (тип, доза, время) — не всё сразу обязательно.\n\n"
        f"{therapy_context or ''}\n\n"
        "Не дублируй дословно прошлые сообщения; это новый день и новая микро-тема."
    )
    try:
        from openai import AsyncOpenAI

        cli = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        resp = await cli.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.82,
            max_tokens=2200,
        )
        out = (resp.choices[0].message.content or "").strip()
        if len(out) < 80:
            return None
        return out[:6000]
    except Exception:
        logger.exception("wellness_coach_ai: generate failed")
        return None
