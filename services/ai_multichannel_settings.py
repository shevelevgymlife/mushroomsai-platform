from __future__ import annotations

import json
import logging
from typing import Any

from db.database import database
from db.models import platform_settings

logger = logging.getLogger(__name__)

AI_MULTI_CHANNEL_SETTINGS_KEY = "ai_multichannel_prompt_settings"


def _default_dm_prompt() -> str:
    return (
        "Ты — NeuroFungi AI в личных сообщениях. "
        "Стиль: на пальцах, живо, без заумности, дружелюбно и с мягкой провокацией, "
        "как сильный стратегичный друг/подруга с критическим мышлением. "
        "Роль: фунготерапевт + психотерапевт + психолог в образовательном формате.\n\n"
        "Строго:\n"
        "- Не пиши штампами дешевого мотивационного копирайта.\n"
        "- Не используй пафосные конструкции вроде «это не просто ..., это трагедия».\n"
        "- Не запугивай и не давай медицинские назначения.\n"
        "- Пиши по-русски, короткими ясными абзацами, без воды и канцелярита.\n"
        "- Не выдумывай факты: для контента про грибы, дозы, схемы и рекомендации опирайся только на обучающие посты."
    )


def _default_post_prompt() -> str:
    return (
        "Стиль постов в сообществе NEUROFUNGI:\n"
        "- Легкий, умный, разговорный тон, без пафоса и без «типичного ChatGPT».\n"
        "- Текст понятный человеку, без высокомерия и сложных терминов без пояснения.\n"
        "- Можно мягкий юмор и фристайл-подачу, но без клоунады.\n"
        "- Никаких манипулятивных драматизаций и шаблонов.\n"
        "- Только проверяемые факты из обучающих постов; если данных нет — честно скажи, что в базе этого нет.\n"
        "- Без медицинских обещаний и назначений."
    )


def _default_comment_prompt() -> str:
    return (
        "Стиль комментариев в ленте:\n"
        "- Коротко, по делу, по-дружески.\n"
        "- Умный, понимающий и провокативный, но уважительный тон.\n"
        "- Без токсичности, без осуждения и без пафосных клише.\n"
        "- Если нужен факт про грибы/дозы/схемы — используй только обучающие посты.\n"
        "- Если фактов в обучающих постах нет, прямо это обозначай."
    )


def _default_dm_algorithm_prompt() -> str:
    return (
        "Алгоритм личных сообщений:\n"
        "1) В каждом новом сообщении задавай новый вопрос, не повторяй один и тот же шаблон.\n"
        "2) Чередуй подачу: где-то с мягким юмором, где-то свободный фристайл, где-то строго и коротко.\n"
        "3) Уточняй контекст для статистики: напомни, что ответ можно подтвердить для записи в базу форматом «да/нет», "
        "чтобы не перепутать вопрос.\n"
        "4) Сохраняй профессиональную роль: фунготерапевт + психотерапевт + психолог.\n"
        "5) Не уходи в длинные лекции; лучше 1 сильный вопрос + 1 уточнение.\n"
        "6) Когда не хватает данных в обучающих постах — не додумывай, а запрашивай уточнение у пользователя."
    )


def default_ai_multichannel_settings() -> dict[str, Any]:
    return {
        "dm_prompt": _default_dm_prompt(),
        "post_prompt": _default_post_prompt(),
        "comment_prompt": _default_comment_prompt(),
        "dm_algorithm_prompt": _default_dm_algorithm_prompt(),
        "dm_interval_enabled": True,
        "dm_interval_minutes": 60,
    }


def _normalize_text(v: Any, fallback: str, max_len: int = 24000) -> str:
    s = (str(v or "")).strip()
    if not s:
        return fallback
    return s[:max_len]


def _normalize_minutes(v: Any, fallback: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = fallback
    return max(15, min(1440, n))


def _normalize_enabled(v: Any, fallback: bool) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return fallback
    return str(v).strip().lower() in ("1", "true", "on", "yes")


def normalize_ai_multichannel_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    base = default_ai_multichannel_settings()
    src = raw if isinstance(raw, dict) else {}
    return {
        "dm_prompt": _normalize_text(src.get("dm_prompt"), base["dm_prompt"]),
        "post_prompt": _normalize_text(src.get("post_prompt"), base["post_prompt"]),
        "comment_prompt": _normalize_text(src.get("comment_prompt"), base["comment_prompt"]),
        "dm_algorithm_prompt": _normalize_text(
            src.get("dm_algorithm_prompt"), base["dm_algorithm_prompt"]
        ),
        "dm_interval_enabled": _normalize_enabled(
            src.get("dm_interval_enabled"), bool(base["dm_interval_enabled"])
        ),
        "dm_interval_minutes": _normalize_minutes(
            src.get("dm_interval_minutes"), int(base["dm_interval_minutes"])
        ),
    }


async def get_ai_multichannel_settings() -> dict[str, Any]:
    try:
        row = await database.fetch_one(
            platform_settings.select().where(platform_settings.c.key == AI_MULTI_CHANNEL_SETTINGS_KEY)
        )
        if not row or not row.get("value"):
            base = default_ai_multichannel_settings()
        else:
            raw = json.loads(row["value"])
            base = normalize_ai_multichannel_settings(raw if isinstance(raw, dict) else {})
        try:
            from services.ai_behavior_config import overlay_multichannel_from_behavior

            base = await overlay_multichannel_from_behavior(base)
        except Exception:
            logger.debug("overlay_multichannel_from_behavior skipped", exc_info=True)
        return normalize_ai_multichannel_settings(base)
    except Exception:
        logger.debug("get_ai_multichannel_settings failed", exc_info=True)
        return default_ai_multichannel_settings()


async def save_ai_multichannel_settings(data: dict[str, Any]) -> None:
    payload = normalize_ai_multichannel_settings(data)
    raw = json.dumps(payload, ensure_ascii=False)
    exists = await database.fetch_one(
        platform_settings.select().where(platform_settings.c.key == AI_MULTI_CHANNEL_SETTINGS_KEY)
    )
    if exists:
        await database.execute(
            platform_settings.update()
            .where(platform_settings.c.key == AI_MULTI_CHANNEL_SETTINGS_KEY)
            .values(value=raw)
        )
    else:
        await database.execute(
            platform_settings.insert().values(key=AI_MULTI_CHANNEL_SETTINGS_KEY, value=raw)
        )
