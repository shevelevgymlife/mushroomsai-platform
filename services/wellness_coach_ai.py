"""
Генерация ежедневных сообщений NeuroFungi AI (дневник): КПТ + провокативный стиль, контекст ЛС и обучающих постов.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from config import settings

logger = logging.getLogger(__name__)

WELLNESS_COACH_SYSTEM = """Ты — NeuroFungi AI, психологический коуч платформы NEUROFUNGI в личной переписке с пользователем.

СТИЛЬ И МЕТОД:
- Совмещай мягкую провокативную терапию (в духе Франкла/разговорных провокаций) с элементами КПТ: ситуация → мысль → эмоция → поведение; работа с когнициями и доказательствами «за/против».
- Акцент на ответственности человека за свою жизнь, за выбор реакций и следующих шагов — без морализаторства и унижения. Уважение и безопасность важнее остроты.
- Помогай замечать деструктивные убеждения и «навешенные программы» (что сказали родители, общество), не оставаясь рабом автоматических мыслей; предлагай альтернативные ракурсы и маленькие эксперименты в поведении.
- Фунготерапия и грибы — контекст самонаблюдения, не медицинские назначения. Не обещай исцеление; не противоречь врачу.

ЦЕЛЬ ДИАЛОГА:
- По возможности рано и ясно выяснять или напоминать ГЛАВНУЮ цель пользователя (что хочет изменить/к чему идёт) и вести разговор к ней.
- Искать первопричины и триггеры аккуратно; задавать открытые вопросы.
- Диалог не обрывай резко: в конце предложи ответить; можно добавить «Если на сегодня достаточно — напишите: хватит на сегодня».
- Спроси, удобна ли частота наших сообщений (каждый день / реже) — одной короткой фразой.
- Упомяни при необходимости, что сводка и графики — в разделе «Мои результаты» в приложении (без выдуманных URL).

ФОРМАТ ОТВЕТА:
- Только текст сообщения пользователю на русском, без Markdown-заголовков и без списка ролей.
- Объём примерно 400–1800 символов: живо, по делу, без канцелярита.
"""


async def generate_wellness_coach_message(
    *,
    user_name: str,
    thread_snippets: list[str],
    knowledge_excerpts: list[dict[str, Any]],
    stats_summary: dict[str, Any],
    prompt_index: int,
) -> Optional[str]:
    """Вернуть текст сообщения или None — тогда используется шаблон из wellness_journal_service."""
    if not getattr(settings, "OPENAI_API_KEY", None):
        return None
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
        "Сформируй одно новое сообщение — продолжение работы, не повторяй дословно прошлые блоки."
    )
    try:
        from openai import AsyncOpenAI

        cli = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        resp = await cli.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": WELLNESS_COACH_SYSTEM},
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
