"""
Каталог разделов админки: группы (как в «Настройках» iOS), подписи и тексты справки (?).
Логика приложения не меняется — только навигация и подсказки в UI.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

# Порядок секций в списке «как Приложения» iOS: кириллица, латиница, # в конце
_RU_LETTER_ORDER = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"

# Категории: id, title, subtitle, icon
ADMIN_UI_CATEGORIES: list[dict[str, str]] = [
    {"id": "overview", "title": "Обзор", "subtitle": "Сводки и быстрый доступ", "icon": "📊"},
    {"id": "users", "title": "Пользователи и коммуникации", "subtitle": "Учётные записи, обратная связь, рассылки", "icon": "👤"},
    {"id": "commerce", "title": "Монетизация", "subtitle": "Оплата, магазин, тарифы", "icon": "💳"},
    {"id": "content", "title": "Контент и сайт", "subtitle": "Главная, кабинет, тексты, знания", "icon": "🌐"},
    {"id": "community", "title": "Сообщество", "subtitle": "Лента, группы, звонки, AI в ленте", "icon": "💬"},
    {"id": "ai", "title": "AI и обучение", "subtitle": "Промпты, посты для модели", "icon": "🧠"},
    {"id": "wellness", "title": "Дневник и аналитика терапии", "subtitle": "Wellness, схемы, отчёты", "icon": "🌿"},
    {"id": "media", "title": "Аудио", "subtitle": "Музыка и радио", "icon": "🎵"},
    {"id": "system", "title": "Система и качество", "subtitle": "Обратная связь по продукту, боты", "icon": "⚙️"},
]

# Элементы: id, category_id, label, href, perm, subtitle, help (HTML фрагмент)
_ADMIN_ITEMS_RAW: list[dict[str, Any]] = [
    {
        "id": "dashboard",
        "category_id": "overview",
        "label": "Панель",
        "href": "/admin",
        "perm": "can_dashboard",
        "subtitle": "Счётчики, последние сообщения и обращения",
        "help": (
            "<p><b>Что это:</b> стартовая страница админки с краткой статистикой (пользователи, сообщения, подписки) "
            "и лентой последних действий.</p>"
            "<p><b>На что влияет:</b> только просмотр; настройки здесь не хранятся.</p>"
            "<p><b>Сохранение:</b> не требуется.</p>"
        ),
    },
    {
        "id": "wellness_overview",
        "category_id": "overview",
        "label": "Сводка дневника (платформа)",
        "href": "/admin/wellness-journal/overview",
        "perm": "can_users",
        "subtitle": "Агрегаты wellness, PDF, запуск пайплайна",
        "help": (
            "<p><b>Что это:</b> обзор метрик дневника терапии по платформе, экспорт, служебные действия аналитики.</p>"
            "<p><b>На что влияет:</b> отчёты и фоновые пересчёты; не меняет тарифы и оплату.</p>"
            "<p><b>Сохранение:</b> по кнопкам на самой странице (каждое действие отдельно).</p>"
        ),
    },
    {
        "id": "users",
        "category_id": "users",
        "label": "Пользователи",
        "href": "/admin/users",
        "perm": "can_users",
        "subtitle": "Поиск, план, блокировки, права модераторов",
        "help": (
            "<p><b>Что это:</b> карточки пользователей, подписка, роли, объединение аккаунтов, выдача прав.</p>"
            "<p><b>На что влияет:</b> доступ к разделам приложения, подписка, реферальные данные пользователя.</p>"
            "<p><b>Сохранение:</b> в карточке пользователя — отдельные кнопки и формы (план, бан, права и т.д.).</p>"
        ),
    },
    {
        "id": "feedback",
        "category_id": "users",
        "label": "Обратная связь",
        "href": "/admin/feedback",
        "perm": "can_feedback",
        "subtitle": "Обращения с сайта, статусы",
        "help": (
            "<p><b>Что это:</b> очередь сообщений от пользователей с сайта.</p>"
            "<p><b>На что влияет:</b> только отметки прочтения/ответа; на продукт не влияет автоматически.</p>"
            "<p><b>Сохранение:</b> по действиям на странице обращения.</p>"
        ),
    },
    {
        "id": "broadcast",
        "category_id": "users",
        "label": "Рассылки",
        "href": "/admin/broadcast",
        "perm": "can_broadcast",
        "subtitle": "Массовые уведомления",
        "help": (
            "<p><b>Что это:</b> отправка сообщений сегментам пользователей (по правилам страницы).</p>"
            "<p><b>На что влияет:</b> входящие у пользователей в приложении/Telegram — используйте осторожно.</p>"
            "<p><b>Сохранение:</b> подтверждение отправки на форме рассылки.</p>"
        ),
    },
    {
        "id": "referral",
        "category_id": "users",
        "label": "Реферальная программа",
        "href": "/admin/referral",
        "perm": "can_users",
        "subtitle": "Префикс ссылок магазина, политики",
        "help": (
            "<p><b>Что это:</b> настройки партнёрских ссылок и правил отображения магазина в рефералке.</p>"
            "<p><b>На что влияет:</b> ссылки «купить» у приглашённых; начисление бонусов по-прежнему по правилам сервера.</p>"
            "<p><b>Сохранение:</b> кнопки сохранения на этой странице (секции формы).</p>"
        ),
    },
    {
        "id": "payment",
        "category_id": "commerce",
        "label": "Оплата и тарифы",
        "href": "/admin/payment",
        "perm": "can_payment",
        "subtitle": "Провайдеры, тарифы, способ оплаты веб / Telegram",
        "help": (
            "<p><b>Что это:</b> CloudPayments, две карточки ЮKassa (отдельные магазины для сайта и для бота/Mini App), Stars; "
            "каталог тарифов. На главной странице «Оплата» можно выбрать <b>один</b> способ для сайта и Telegram или <b>разные</b> списки для веба и для бота/приложения.</p>"
            "<p><b>На что влияет:</b> куда ведут кнопки оплаты на сайте и показывается ли счёт ЮKassa в боте; после успешной оплаты подписка активируется по общим правилам — неважно, с какого канала пришёл платёж.</p>"
            "<p><b>Сохранение:</b> карточки провайдеров и «Тарифы подписок» — отдельно; блок «Способ оплаты подписок» — одна кнопка сохранения.</p>"
        ),
    },
    {
        "id": "liquidity_exchange",
        "category_id": "commerce",
        "label": "Биржа / ликвидность",
        "href": "/admin/liquidity",
        "perm": "can_payment",
        "subtitle": "Пул Shevelev↔бонусы, coverage, автодолив",
        "help": (
            "<p><b>Что это:</b> внутренний пул обмена бонусов и токена биржи (Shevelev), ручное пополнение в блоке «Добавить ликвидность», автодолив при росте пользователей.</p>"
            "<p><b>На что влияет:</b> курс на /exchange и объём сделок; уведомления ops при низком coverage и заявках на вывод.</p>"
            "<p><b>Сохранение:</b> кнопки на странице (ликвидность и настройки автодолива).</p>"
        ),
    },
    {
        "id": "shop",
        "category_id": "commerce",
        "label": "Магазин",
        "href": "/admin/shop",
        "perm": "can_shop",
        "subtitle": "Товары, витрина, хаб Макси для рефералов",
        "help": (
            "<p><b>Что это:</b> каталог товаров на /shop и настройки эксклюзивной витрины для рефералов продавцов Макси.</p>"
            "<p><b>На что влияет:</b> видимость товаров и ссылок для части пользователей; не меняет математику реферальных бонусов.</p>"
            "<p><b>Сохранение:</b> товары — в модалке и карточках; хаб «Рефералы · Макси» — четыре независимые кнопки: эксклюзивный каталог, льготные дни, баннер перехода, опция AI-ссылки (плюс legacy-эндпоинт на всё сразу).</p>"
        ),
    },
    {
        "id": "homepage",
        "category_id": "content",
        "label": "Главная страница сайта",
        "href": "/admin/homepage",
        "perm": "can_homepage",
        "subtitle": "Порядок и видимость блоков hero, тарифы, …",
        "help": (
            "<p><b>Что это:</b> блоки публичной главной: порядок, включение, уровни доступа, тексты в рамках каждого блока.</p>"
            "<p><b>На что влияет:</b> только маркетинговую страницу для гостей и залогиненных на /.</p>"
            "<p><b>Сохранение:</b> «Сохранить порядок» и сохранение внутри карточки блока — разные действия.</p>"
            "<p><b>Ограничение:</b> набор типов блоков задан в коде; добавление нового типа блока по-прежнему требует разработки.</p>"
        ),
    },
    {
        "id": "dashboard_blocks",
        "category_id": "content",
        "label": "Блоки личного кабинета",
        "href": "/admin/dashboard-blocks",
        "perm": "can_dashboard_blocks",
        "subtitle": "Что видно в меню и дашборде",
        "help": (
            "<p><b>Что это:</b> глобальная видимость блоков (лента, магазин, AI и т.д.) и порядок для пользователей.</p>"
            "<p><b>На что влияет:</b> навигацию в приложении после входа.</p>"
            "<p><b>Сохранение:</b> по кнопкам на странице (часто одна форма на весь список — см. подсказки там).</p>"
        ),
    },
    {
        "id": "content_settings",
        "category_id": "content",
        "label": "Настройки контента",
        "href": "/admin/content-settings",
        "perm": "can_groups",
        "subtitle": "Радио, ссылки, флаги интерфейса",
        "help": (
            "<p><b>Что это:</b> переключатели поведения сайта (например глобальное радио, кликабельность ссылок), заданные в БД.</p>"
            "<p><b>На что влияет:</b> отображение и доступность функций у всех пользователей согласно выбранным флагам.</p>"
            "<p><b>Сохранение:</b> используйте кнопку сохранения на странице; при разбиении на секции — сохраняйте секцию целиком.</p>"
        ),
    },
    {
        "id": "knowledge",
        "category_id": "content",
        "label": "База знаний",
        "href": "/admin/knowledge",
        "perm": "can_knowledge",
        "subtitle": "Материалы для ответов AI",
        "help": (
            "<p><b>Что это:</b> статьи и структура знаний, из которых AI черпает контекст (в пределах настроенного режима).</p>"
            "<p><b>На что влияет:</b> качество и тематика ответов консультанта.</p>"
            "<p><b>Сохранение:</b> при редактировании записей знаний — по форме записи.</p>"
        ),
    },
    {
        "id": "community",
        "category_id": "community",
        "label": "Сообщество",
        "href": "/admin/community",
        "perm": "can_community",
        "subtitle": "Лента, модерация",
        "help": (
            "<p><b>Что это:</b> инструменты модерации публичной ленты и связанных сущностей.</p>"
            "<p><b>На что влияет:</b> видимость постов и санкции к контенту.</p>"
            "<p><b>Сохранение:</b> действиями модерации на странице.</p>"
        ),
    },
    {
        "id": "groups_chats",
        "category_id": "community",
        "label": "Группы и чаты",
        "href": "/admin/groups-chats",
        "perm": "can_groups",
        "subtitle": "Заглушка / будущие настройки",
        "help": (
            "<p><b>Что это:</b> раздел-заготовка под управление группами и чатами.</p>"
            "<p><b>На что влияет:</b> сейчас минимально; расширение без смены URL возможно позже.</p>"
        ),
    },
    {
        "id": "video_calls",
        "category_id": "community",
        "label": "Видеосвязь",
        "href": "/admin/video-calls",
        "perm": "can_groups",
        "subtitle": "Включение звонков LiveKit и лимиты",
        "help": (
            "<p><b>Что это:</b> глобальные переключатели и параметры видеозвонков между пользователями.</p>"
            "<p><b>На что влияет:</b> появление функции звонков в UI и работу сервера сигналинга.</p>"
            "<p><b>Сохранение:</b> кнопка сохранения на странице настроек.</p>"
        ),
    },
    {
        "id": "ai_community_bot",
        "category_id": "community",
        "label": "AI в сообществе",
        "href": "/admin/ai-community-bot",
        "perm": "can_users",
        "subtitle": "Аккаунт бота в ленте",
        "help": (
            "<p><b>Что это:</b> привязка пользователя-бота, который публикует и отвечает от имени NeuroFungi AI в ленте.</p>"
            "<p><b>На что влияет:</b> автоматические посты и ответы в соцблоке; не связано с личным чатом AI пользователя.</p>"
            "<p><b>Сохранение:</b> три блока с отдельными кнопками — главный выключатель, сетка разрешений по действиям, дневные лимиты; плюс формы привязки id и создания аккаунта.</p>"
        ),
    },
    {
        "id": "platform_ai_feedback",
        "category_id": "system",
        "label": "Пожелания к платформенному AI",
        "href": "/admin/platform-ai-feedback",
        "perm": "can_users",
        "subtitle": "Сообщения пользователей про AI",
        "help": (
            "<p><b>Что это:</b> очередь обратной связи именно по качеству/поведению AI платформы.</p>"
            "<p><b>На что влияет:</b> операционную работу команды; на модель напрямую не меняет веса.</p>"
        ),
    },
    {
        "id": "ai",
        "category_id": "ai",
        "label": "AI — системный промпт",
        "href": "/admin/ai",
        "perm": "can_ai",
        "subtitle": "Промпт, режимы, тест чата",
        "help": (
            "<p><b>Что это:</b> глобальные настройки текстового AI: системный промпт, режим поиска по обучающим постам (RAG), лимит фрагментов, тест чата.</p>"
            "<p><b>На что влияет:</b> ответы AI во всех сценариях, где подключается этот слой.</p>"
            "<p><b>Сохранение:</b> две независимые кнопки — «Сохранить промпт» и «Сохранить режим поиска»; каждая пишет новую запись в историю, не затирая другую половину настроек.</p>"
        ),
    },
    {
        "id": "ai_posts",
        "category_id": "ai",
        "label": "Обучающие посты",
        "href": "/admin/ai-posts",
        "perm": "can_ai_posts",
        "subtitle": "Папки и посты для RAG / обучения",
        "help": (
            "<p><b>Что это:</b> контент, который участвует в поиске и обучающих сценариях бота.</p>"
            "<p><b>На что влияет:</b> выдачу релевантных фрагментов в ответах.</p>"
            "<p><b>Сохранение:</b> при создании/редактировании поста или папки.</p>"
        ),
    },
    {
        "id": "wellness_journal",
        "category_id": "wellness",
        "label": "Дневник терапии",
        "href": "/admin/wellness-journal",
        "perm": "can_users",
        "subtitle": "Глобальное вкл/выкл, паузы, тесты",
        "help": (
            "<p><b>Что это:</b> управление функцией дневника: для всей платформы, для отдельных пользователей, тихий режим AI для админа.</p>"
            "<p><b>На что влияет:</b> расписание напоминаний дневника и сбор ответов.</p>"
            "<p><b>Сохранение:</b> у каждой формы на странице своя кнопка (глобальное, пауза пользователя и т.д.).</p>"
        ),
    },
    {
        "id": "wellness_insights",
        "category_id": "wellness",
        "label": "Статистика и инсайты дневника",
        "href": "/admin/wellness-journal/insights",
        "perm": "can_users",
        "subtitle": "Платформа и пользователи, схемы",
        "help": (
            "<p><b>Что это:</b> аналитические вкладки, агрегаты по дневнику, обновление статистики схем.</p>"
            "<p><b>На что влияет:</b> только отчёты и кэши аналитики, не цены и не подписки.</p>"
            "<p><b>Сохранение:</b> кнопки «обновить» / экспорт на самой странице.</p>"
        ),
    },
    {
        "id": "music",
        "category_id": "media",
        "label": "Музыка (плеер)",
        "href": "/admin/music",
        "perm": "can_dashboard",
        "subtitle": "Треки персонального радио",
        "help": (
            "<p><b>Что это:</b> загрузка и порядок треков для встроенного радио; глобальный переключатель кружка радио.</p>"
            "<p><b>На что влияет:</b> аудио в интерфейсе у пользователей, у которых включено радио.</p>"
            "<p><b>Доступ:</b> страница доступна полному администратору (роль admin) по историческим правилам маршрута.</p>"
            "<p><b>Сохранение:</b> «Сохранить» у глобального переключателя; загрузка и порядок — отдельные действия.</p>"
        ),
    },
    {
        "id": "radio_downtempo",
        "category_id": "media",
        "label": "Радио Down Tempo",
        "href": "/admin/radio-downtempo",
        "perm": "can_radio_downtempo",
        "subtitle": "Отдельный плейлист /down-tempo",
        "help": (
            "<p><b>Что это:</b> медиатека для режима Downtempo на сайте.</p>"
            "<p><b>На что влияет:</b> только этот аудиораздел.</p>"
            "<p><b>Сохранение:</b> по кнопкам загрузки, удаления, порядка на странице.</p>"
        ),
    },
]


def _visible(perms: dict[str, Any] | None, perm: str) -> bool:
    if perms is None:
        return True
    return bool(perms.get(perm))


def _first_alphabet_bucket(label: str) -> str:
    """Буква секции: кириллица/латиница или '#' для цифр и прочего."""
    s = (label or "").strip()
    if not s:
        return "#"
    ch0 = s[0]
    if ch0.isdigit():
        return "#"
    ch = ch0.upper()
    if ch == "Ё":
        ch = "Е"
    if len(ch) == 1 and "A" <= ch <= "Z":
        return ch
    o = ord(ch)
    if len(ch) == 1 and 0x0410 <= o <= 0x042F:  # А–Я
        return ch
    return "#"


def _section_sort_key(letter: str) -> tuple[int, int]:
    if letter == "#":
        return (2, 0)
    if len(letter) == 1 and "A" <= letter <= "Z":
        return (1, ord(letter))
    pos = _RU_LETTER_ORDER.find(letter)
    if pos >= 0:
        return (0, pos)
    return (0, 9999)


def _build_alphabet_sections(items_out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cat_icons = {c["id"]: c["icon"] for c in ADMIN_UI_CATEGORIES}
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for it in items_out:
        row = dict(it)
        row["list_icon"] = cat_icons.get(it.get("category_id"), "⚙️")
        letter = _first_alphabet_bucket(it.get("label") or "")
        buckets[letter].append(row)
    for letter in buckets:
        buckets[letter].sort(key=lambda x: (x.get("label") or "").casefold())
    ordered = sorted(buckets.keys(), key=_section_sort_key)
    sections: list[dict[str, Any]] = []
    for letter in ordered:
        aid = "sym" if letter == "#" else letter
        sections.append(
            {
                "letter": "#" if letter == "#" else letter,
                "anchor_id": aid,
                # Не ключ «items»: в Jinja sec.items — это dict.items(), а не список.
                "section_items": buckets[letter],
            }
        )
    return sections


def build_admin_ui_context(
    user_permissions: dict[str, Any] | None,
    user: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Категории с отфильтрованными пунктами + плоский список для модалки справки."""
    items_out: list[dict[str, Any]] = []
    help_registry: dict[str, str] = {}
    for it in _ADMIN_ITEMS_RAW:
        if it.get("id") == "music":
            if not user or user.get("role") != "admin":
                continue
        perm = it.get("perm") or ""
        if not _visible(user_permissions, perm):
            continue
        entry = {k: v for k, v in it.items() if k != "help"}
        entry["help_html"] = it.get("help") or ""
        items_out.append(entry)
        help_registry[it["id"]] = it.get("help") or ""

    cats_out: list[dict[str, Any]] = []
    for c in ADMIN_UI_CATEGORIES:
        cid = c["id"]
        sub = [x for x in items_out if x.get("category_id") == cid]
        if sub:
            # Не ключ «items»: в Jinja cat.items — это dict.items(), а не список пунктов.
            cats_out.append({**c, "section_items": sub})

    return {
        "categories": cats_out,
        "items": items_out,
        "help_registry": help_registry,
        "alphabet_sections": _build_alphabet_sections(items_out),
    }
