from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db.database import database
from db.models import users

LANGUAGES = {
    "ru": "Русский",
    "en": "English",
    "de": "Deutsch",
    "fr": "Francais",
    "es": "Espanol",
    "zh": "Zhongwen",
    "ja": "Nihongo",
    "ar": "Arabic",
    "hi": "Hindi",
    "pt": "Portugues",
}


async def show_language_selector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    row = []
    for i, (code, name) in enumerate(LANGUAGES.items()):
        row.append(InlineKeyboardButton(name, callback_data=f"lang_{code}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    await update.message.reply_text(
        "Выберите язык / Select language:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    lang = query.data.replace("lang_", "")
    tg_id = query.from_user.id

    await database.execute(
        users.update().where(users.c.tg_id == tg_id).values(language=lang)
    )

    lang_name = LANGUAGES.get(lang, lang)
    await query.edit_message_text(f"Язык установлен: {lang_name}")
