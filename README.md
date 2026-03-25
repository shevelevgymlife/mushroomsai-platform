# NEUROFUNGI AI Platform

AI-платформа по функциональным грибам: сайт с AI консультантом, магазином и соцсетью.

## Стек

- **Python 3.11** + **FastAPI** — backend
- **Jinja2** + **Tailwind CSS** — шаблоны и UI
- **OpenAI GPT-4o** — AI
- **PostgreSQL** + **asyncpg** — база данных
- **Render** — хостинг

Интеграция с Telegram в коде **отключена** (уведомления и вход — через сайт и email).

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

Заполнить значения в `.env`:

| Переменная | Где взять |
|---|---|
| `OPENAI_API_KEY` | platform.openai.com |
| `DATABASE_URL` | Render → PostgreSQL → Internal URL |
| `GOOGLE_CLIENT_ID` | console.cloud.google.com |
| `GOOGLE_CLIENT_SECRET` | console.cloud.google.com |
| `JWT_SECRET` | любая случайная строка (32+ символа) |
| `ADMIN_TG_ID` | Telegram ID владельца (для совместимости данных пользователей; узнать у @userinfobot) |
| `ADMIN_EMAIL` | email владельца (Google) — выдаёт роль оператора при совпадении с аккаунтом |
| `SITE_URL` | https://mushroomsai.ru |
| `DEPLOY_NOTIFY_EMAIL_TO` | email для уведомлений о новом деплое (опционально) |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` | SMTP для писем (опционально) |
| `DEPLOY_NOTIFY_TASK_EMAIL_TO` | email для статусов задач (если пусто — `DEPLOY_NOTIFY_EMAIL_TO`) |

### Служебные уведомления по email (этапы задачи и деплоя)

Можно получать письма о ключевых этапах (если заданы SMTP и адреса):

1. `Задача принята`  
2. `Задача завершена`  
3. `Отправил в Render на деплой`  
4. `Деплой завершён` (при старте нового инстанса на Render)

Команды (локально / CI):

```bash
python scripts/task_notify.py accepted --task "Короткое описание задачи"
python scripts/task_notify.py done --result "Короткий итог по задаче"
python scripts/task_notify.py deploy_sent --task "Короткое описание задачи"
python scripts/task_notify.py confirm --question "Подтвердить запуск команды?" --details "Будет выполнена операция X" --wait-seconds 600
```

Сообщение `deploy_completed` уходит автоматически из `main.py` при успешном старте.

Команда `confirm` создаёт запрос подтверждения в БД и ждёт ответа в админке (или таймаута): код выхода `0` при согласии, `2` при отказе/таймауте, `3` если запрос не создан.

### Ops-уведомления (email)

При настроенном SMTP и адресах планировщик может слать сводки и billing-предупреждения. Дополнительные поля:

```bash
OPS_NOTIFY_DAILY_SUMMARY_HOUR_UTC=9
OPS_NOTIFY_BILLING_DUE_AT=2026-04-01
OPS_NOTIFY_BILLING_CURRENT_USD=0
OPS_NOTIFY_BILLING_LIMIT_USD=0
OPS_NOTIFY_BILLING_WARN_PERCENT=90
```

### Внешний раннер задач (webhook, опционально)

Если заданы `TASK_AUTORUN_WEBHOOK_URL` и при необходимости `TASK_AUTORUN_WEBHOOK_TOKEN` или `TASK_AUTORUN_SECRET`, принятые задачи можно отправлять на внешний endpoint (см. `services/task_autorun.py`).

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
5. Добавить переменные из `.env` в Environment Variables  
6. Создать PostgreSQL на Render и скопировать Internal Database URL в `DATABASE_URL`  

В `render.yaml` у каждой переменной окружения должен быть **один** ключ `value:` (дубликаты ломают YAML).

## Структура

```
main.py          # FastAPI приложение
config.py        # настройки из .env
auth/            # авторизация (Google / Email)
web/             # FastAPI роуты и HTML шаблоны
ai/              # OpenAI клиент и системный промт
db/              # модели БД
services/        # бизнес-логика
static/          # CSS, JS
```

## Первый запуск

После первого деплоя:

1. Откройте `/login`, войдите через Google (или email, если включено). Для доступа в `/admin` пользователь должен иметь роль `admin` (например, через `ADMIN_EMAIL`, совпадающий с Google-аккаунтом).
2. Добавьте товары в Маркетплейсе.
3. При необходимости отредактируйте AI системный промт в разделе AI.

Миграции колонок и таблиц выполняются при старте `main.py` (см. `new_columns`). Юридические поля (`legal_accepted_at` и др.), заявки в группы и запросы смены тарифа появятся после первого успешного деплоя с обновлённым кодом.

### Как задеплоить изменения

```bash
git add -A
git commit -m "Описание изменений"
git push origin master
```

На Render деплой подключается к ветке репозитория автоматически.

## Разработка

- Домен в конфигах: `mushroomsai.ru` — замените на свой при форке.
