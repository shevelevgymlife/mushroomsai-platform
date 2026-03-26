"""Темы оформления профиля (id → подпись). CSS: static/css/profile-ui-themes.css + data-profile-ui на .cp-ig-root."""

PROFILE_UI_THEMES = [
    {"id": "default", "label": "Стандарт — тёмное полотно"},
    {"id": "tg-night", "label": "Telegram — ночная синяя"},
    {"id": "tg-day", "label": "Telegram — дневная"},
    {"id": "tg-monochrome", "label": "Telegram — монохром"},
    {"id": "forest-moss", "label": "Грибы — мох и лес"},
    {"id": "amanita-red", "label": "Мухомор — красный кап"},
    {"id": "porcini-gold", "label": "Белый гриб — золото"},
    {"id": "chanterelle-sun", "label": "Лисички — солнечные"},
    {"id": "spores-mist", "label": "Споры в тумане"},
    {"id": "neon-mycelium", "label": "Неоновый мицелий"},
    {"id": "deep-ocean", "label": "Глубокий океан"},
    {"id": "aurora-borealis", "label": "Северное сияние"},
    {"id": "sunset-gradient", "label": "Закат — градиент"},
    {"id": "lavender-dream", "label": "Лавандовый сон"},
    {"id": "cyber-magenta", "label": "Кибер-пурпур"},
    {"id": "paper-craft", "label": "Бумажный крафт"},
    {"id": "midnight-violet", "label": "Полночь фиолет"},
    {"id": "emerald-luxury", "label": "Изумруд люкс"},
    {"id": "copper-rust", "label": "Медь и ржавчина"},
    {"id": "ice-fjord", "label": "Лёд фьорда"},
    {"id": "sakura-bloom", "label": "Сакура"},
    {"id": "desert-dune", "label": "Пустыня — дюны"},
    {"id": "volcanic-ash", "label": "Вулканический пепел"},
    {"id": "matrix-rain", "label": "Матрица — дождь"},
    {"id": "retro-crt", "label": "Ретро CRT"},
    {"id": "glass-morphism", "label": "Стекло — glassmorphism"},
    {"id": "candy-pop", "label": "Конфетный поп-арт"},
    {"id": "ink-wash", "label": "Тушь — японская"},
    {"id": "steampunk-brass", "label": "Стимпанк латунь"},
    {"id": "cosmic-dust", "label": "Космическая пыль"},
]

PROFILE_UI_THEME_IDS = {t["id"] for t in PROFILE_UI_THEMES}

# Согласовано с MAX_PROFILE_CIRCLES в web.routes.public
MAX_PROFILE_CIRCLES_ACCOUNT = 6
