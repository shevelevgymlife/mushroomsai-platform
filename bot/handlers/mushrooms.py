from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot.handlers.start import ensure_user
from services.subscription_service import check_subscription
from config import settings

MUSHROOMS_BASE = """База знаний о функциональных грибах:

Рейши (Ganoderma lucidum)
Иммунитет, стресс, сон. Адаптоген. Курс 2-3 мес.

Чага (Inonotus obliquus)
Антиоксидант, ЖКТ, онкопрофилактика. Курс 3 мес.

Кордицепс (Cordyceps sinensis)
Энергия, выносливость, либидо. Курс 2 мес.

Лев-грива (Hericium erinaceus)
Нейропротектор, память, концентрация. Курс 3 мес.

Шиитаке (Lentinus edodes)
Иммунитет, холестерин, печень. Курс 2 мес.

Трутовик (Trametes versicolor)
Онкопрофилактика, иммунитет. Курс 3 мес.

Майтаке (Grifola frondosa)
Сахар в крови, вес, иммунитет. Курс 2-3 мес.

Мухомор (Amanita muscaria)
Микродозинг, ЦНС, психологические протоколы.
Требует индивидуального подхода."""


async def mushrooms_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user(update.effective_user)
    plan = await check_subscription(user["id"])

    keyboard_buttons = [
        [InlineKeyboardButton("Задать вопрос об этом грибе", callback_data="consult_now")],
    ]

    if plan == "pro":
        keyboard_buttons.insert(0, [
            InlineKeyboardButton("Глубокий разбор (Pro)", callback_data="mushroom_deep")
        ])
    else:
        keyboard_buttons.append([
            InlineKeyboardButton("Глубокий разбор — только Pro", callback_data="show_tariffs")
        ])

    await update.message.reply_text(
        MUSHROOMS_BASE,
        reply_markup=InlineKeyboardMarkup(keyboard_buttons),
    )


async def mushroom_deep_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = await ensure_user(query.from_user)
    plan = await check_subscription(user["id"])

    if plan != "pro":
        await query.message.reply_text(
            "Раздел доступен только по подписке Про.\n\n"
            "Напишите /tariffs для подключения."
        )
        return

    deep_text = (
        "Глубокий разбор — подраздел Pro\n\n"
        "Доступны детальные протоколы по каждому грибу, "
        "взаимодействия с лекарствами, клинические исследования.\n\n"
        "Напишите название гриба для детального разбора."
    )
    await query.message.reply_text(deep_text)
