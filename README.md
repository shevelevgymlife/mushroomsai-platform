# MushroomsAI Platform

AI-платформа по функциональным грибам. Telegram бот + сайт с AI консультантом, магазином и соц сетью.

## Стек

- **Python 3.11** + **FastAPI** — backend
- **Jinja2** + **Tailwind CSS** — шаблоны и UI
- **python-telegram-bot 21** — Telegram бот
- **OpenAI GPT-4o** — AI мозг
- **PostgreSQL** + **asyncpg** — база данных
- **Render** — хостинг

## Быстрый старт

### 1. Клонировать и установить зависимости

```bash
git clone https://github.com/shevelevgymlife/mushroomsai-platform
cd mushroomsai-platform
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Настроить .env

```bash
cp .env.example .env
```

Заполнить все значения в `.env`:

| Переменная | Где взять |
|---|---|
| `TELEGRAM_TOKEN` | @BotFather в Telegram |
| `OPENAI_API_KEY` | platform.openai.com |
| `DATABASE_URL` | Render → PostgreSQL → Internal URL |
| `GOOGLE_CLIENT_ID` | console.cloud.google.com |
| `GOOGLE_CLIENT_SECRET` | console.cloud.google.com |
| `JWT_SECRET` | любая случайная строка (32+ символа) |
| `ADMIN_TG_ID` | свой Telegram ID (узнать у @userinfobot) |
| `SITE_URL` | https://mushroomsai.ru |
| `DEPLOY_NOTIFY_EMAIL_TO` | email для уведомлений о новом деплое (опционально) |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` | SMTP-параметры для отправки уведомления (опционально) |

### 3. Запустить локально

```bash
uvicorn main:app --reload --port 8000
```

Сайт: http://localhost:8000
Admin: http://localhost:8000/admin

### 4. Деплой на Render

1. Создать новый Web Service на render.com
2. Подключить этот репозиторий
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Добавить все переменные из `.env` в Environment Variables
6. Создать PostgreSQL базу данных на Render
7. Скопировать Internal Database URL в `DATABASE_URL`

## Структура

```
main.py          # FastAPI приложение + запуск бота
config.py        # настройки из .env
auth/            # авторизация (Telegram / Google / Email)
bot/             # Telegram бот и хендлеры
web/             # FastAPI роуты и HTML шаблоны
ai/              # OpenAI клиент и системный промт
db/              # модели БД
services/        # бизнес-логика
static/          # CSS, JS
```

## Первый запуск

После первого деплоя:
1. Откройте `/admin` — войдите через Telegram (ваш TG ID должен быть в `ADMIN_TG_ID`)
2. Добавьте товары в Маркетплейсе
3. При необходимости отредактируйте AI системный промт в разделе AI

Миграции колонок и таблиц выполняются при старте `main.py` (см. `new_columns`). Юридические поля (`legal_accepted_at` и др.), заявки в группы и запросы смены тарифа появятся после первого успешного деплоя с обновлённым кодом.

### Как задеплоить изменения

```bash
git add -A
git commit -m "Описание изменений"
git push origin main
```

На Render деплой подключится к ветке репозитория автоматически. Убедитесь, что `ADMIN_TG_ID` задан — туда приходят запросы на смену тарифа и служебные уведомления.

## Разработка

- Telegram бот username в шаблонах: `mushrooms_ai_bot` — замените на ваш
- Домен в конфигах: `mushroomsai.ru` — замените на ваш
