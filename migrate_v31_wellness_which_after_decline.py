"""migrate_v31 — после отказа включать ответ в статистику: спросить, какое сообщение включить."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_awaiting_which_stats_after_decline BOOLEAN NOT NULL DEFAULT false",
]
