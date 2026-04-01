# Юридический пакет (сайт + бот) — для Cursor / разработки

## Версия документов

- Константа `LEGAL_DOCS_VERSION` в `services/legal.py`. При смене текста оферты / политики / существенных правил в `accept` — **увеличить версию**, чтобы пользователи снова прошли `/legal/accept`.

## URL на сайте

| Страница | Путь |
|----------|------|
| Публичная оферта | `/legal/offer` |
| Политика конфиденциальности | `/legal/privacy` |
| Пользовательское соглашение | `/legal/terms` |
| Принятие пакета документов (после входа) | `/legal/accept` |

Страницы `/legal/*` **не** требуют предварительного accept (см. `LegalAcceptanceGateMiddleware` в `main.py`).

## Вход и логика

- Редирект на accept, если `legal_docs_version` ≠ актуальной или нет `legal_accepted_at` (`services/legal.py` → `legal_acceptance_redirect`).
- Рефералы и сценарии входа **не** менялись: только тексты, ссылки и версия.

## Telegram-бот

- `SITE_URL` — база для ссылок в боте (оферта/политика).
- Команды: `/terms` → ссылка на `{SITE_URL}/legal/offer`, `/privacy` → `{SITE_URL}/legal/privacy` (`bot/handlers/legal_commands.py`, регистрация в `bot/main_bot.py`, группа `-4`).
- Меню подписки и счёт: текст о невозврате и ссылки — в `bot/handlers/yookassa_subscribe.py`.

## Оплата на сайте

- Блок с офертой и формулировкой о возврате / доступе до конца периода — `web/templates/subscriptions.html` (секция ЮKassa).

## Удаление данных пользователем

- `GET/POST /account/delete-data` — подтверждение фразой **УДАЛИТЬ** + чекбокс; вызов `permanently_delete_user` для **primary** аккаунта (`_resolve_primary_row`). Владелец платформы защищён (`is_protected_super_admin`).
- Ссылка в `web/templates/account/settings.html`.

## Шаблоны и футер

- Согласие: `web/templates/legal/accept.html` (оферта + политика + пользовательское соглашение).
- Футер, drawer, онбординг тарифа: ссылки на оферту добавлены рядом с остальными документами.

## JSON-спека из ТЗ

Логика соответствует переданной схеме (`consent_screen` → `/legal/accept`, `commands` → `/terms` и `/privacy`, `subscription.on_payment`, `profile.delete_user_data` → `/account/delete-data`, `footer_links` → шаблоны сайта).
