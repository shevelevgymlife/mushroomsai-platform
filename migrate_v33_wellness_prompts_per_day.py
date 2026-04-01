"""migrate_v33 — сколько раз в день слать промпт дневника (1 / 2 / 3 слота UTC)."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_journal_prompts_per_day INTEGER NOT NULL DEFAULT 1",
]
