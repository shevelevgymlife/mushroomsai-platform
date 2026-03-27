"""Темы оформления профиля (id → подпись + превью для выбора). CSS: static/css/profile-ui-themes.css + data-profile-ui на .cp-ig-root."""

# preview — значение CSS background для миниатюры в настройках (совпадает с фоном темы)
PROFILE_UI_THEMES = [
    {"id": "default", "label": "Стандарт — тёмное полотно", "preview": "#080808"},
    {"id": "tg-night", "label": "Telegram — ночная синяя", "preview": "radial-gradient(120% 80% at 50% 0%, #1a2a3a 0%, #0d1620 45%, #080a0e 100%)"},
    {"id": "tg-day", "label": "Telegram — дневная", "preview": "linear-gradient(180deg, #e8f4fc 0%, #c8dce8 35%, #9bb5c8 100%)"},
    {"id": "tg-monochrome", "label": "Telegram — монохром", "preview": "linear-gradient(145deg, #2a2a2a 0%, #121212 50%, #0a0a0a 100%)"},
    {"id": "forest-moss", "label": "Грибы — мох и лес", "preview": "radial-gradient(ellipse at 30% 20%, #1e3d2f 0%, #0f1f18 50%, #050807 100%)"},
    {"id": "amanita-red", "label": "Мухомор — красный кап", "preview": "radial-gradient(circle at 70% 30%, #5c1518 0%, #2a0a0c 40%, #0f0405 100%)"},
    {"id": "porcini-gold", "label": "Белый гриб — золото", "preview": "linear-gradient(160deg, #3d2e14 0%, #1a1408 50%, #0a0804 100%)"},
    {"id": "chanterelle-sun", "label": "Лисички — солнечные", "preview": "radial-gradient(ellipse at 50% 0%, #4a3a12 0%, #2a2208 45%, #0f0c04 100%)"},
    {"id": "spores-mist", "label": "Споры в тумане", "preview": "linear-gradient(180deg, #1a2228 0%, #0e1218 50%, #06080a 100%)"},
    {"id": "neon-mycelium", "label": "Неоновый мицелий", "preview": "radial-gradient(ellipse at 50% 100%, #0a2a1a 0%, #050f0a 50%, #020503 100%)"},
    {"id": "deep-ocean", "label": "Глубокий океан", "preview": "linear-gradient(180deg, #0a1a2e 0%, #051018 50%, #020810 100%)"},
    {"id": "aurora-borealis", "label": "Северное сияние", "preview": "linear-gradient(185deg, #0a1a28 0%, #0f2030 40%, #081018 70%, #040810 100%)"},
    {"id": "sunset-gradient", "label": "Закат — градиент", "preview": "linear-gradient(165deg, #3a1a2a 0%, #2a1020 35%, #120818 70%, #080408 100%)"},
    {"id": "lavender-dream", "label": "Лавандовый сон", "preview": "radial-gradient(ellipse at 50% 0%, #2a1a3a 0%, #140a1e 55%, #080510 100%)"},
    {"id": "cyber-magenta", "label": "Кибер-пурпур", "preview": "linear-gradient(145deg, #2a0a28 0%, #14051a 50%, #080208 100%)"},
    {"id": "paper-craft", "label": "Бумажный крафт", "preview": "linear-gradient(180deg, #2a2620 0%, #181612 50%, #0e0c0a 100%)"},
    {"id": "midnight-violet", "label": "Полночь фиолет", "preview": "radial-gradient(circle at 80% 20%, #2a1a4a 0%, #120a22 45%, #060308 100%)"},
    {"id": "emerald-luxury", "label": "Изумруд люкс", "preview": "linear-gradient(160deg, #0a2820 0%, #051810 50%, #020a08 100%)"},
    {"id": "copper-rust", "label": "Медь и ржавчина", "preview": "linear-gradient(170deg, #3a2218 0%, #1a1008 50%, #0a0604 100%)"},
    {"id": "ice-fjord", "label": "Лёд фьорда", "preview": "linear-gradient(180deg, #1a2838 0%, #0e1824 50%, #060a10 100%)"},
    {"id": "sakura-bloom", "label": "Сакура", "preview": "radial-gradient(ellipse at 50% 0%, #3a1a2a 0%, #1a0a14 50%, #0a0408 100%)"},
    {"id": "desert-dune", "label": "Пустыня — дюны", "preview": "linear-gradient(175deg, #3a3020 0%, #1a140c 50%, #0c0a06 100%)"},
    {"id": "volcanic-ash", "label": "Вулканический пепел", "preview": "linear-gradient(180deg, #1a1818 0%, #0e0c0c 50%, #060404 100%)"},
    {"id": "matrix-rain", "label": "Матрица — дождь", "preview": "#020805"},
    {"id": "retro-crt", "label": "Ретро CRT", "preview": "#0a0c0a"},
    {"id": "glass-morphism", "label": "Стекло — glassmorphism", "preview": "linear-gradient(135deg, rgba(30, 40, 60, 0.9) 0%, rgba(10, 12, 20, 0.95) 100%)"},
    {"id": "candy-pop", "label": "Конфетный поп-арт", "preview": "linear-gradient(125deg, #3a1a3a 0%, #1a0a2a 40%, #0a0518 100%)"},
    {"id": "ink-wash", "label": "Тушь — японская", "preview": "radial-gradient(ellipse at 40% 30%, #1a1a1a 0%, #0a0a0a 60%, #020202 100%)"},
    {"id": "steampunk-brass", "label": "Стимпанк латунь", "preview": "linear-gradient(165deg, #2a2210 0%, #141008 50%, #080604 100%)"},
    {"id": "cosmic-dust", "label": "Космическая пыль", "preview": "radial-gradient(ellipse at 50% 80%, #1a1028 0%, #0a0818 50%, #020208 100%)"},
    # Фото-темы: фон профиля в CSS дополнен overlay; превью — фото + затемнение
    {
        "id": "photo-moss-forest",
        "label": "Фото — лес и мох",
        "preview": (
            "linear-gradient(165deg, rgba(8,12,10,.88) 0%, rgba(4,8,6,.75) 100%), "
            "url(https://images.unsplash.com/photo-1448375240586-882707db888b?w=800&q=80)"
        ),
        "preview_bg_size": "cover",
        "preview_bg_pos": "center",
    },
    {
        "id": "photo-fog-trees",
        "label": "Фото — туман в лесу",
        "preview": (
            "linear-gradient(180deg, rgba(12,14,18,.85) 0%, rgba(6,8,10,.7) 100%), "
            "url(https://images.unsplash.com/photo-1511497584788-876760111969?w=800&q=80)"
        ),
        "preview_bg_size": "cover",
        "preview_bg_pos": "center",
    },
    {
        "id": "photo-spores",
        "label": "Фото — споры и свет",
        "preview": (
            "linear-gradient(125deg, rgba(10,8,14,.9) 0%, rgba(6,4,8,.78) 100%), "
            "url(https://images.unsplash.com/photo-1506905925346-21bda4d32df4?w=800&q=80)"
        ),
        "preview_bg_size": "cover",
        "preview_bg_pos": "center",
    },
]

PROFILE_UI_THEME_IDS = {t["id"] for t in PROFILE_UI_THEMES}

# Согласовано с MAX_PROFILE_CIRCLES в web.routes.public
MAX_PROFILE_CIRCLES_ACCOUNT = 6
