"""
Бот: новые посты Telegram-канала → ai_training_posts (папка по умолчанию «Из канала с 26.03.26»)
+ опционально зеркало в ленту сообщества от выбранного users.id с меткой TG.

Переменные:
  TRAINING_BOT_TOKEN — тот же бот, что папки/посты; им при желании можно обойтись без отдельного CHANNEL_INGEST_BOT_TOKEN.
  CHANNEL_INGEST_BOT_TOKEN — опционально; если пусто или совпадает с TRAINING_BOT_TOKEN, приём канала вешается на бот обучения (один polling).
  CHANNEL_INGEST_ALLOWED_IDS — chat_id канала(ов).
  CHANNEL_INGEST_FOLDER (по умолчанию из config),
  CHANNEL_INGEST_COMMUNITY_USER_ID — id пользователя на сайте для публикации в ленте (0 = выкл).

История канала задним числом API не отдаёт — только новые посты после добавления бота админом.
"""
from __future__ import annotations

import logging
from typing import FrozenSet

from telegram import Message, Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from config import settings
from db.database import database
from db.models import ai_training_folders, ai_training_posts, community_posts, users
from services.channel_ingest_save_image import save_channel_ingest_image
from services.telegram_file_download import download_telegram_file_bytes
from services.training_post_title_ai import suggest_training_post_title

logger = logging.getLogger(__name__)


def effective_channel_ingest_bot_token() -> str:
    """Токен для getFile: отдельный CHANNEL_INGEST или тот же, что у бота обучения."""
    ch = (settings.CHANNEL_INGEST_BOT_TOKEN or "").strip()
    if ch:
        return ch
    return (settings.TRAINING_BOT_TOKEN or "").strip()


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


def _body_from_message(msg: Message) -> str:
    parts: list[str] = []
    if msg.text:
        t = msg.text.strip()
        if t:
            return t
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
        media.append("видеокружок")
    if msg.poll:
        media.append("опрос")
    if cap:
        parts.append(cap)
    if media:
        parts.append("[В канале: " + ", ".join(media) + "]")
    if parts:
        return "\n\n".join(parts)
    if media:
        return "[Пост из канала: " + ", ".join(media) + " — без текста]"
    return ""


def _largest_photo_file_id(msg: Message) -> str | None:
    if not msg.photo:
        return None
    try:
        best = max(msg.photo, key=lambda p: (p.width or 0) * (p.height or 0))
        return best.file_id
    except Exception:
        return None


async def _on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    post = update.channel_post
    if not post:
        return

    allowed: FrozenSet[int] = context.application.bot_data["channel_ingest_allowed"]
    chat_id = post.chat_id
    if chat_id not in allowed:
        logger.warning(
            "channel_ingest: игнор chat_id=%s (нет в CHANNEL_INGEST_ALLOWED_IDS).",
            chat_id,
        )
        return

    mid = post.message_id
    dup = await database.fetch_one(
        ai_training_posts.select()
        .where(ai_training_posts.c.ingest_tg_chat_id == chat_id)
        .where(ai_training_posts.c.ingest_tg_message_id == mid)
    )
    if dup:
        return

    token = effective_channel_ingest_bot_token()
    body = _body_from_message(post)
    image_url: str | None = None
    fid = _largest_photo_file_id(post)
    if fid:
        raw_img = await download_telegram_file_bytes(token, fid)
        if raw_img:
            image_url = save_channel_ingest_image(raw_img)

    if not body.strip() and not image_url:
        logger.info("channel_ingest: пропуск msg=%s — нет текста и не удалось сохранить фото", mid)
        return

    title_base = body if body.strip() else (image_url and "Пост с изображением из Telegram") or "Пост из Telegram"
    title = await suggest_training_post_title(title_base)

    folder = (settings.CHANNEL_INGEST_FOLDER or "Из канала с 26.03.26").strip() or "Из канала с 26.03.26"
    content = body.strip() if body.strip() else "[Изображение из Telegram-канала]"

    try:
        await database.execute(
            ai_training_posts.insert().values(
                title=title[:500],
                content=content[:100000],
                category="telegram_channel",
                folder=folder,
                image_url=image_url,
                ingest_tg_chat_id=chat_id,
                ingest_tg_message_id=mid,
            )
        )
        try:
            await database.execute(ai_training_folders.insert().values(name=folder))
        except Exception:
            pass
    except Exception:
        logger.exception("channel_ingest: ai_training_posts chat=%s msg=%s", chat_id, mid)
        return

    uid = int(settings.CHANNEL_INGEST_COMMUNITY_USER_ID or 0)
    if uid > 0:
        urow = await database.fetch_one(users.select().where(users.c.id == uid))
        if urow:
            try:
                tit = title[:200] if len(title) > 200 else title
                await database.execute(
                    community_posts.insert().values(
                        user_id=uid,
                        title=tit,
                        content=content[:100000],
                        image_url=image_url,
                        from_telegram=True,
                        folder_id=None,
                        approved=True,
                    )
                )
            except Exception:
                logger.exception("channel_ingest: community_posts chat=%s msg=%s", chat_id, mid)
        else:
            logger.warning("channel_ingest: CHANNEL_INGEST_COMMUNITY_USER_ID=%s не найден в users", uid)

    logger.info(
        "channel_ingest: OK training + optional feed chat=%s msg=%s folder=%r",
        chat_id,
        mid,
        folder,
    )


def register_channel_ingest_on_app(app: Application, allowed: FrozenSet[int]) -> None:
    """Один polling с ботом обучающих постов: тот же TRAINING_BOT_TOKEN."""
    if not allowed:
        return
    app.bot_data["channel_ingest_allowed"] = allowed
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, _on_channel_post))


def create_channel_ingest_bot() -> Application:
    token = effective_channel_ingest_bot_token()
    if not token:
        raise RuntimeError("Нет токена: задайте CHANNEL_INGEST_BOT_TOKEN или TRAINING_BOT_TOKEN")

    allowed = parse_allowed_channel_ids()
    if not allowed:
        raise RuntimeError("CHANNEL_INGEST_ALLOWED_IDS пуст — укажите id канала(ов), например -1001234567890")

    app = Application.builder().token(token).build()
    app.bot_data["channel_ingest_allowed"] = allowed
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, _on_channel_post))
    return app
