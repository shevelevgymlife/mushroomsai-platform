"""
Бот забирает новые посты из Telegram-канала и пишет их в ai_training_posts
(тот же поток, что бот обучения и AI на сайте через get_system_prompt).

Настройка:
  1. @BotFather → новый бот → CHANNEL_INGEST_BOT_TOKEN в Environment.
  2. Добавить бота в канал администратором (достаточно читать сообщения / без особых прав).
  3. CHANNEL_INGEST_ALLOWED_IDS — chat_id канала(ов), через запятую (обычно -100…).
     Узнать id: @userinfobot, пересланное сообщение из канала, или логи бота при первом посте.
  4. Опционально CHANNEL_INGEST_FOLDER (по умолчанию «Из канала»).

Ограничение Telegram Bot API: историю канала «задним числом» бот не получает —
только посты, опубликованные после того, как бот стал админом канала.
Для старых постов — ручной перенос или дубли через бот обучения.
"""
from __future__ import annotations

import logging
from typing import FrozenSet

from telegram import Message, Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from config import settings
from db.database import database
from db.models import ai_training_folders, ai_training_posts

logger = logging.getLogger(__name__)


def parse_allowed_channel_ids() -> FrozenSet[int]:
    raw = (settings.CHANNEL_INGEST_ALLOWED_IDS or "").strip()
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            logger.warning("channel_ingest: пропускаю неверный id в CHANNEL_INGEST_ALLOWED_IDS: %r", part)
    return frozenset(out)


def _message_to_training_fields(msg: Message) -> tuple[str, str] | None:
    """(title, content) для вставки или None — пропустить."""
    if msg.text:
        raw = msg.text.strip()
        if not raw:
            return None
        first = (raw.split("\n", 1)[0] or "").strip() or "Пост из канала"
        return first[:500], raw[:100000]

    cap = (msg.caption or "").strip()
    media: list[str] = []
    if msg.photo:
        media.append("фото")
    if msg.video:
        media.append("видео")
    if msg.document:
        media.append("файл")
    if msg.audio:
        media.append("аудио")
    if msg.voice:
        media.append("голосовое")
    if msg.video_note:
        media.append("видеосообщение")
    if msg.poll:
        media.append("опрос")

    if cap:
        body = cap
        if media:
            body += "\n\n[В канале: " + ", ".join(media) + "]"
        first = (body.split("\n", 1)[0] or "").strip() or "Пост из канала"
        return first[:500], body[:100000]

    if media:
        body = (
            "[Пост только с медиа без подписи: "
            + ", ".join(media)
            + " — добавьте подпись к посту в канале, чтобы текст попал в базу для AI.]"
        )
        return body[:500], body[:100000]

    return None


async def _on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    post = update.channel_post
    if not post:
        return

    allowed: FrozenSet[int] = context.application.bot_data["channel_ingest_allowed"]
    chat_id = post.chat_id
    if chat_id not in allowed:
        logger.warning(
            "channel_ingest: игнор chat_id=%s (нет в CHANNEL_INGEST_ALLOWED_IDS). Добавьте этот id в Environment.",
            chat_id,
        )
        return

    fields = _message_to_training_fields(post)
    if not fields:
        return

    title, content = fields
    mid = post.message_id

    dup = await database.fetch_one(
        ai_training_posts.select()
        .where(ai_training_posts.c.ingest_tg_chat_id == chat_id)
        .where(ai_training_posts.c.ingest_tg_message_id == mid)
    )
    if dup:
        return

    folder = (settings.CHANNEL_INGEST_FOLDER or "Из канала").strip() or "Из канала"

    try:
        await database.execute(
            ai_training_posts.insert().values(
                title=title,
                content=content,
                category="telegram_channel",
                folder=folder,
                ingest_tg_chat_id=chat_id,
                ingest_tg_message_id=mid,
            )
        )
        try:
            await database.execute(ai_training_folders.insert().values(name=folder))
        except Exception:
            pass
    except Exception:
        logger.exception("channel_ingest: не удалось сохранить пост chat=%s msg=%s", chat_id, mid)
        return

    logger.info("channel_ingest: сохранён пост в AI-обучение chat=%s msg=%s folder=%r", chat_id, mid, folder)


def create_channel_ingest_bot() -> Application:
    token = (settings.CHANNEL_INGEST_BOT_TOKEN or "").strip()
    if not token:
        raise RuntimeError("CHANNEL_INGEST_BOT_TOKEN пуст")

    allowed = parse_allowed_channel_ids()
    if not allowed:
        raise RuntimeError("CHANNEL_INGEST_ALLOWED_IDS пуст — укажите id канала(ов), например -1001234567890")

    app = Application.builder().token(token).build()
    app.bot_data["channel_ingest_allowed"] = allowed
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, _on_channel_post))
    return app
