"""migrate_v36 — админ: тихий режим (только JSON) и счётчик цепочки вопросов для теста дневника."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_admin_ai_silent BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_admin_q_index INTEGER NOT NULL DEFAULT 0",
]
