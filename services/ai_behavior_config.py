"""
Поведение AI по сценариям: отдельные промпты и флаги для кабинета, ЛС, ленты, дневника и т.д.
Глобально + переопределение на пользователя (deep merge).
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import datetime
from typing import Any

import sqlalchemy.exc

from db.database import database
from db.models import ai_behavior_admin_feedback, ai_behavior_global, ai_behavior_user_overrides

logger = logging.getLogger(__name__)

# Ключ сценария → заголовок и описание для админки
AI_BEHAVIOR_ASPECTS: list[tuple[str, str, str]] = [
    (
        "cabinet_ai_chat",
        "Кабинет: AI-чат (веб)",
        "Ответы по кнопке AI в переписке и основному чату в кабинете (/api/chat, авторизованный пользователь).",
    ),
    (
        "telegram_dm_ai",
        "Telegram: вопрос к AI",
        "Режим «Задать вопрос AI» в боте (личка, привязанный или гостевой сценарий с лимитом).",
    ),
    (
        "guest_ai_preview",
        "Гость без регистрации",
        "Короткий предпросмотр AI по cookie-сессии на сайте (до лимита сообщений).",
    ),
    (
        "free_tier_cabinet",
        "Бесплатный тариф (доп. инструкции)",
        "Дополнительный слой поверх кабинетного чата для пользователей на free (после основного ответа может добавляться текст лимита отдельно).",
    ),
    (
        "dm_interval_broadcast",
        "ЛС по таймеру + алгоритм",
        "Периодические сообщения в личку (интервал), промпт стиля ЛС и алгоритм вопросов/«да-нет» для статистики. Перекрывает блок «Промпты каналов» если поля заполнены здесь.",
    ),
    (
        "community_post",
        "Сообщество: посты AI",
        "Генерация и стиль постов в ленте от имени AI.",
    ),
    (
        "community_comment",
        "Сообщество: комментарии",
        "Стиль комментариев под постами.",
    ),
    (
        "wellness_daily_coach",
        "Дневник: ежедневные сообщения",
        "Коуч в дневнике терапии (ежедневные тексты, КПТ, контекст ЛС).",
    ),
    (
        "wellness_results_personal",
        "Мои результаты: персональные подсказки",
        "Пояснения и тексты в персональной сводке пользователя.",
    ),
    (
        "wellness_results_aggregate",
        "Мои результаты: сводные инсайты",
        "Агрегированные формулировки по пользователю / периоду.",
    ),
    (
        "stats_dm_collection",
        "Сбор статистики из ЛС",
        "Как AI интерпретирует и нормализует ответы пользователя для аналитики и привычек (инструкции).",
    ),
    (
        "knowledge_mushroom_memo",
        "Памятки и знания по грибам",
        "Индексация, напоминания, справочные формулировки (если отдельный поток использует эти инструкции).",
    ),
    (
        "new_user_referral_outreach",
        "Новые пользователи: магазин и приложение",
        "Сценарии со ссылками на магазин/регистрацию по концепции рефералок (когда AI или бот вводит человека в воронку).",
    ),
    (
        "telegram_group_widget",
        "Telegram-группы: виджет NeuroFungi AI",
        "Контекст для пользователей, перешедших из закреплённого виджета в группах (косвенно через общий стиль; основной текст виджета в админке групп).",
    ),
    (
        "linked_chats_messaging",
        "Привязанные чаты (концепт)",
        "Единый сценарий для чатов, привязанных к аккаунту/платформе (расширяемо под будущие интеграции).",
    ),
    (
        "admin_ai_test",
        "Тест из админки /admin/ai",
        "Дополнительные инструкции только для кнопки «Проверить» на странице управления AI.",
    ),
]

ASPECT_KEYS = frozenset(k for k, _, _ in AI_BEHAVIOR_ASPECTS)

# Если в админке поле «Кто ты» пустое — подмешиваем дружелюбный дефолт (можно переопределить в AI → сценарии).
TELEGRAM_DM_DEFAULT_ROLE_PREAMBLE = (
    "Ты — NeuroFungi AI, девушка: общаешься в личке как близкая подруга — тепло, на «ты», по-человечески, без канцелярита. "
    "Помни контекст переписки и отвечай с учётом того, о чём вы уже говорили. "
    "По грибам и дозам опирайся только на обучающие материалы в системном промпте; не выдумывай факты. "
    "Не ставь диагнозы; фунготерапия — в образовательном ключе."
)

_DEFAULT_TEMPLATE: dict[str, Any] = {
    "enabled": True,
    "refuse_conversation": False,
    "refusal_message": "Ответы AI по этому сценарию временно отключены администратором.",
    "role_preamble": "",
    "prompt_extra": "",
    # inherit = как в общих настройках AI (обучающие посты подмешиваются); minimal_context = только базовый системный промпт + доп. текст ниже
    "knowledge_mode": "inherit",
    "collect_client_stats": False,
    "tone_preset": "friendly",
    "tone_custom_notes": "",
    "use_subscription_marketing_copy": True,
    "link_policy": "platform_default",
    "weekly_wellness_pdf": False,
    "allow_wellness_pdf_download": True,
    "show_stats_calendar": True,
    "show_stats_memo_cards": True,
    "show_stats_rollups": True,
    "dm_prompt": "",
    "post_prompt": "",
    "comment_prompt": "",
    "dm_algorithm_prompt": "",
    "dm_interval_enabled": True,
    "dm_interval_minutes": 60,
}


def _default_skeleton() -> dict[str, Any]:
    return deepcopy(_DEFAULT_TEMPLATE)


def _parse_json_obj(raw: str | None) -> dict[str, Any]:
    if not raw or not str(raw).strip():
        return {}
    try:
        o = json.loads(raw)
        return o if isinstance(o, dict) else {}
    except Exception:
        return {}


def normalize_behavior_config(src: dict[str, Any] | None) -> dict[str, Any]:
    base = _default_skeleton()
    if not src or not isinstance(src, dict):
        return base
    out = deepcopy(base)
    for k, v in src.items():
        if k not in out:
            continue
        if k in ("enabled", "refuse_conversation", "collect_client_stats", "use_subscription_marketing_copy"):
            out[k] = bool(v) if v is not None else out[k]
        elif k in ("weekly_wellness_pdf", "allow_wellness_pdf_download", "show_stats_calendar", "show_stats_memo_cards", "show_stats_rollups"):
            out[k] = bool(v) if v is not None else out[k]
        elif k == "dm_interval_enabled":
            out[k] = bool(v) if v is not None else out[k]
        elif k == "dm_interval_minutes":
            try:
                out[k] = max(15, min(1440, int(v)))
            except (TypeError, ValueError):
                pass
        elif k in (
            "role_preamble",
            "prompt_extra",
            "tone_custom_notes",
            "refusal_message",
            "dm_prompt",
            "post_prompt",
            "comment_prompt",
            "dm_algorithm_prompt",
        ):
            out[k] = (str(v) if v is not None else "")[:24000]
        elif k in ("knowledge_mode", "tone_preset", "link_policy"):
            s = (str(v) if v is not None else "").strip()
            if s:
                out[k] = s
    if out["knowledge_mode"] not in ("inherit", "minimal_context"):
        out["knowledge_mode"] = "inherit"
    if out["tone_preset"] not in ("friendly", "neutral", "formal", "custom"):
        out["tone_preset"] = "friendly"
    if out["link_policy"] not in ("platform_default", "referral_concept", "no_shop_links"):
        out["link_policy"] = "platform_default"
    return out


def deep_merge_behavior(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge_behavior(out[k], v)
        else:
            out[k] = v
    return out


async def get_global_behavior_row(aspect_key: str) -> dict[str, Any]:
    if aspect_key not in ASPECT_KEYS:
        return normalize_behavior_config({})
    try:
        row = await database.fetch_one(
            ai_behavior_global.select().where(ai_behavior_global.c.aspect_key == aspect_key)
        )
    except (sqlalchemy.exc.ProgrammingError, sqlalchemy.exc.OperationalError) as e:
        logger.debug("ai_behavior_global read skipped: %s", e)
        return normalize_behavior_config({})
    if not row:
        return normalize_behavior_config({})
    return normalize_behavior_config(_parse_json_obj(row.get("config_json")))


async def get_user_behavior_override(aspect_key: str, user_id: int) -> dict[str, Any]:
    try:
        row = await database.fetch_one(
            ai_behavior_user_overrides.select()
            .where(
                ai_behavior_user_overrides.c.user_id == int(user_id),
                ai_behavior_user_overrides.c.aspect_key == aspect_key,
            )
        )
    except (sqlalchemy.exc.ProgrammingError, sqlalchemy.exc.OperationalError) as e:
        logger.debug("ai_behavior_user_overrides read skipped: %s", e)
        return {}
    if not row:
        return {}
    return normalize_behavior_config(_parse_json_obj(row.get("config_json")))


async def get_merged_behavior_config(aspect_key: str, user_id: int | None) -> dict[str, Any]:
    g = await get_global_behavior_row(aspect_key)
    if user_id is None:
        return g
    o = await get_user_behavior_override(aspect_key, int(user_id))
    if not o:
        return g
    return normalize_behavior_config(deep_merge_behavior(g, o))


async def save_global_behavior(aspect_key: str, cfg: dict[str, Any]) -> None:
    if aspect_key not in ASPECT_KEYS:
        raise ValueError("unknown aspect")
    norm = normalize_behavior_config(cfg)
    raw = json.dumps(norm, ensure_ascii=False)
    try:
        exists = await database.fetch_one(
            ai_behavior_global.select().where(ai_behavior_global.c.aspect_key == aspect_key)
        )
    except (sqlalchemy.exc.ProgrammingError, sqlalchemy.exc.OperationalError) as e:
        raise RuntimeError("Таблица ai_behavior_global ещё не создана (нужен деплой / миграция).") from e
    if exists:
        await database.execute(
            ai_behavior_global.update()
            .where(ai_behavior_global.c.aspect_key == aspect_key)
            .values(config_json=raw, updated_at=datetime.utcnow())
        )
    else:
        await database.execute(
            ai_behavior_global.insert().values(aspect_key=aspect_key, config_json=raw, updated_at=datetime.utcnow())
        )


async def save_user_behavior_override(user_id: int, aspect_key: str, cfg: dict[str, Any]) -> None:
    if aspect_key not in ASPECT_KEYS:
        raise ValueError("unknown aspect")
    norm = normalize_behavior_config(cfg)
    raw = json.dumps(norm, ensure_ascii=False)
    exists = await database.fetch_one(
        ai_behavior_user_overrides.select()
        .where(
            ai_behavior_user_overrides.c.user_id == int(user_id),
            ai_behavior_user_overrides.c.aspect_key == aspect_key,
        )
    )
    if exists:
        await database.execute(
            ai_behavior_user_overrides.update()
            .where(
                ai_behavior_user_overrides.c.user_id == int(user_id),
                ai_behavior_user_overrides.c.aspect_key == aspect_key,
            )
            .values(config_json=raw, updated_at=datetime.utcnow())
        )
    else:
        await database.execute(
            ai_behavior_user_overrides.insert().values(
                user_id=int(user_id),
                aspect_key=aspect_key,
                config_json=raw,
                updated_at=datetime.utcnow(),
            )
        )


async def delete_user_behavior_override(user_id: int, aspect_key: str) -> None:
    await database.execute(
        ai_behavior_user_overrides.delete().where(
            ai_behavior_user_overrides.c.user_id == int(user_id),
            ai_behavior_user_overrides.c.aspect_key == aspect_key,
        )
    )


def build_behavior_system_addon(cfg: dict[str, Any]) -> str:
    """Текст, добавляемый к системному промпту OpenAI."""
    if not cfg.get("enabled", True):
        return ""
    parts: list[str] = []
    rp = (cfg.get("role_preamble") or "").strip()
    if rp:
        parts.append("РОЛЬ И ИДЕНТИЧНОСТЬ В ЭТОМ СЦЕНАРИИ:\n" + rp)
    pe = (cfg.get("prompt_extra") or "").strip()
    if pe:
        parts.append("ДОПОЛНИТЕЛЬНЫЕ ИНСТРУКЦИИ ДЛЯ ЭТОГО СЦЕНАРИЯ:\n" + pe)
    tone = cfg.get("tone_preset") or "friendly"
    tn = (cfg.get("tone_custom_notes") or "").strip()
    tone_map = {
        "friendly": "Тон: дружелюбный, на «ты» или нейтрально-теплый, без канцелярита.",
        "neutral": "Тон: нейтральный, деловой, сдержанный.",
        "formal": "Тон: формальный, уважительный, выдержанный.",
        "custom": "Тон: задайте сами в поле заметок ниже.",
    }
    tl = tone_map.get(tone, tone_map["friendly"])
    if tone == "custom" and tn:
        tl += " " + tn
    elif tone != "custom":
        if tn:
            tl += " " + tn
    parts.append(tl)
    km = cfg.get("knowledge_mode") or "inherit"
    if km == "minimal_context":
        parts.append(
            "ИСТОЧНИК ФАКТОВ: в этом сценарии не опирайся на длинный блок обучающих постов в системном сообщении "
            "(он может быть сокращён). Не выдумывай дозы и протоколы; при нехватке данных скажи об этом."
        )
    else:
        parts.append(
            "ИСТОЧНИК ФАКТОВ: соблюдай глобальные правила платформы и фрагменты обучающих постов из системного промпта."
        )
    if cfg.get("collect_client_stats"):
        parts.append(
            "СТАТИСТИКА: по возможности уточняй формулировки так, чтобы ответы пользователя можно было однозначно учитывать "
            "в аналитике (короткие подтверждения да/нет там, где уместно)."
        )
    else:
        parts.append("СТАТИСТИКА: не настаивай на сборе структурированных ответов для аналитики, если пользователь не готов.")
    if cfg.get("use_subscription_marketing_copy", True):
        parts.append(
            "ПОДПИСКИ И ТАРИФЫ: можно мягко напоминать о тарифах и возможностях платформы в духе официальной концепции NEUROFUNGI."
        )
    else:
        parts.append("ПОДПИСКИ: не продвигай платные тарифы активно; только по прямому вопросу пользователя.")
    lp = cfg.get("link_policy") or "platform_default"
    if lp == "no_shop_links":
        parts.append("ССЫЛКИ: не предлагай ссылки на магазин или регистрацию, пока пользователь сам не попросит.")
    elif lp == "referral_concept":
        parts.append(
            "ССЫЛКИ: при упоминании покупок ориентируйся на реферальную концепцию платформы (пригласивший, партнёрский магазин, единая витрина)."
        )
    else:
        parts.append("ССЫЛКИ: используй те URL магазинов и логику, которые уже переданы в системном промпте для этого пользователя.")
    vis = []
    if not cfg.get("show_stats_calendar", True):
        vis.append("не акцентируй календарь/дневную сетку")
    if not cfg.get("show_stats_memo_cards", True):
        vis.append("не настаивай на памятках по связкам")
    if not cfg.get("show_stats_rollups", True):
        vis.append("не расписывай сводные агрегаты, если пользователь не спрашивает")
    if vis:
        parts.append("ИНТЕРФЕЙС СТАТИСТИКИ: " + "; ".join(vis) + ".")
    pdf_on = cfg.get("weekly_wellness_pdf")
    pdf_dl = cfg.get("allow_wellness_pdf_download", True)
    if pdf_on:
        parts.append(
            "PDF: раз в неделю допускается напоминание о выгрузке сводки дневника в PDF."
            + (" Пользователь может скачать PDF." if pdf_dl else " Скачивание PDF без явного разрешения админа не предлагай.")
        )
    return "\n\n".join(parts)


async def build_addon_for_aspects(aspect_keys: list[str], user_id: int | None) -> tuple[str, bool, str | None, bool]:
    """
    Склеивает аддоны для нескольких сценариев.
    Возвращает (текст, skip_training, refusal_message_or_none, any_disabled).
    """
    texts: list[str] = []
    skip_training = False
    refusal: str | None = None
    any_disabled = False
    for key in aspect_keys:
        cfg = await get_merged_behavior_config(key, user_id)
        if key == "telegram_dm_ai" and not (cfg.get("role_preamble") or "").strip():
            cfg = normalize_behavior_config(deep_merge_behavior(cfg, {"role_preamble": TELEGRAM_DM_DEFAULT_ROLE_PREAMBLE}))
        if not cfg.get("enabled", True):
            any_disabled = True
            continue
        if cfg.get("refuse_conversation"):
            refusal = (cfg.get("refusal_message") or "Сценарий отключён.").strip()
        if cfg.get("knowledge_mode") == "minimal_context":
            skip_training = True
        block = build_behavior_system_addon(cfg)
        if block.strip():
            texts.append(block)
    return "\n\n---\n\n".join(texts), skip_training, refusal, any_disabled


async def _fetch_global_json_if_saved(aspect_key: str) -> dict[str, Any] | None:
    try:
        row = await database.fetch_one(
            ai_behavior_global.select().where(ai_behavior_global.c.aspect_key == aspect_key)
        )
    except (sqlalchemy.exc.ProgrammingError, sqlalchemy.exc.OperationalError):
        return None
    if not row:
        return None
    return normalize_behavior_config(_parse_json_obj(row.get("config_json")))


async def overlay_multichannel_from_behavior(base: dict[str, Any]) -> dict[str, Any]:
    """Подмена полей multichannel только если для сценария есть сохранённая строка в БД."""
    out = dict(base)
    try:
        dm = await _fetch_global_json_if_saved("dm_interval_broadcast")
        if dm is not None:
            if (dm.get("dm_prompt") or "").strip():
                out["dm_prompt"] = dm["dm_prompt"].strip()
            if (dm.get("dm_algorithm_prompt") or "").strip():
                out["dm_algorithm_prompt"] = dm["dm_algorithm_prompt"].strip()
            out["dm_interval_enabled"] = bool(dm.get("dm_interval_enabled", True))
            try:
                out["dm_interval_minutes"] = max(15, min(1440, int(dm.get("dm_interval_minutes") or 60)))
            except (TypeError, ValueError):
                pass
        po = await _fetch_global_json_if_saved("community_post")
        if po is not None and (po.get("post_prompt") or "").strip():
            out["post_prompt"] = po["post_prompt"].strip()
        co = await _fetch_global_json_if_saved("community_comment")
        if co is not None and (co.get("comment_prompt") or "").strip():
            out["comment_prompt"] = co["comment_prompt"].strip()
    except Exception:
        logger.debug("overlay_multichannel_from_behavior", exc_info=True)
    return out


async def append_wellness_aspect_addons(base_parts: list[str]) -> None:
    """Добавляет в список частей промпта блоки из сценариев дневника."""
    for aspect in ("wellness_daily_coach", "wellness_results_personal", "stats_dm_collection"):
        cfg = await get_merged_behavior_config(aspect, None)
        if not cfg.get("enabled", True):
            continue
        block = build_behavior_system_addon(cfg)
        if block.strip():
            base_parts.append(f"[{aspect}]\n" + block)
        pe = (cfg.get("prompt_extra") or "").strip()
        if pe and f"[{aspect}]" not in block:
            base_parts.append(f"[{aspect} — доп. текст]\n" + pe)


async def save_admin_feedback(
    aspect_key: str,
    question: str,
    answer: str,
    liked: bool,
    admin_user_id: int | None,
) -> None:
    try:
        await database.execute(
            ai_behavior_admin_feedback.insert().values(
                aspect_key=aspect_key[:64],
                question=question[:12000],
                answer=answer[:24000],
                liked=bool(liked),
                admin_user_id=admin_user_id,
                created_at=datetime.utcnow(),
            )
        )
    except (sqlalchemy.exc.ProgrammingError, sqlalchemy.exc.OperationalError):
        logger.debug("save_admin_feedback: table missing")


async def list_recent_feedback(aspect_key: str | None, limit: int = 15) -> list[dict[str, Any]]:
    try:
        if aspect_key:
            rows = await database.fetch_all(
                ai_behavior_admin_feedback.select()
                .where(ai_behavior_admin_feedback.c.aspect_key == aspect_key)
                .order_by(ai_behavior_admin_feedback.c.created_at.desc())
                .limit(limit)
            )
        else:
            rows = await database.fetch_all(
                ai_behavior_admin_feedback.select()
                .order_by(ai_behavior_admin_feedback.c.created_at.desc())
                .limit(limit)
            )
    except (sqlalchemy.exc.ProgrammingError, sqlalchemy.exc.OperationalError):
        return []
    return [dict(r) for r in rows]
