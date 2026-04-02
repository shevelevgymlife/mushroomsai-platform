"""migrate_v34 — JSON-профиль для AI: эвристики грибов/связок по метрикам пользователя."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_ai_profile_json TEXT",
]
