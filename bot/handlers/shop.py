from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db.database import database
from db.models import products
from config import settings


async def shop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = await database.fetch_all(
        products.select().where(products.c.active == True).limit(8)
    )

    if not items:
        text = "Магазин загружается. Загляните позже или посетите сайт."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Перейти в магазин", url=f"{settings.SITE_URL}/shop")]
        ])
        await update.message.reply_text(text, reply_markup=keyboard)
        return

    text = "Каталог продуктов:\n\n"
    buttons = []
    for item in items:
        price = int(item["price"])
        text += f"{item['name']} — {price} руб.\n"
        buttons.append([
            InlineKeyboardButton(
                f"{item['name']}",
                url=f"{settings.SITE_URL}/shop/{item['id']}"
            )
        ])

    buttons.append([InlineKeyboardButton("Весь каталог", url=f"{settings.SITE_URL}/shop")])

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
    )
