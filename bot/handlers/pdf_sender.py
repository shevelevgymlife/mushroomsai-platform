import io
from telegram import Update
from telegram.ext import ContextTypes
from services.pdf_service import generate_recipe_pdf
from bot.handlers.start import ensure_user_or_blocked_reply
from services.subscription_service import check_subscription


async def send_pdf_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE, recipe_text: str, title: str = "Персональный протокол"):
    user = await ensure_user_or_blocked_reply(update)
    if not user:
        return
    plan = await check_subscription(user["id"])

    if plan not in ("start", "pro"):
        await update.message.reply_text(
            "PDF-протоколы доступны по подписке Старт и Про.\n\n"
            "Напишите /tariffs для подключения."
        )
        return

    pdf_bytes = generate_recipe_pdf(
        title=title,
        content=recipe_text,
        user_name=user.get("name", "Пользователь"),
    )

    pdf_file = io.BytesIO(pdf_bytes)
    pdf_file.name = "protocol.pdf"

    await update.message.reply_document(
        document=pdf_file,
        filename="mushrooms_protocol.pdf",
        caption=f"Ваш персональный протокол: {title}",
    )
