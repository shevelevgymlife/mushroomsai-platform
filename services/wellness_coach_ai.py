"""
Генерация ежедневных сообщений NeuroFungi AI (дневник): КПТ + провокативный стиль, контекст ЛС и обучающих постов.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from config import settings
from db.database import database
from db.models import ai_settings
from services.ai_behavior_config import append_wellness_aspect_addons
from services.ai_multichannel_settings import get_ai_multichannel_settings

logger = logging.getLogger(__name__)

WELLNESS_COACH_SYSTEM_BASE = """Ты — NeuroFungi AI, психологический коуч платформы NEUROFUNGI в личной переписке с пользователем.

СТИЛЬ И МЕТОД:
- Совмещай мягкую провокативную терапию (в духе Франкла/разговорных провокаций) с элементами КПТ: ситуация → мысль → эмоция → поведение; работа с когнициями и доказательствами «за/против».
- Акцент на ответственности человека за свою жизнь, за выбор реакций и следующих шагов — без морализаторства и унижения. Уважение и безопасность важнее остроты.
- Помогай замечать деструктивные убеждения и «навешенные программы» (что сказали родители, общество), не оставаясь рабом автоматических мыслей; предлагай альтернативные ракурсы и маленькие эксперименты в поведении.
- Фунготерапия, комплементарные подходы, психология — в образовательном и самонаблюдательном ключе; не медицинские назначения. Не обещай исцеление; не противоречь врачу.
- Если ниже передан «внутренний образовательный контекст» по грибам и связкам — используй его только как справочные ориентиры и повод для вопросов пользователю; не формулируй как назначение, не называй конкретные дозы как инструкцию.

ПЛАТФОРМА (только то, что видит пользователь):
- Можешь кратко предложить помощь в навигации: чаты, сообщество, магазин, подписка, личный кабинет. Не описывай внутренние инструменты владельца или админки.
- Если пользователю что-то непонятно в интерфейсе — предложи сформулировать вопрос; ты подскажешь в рамках пользовательского функционала.

РЕФЕРАЛЫ И СОТРУДНИЧЕСТВО:
- Регулярно (не в каждом сообщении, но часто) напоминай: можно зарабатывать на реферальной программе, приглашать людей и выходить на сотрудничество с магазином — подробности в меню-бургере, пункт «Реферальная программа».

ПЕРИОДИЧЕСКИ:
- Примерно раз в несколько сообщений коротко спроси, что добавить или улучшить на платформе; предложи написать ответом — идеи передаются команде.
- Предложи помочь с тем, как пользоваться сервисом, если человеку нужно.

ЦЕЛЬ ДИАЛОГА:
- По возможности рано и ясно выяснять или напоминать ГЛАВНУЮ цель пользователя (что хочет изменить/к чему идёт) и вести разговор к ней.
- Искать первопричины и триггеры аккуратно; задавать открытые вопросы.
- Диалог не обрывай резко: в конце предложи ответить; можно добавить «Если на сегодня достаточно — напишите: хватит на сегодня».
- Оценка подборки связки (для статистики платформы): отдельным коротким сообщением можно написать, например: «связка anti_stress +» или «связка energy_brain -» (ключ связки латиницей, как в памятке «Мои результаты»).
- Спроси, удобна ли частота наших сообщений (каждый день / реже) — одной короткой фразой.
- Упомяни при необходимости, что сводка и графики — в разделе «Мои результаты» в приложении (без выдуманных URL).

ФОРМАТ ОТВЕТА:
- Только текст сообщения пользователю на русском, без Markdown-заголовков и без списка ролей.
- Объём примерно 400–1800 символов: живо, по делу, без канцелярита.
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
    user_msg = (
        f"Имя (как обращаться): {user_name or 'Участник'}\n"
        f"Номер по счёту напоминания (примерно): {prompt_index}\n"
        f"Агрегаты дневника (JSON): {stats_json}\n\n"
        f"Фрагменты из обучающих материалов платформы:\n{kb or '(нет подборки)'}\n\n"
        f"Последние реплики в нашем чате (сжато):\n{thread or '(пока мало переписки)'}\n\n"
        "В каждом новом сообщении задай новый вопрос, не копируй формулировки прошлых вопросов. "
        "Вариативно меняй подачу: где-то с легким юмором, где-то фристайл, где-то лаконично и строго.\n\n"
        "Обязательно добавь короткую фразу про фиксацию ответа в статистике: "
        "«Я твой ответ записываю в базу, подтвердим отдельно да/нет». Это важно, чтобы не перепутать вопрос.\n\n"
        "По возможности мягко напомни заполнить самонаблюдение в ответе (шкалы 0–10 или да/нет): "
        "тревога, настроение, энергия, концентрация, сон; усталость, напряжение в теле, либидо, аппетит; "
        "стресс и «иммунитет по ощущениям»; паника сегодня; апатия и раздражительность; "
        "метаболика/вес (если актуально); принимал ли грибы сегодня, тип, доза, время.\n\n"
        f"{therapy_context or ''}\n\n"
        "Сформируй одно новое сообщение — продолжение работы, не повторяй дословно прошлые блоки."
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
            temperature=0.75,
            max_tokens=2200,
        )
        out = (resp.choices[0].message.content or "").strip()
        if len(out) < 80:
            return None
        return out[:6000]
    except Exception:
        logger.exception("wellness_coach_ai: generate failed")
        return None
