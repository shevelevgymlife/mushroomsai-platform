"""migrate_v30 — Подтверждение включения ответа в статистику; pending entry id на пользователе."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_pending_stats_entry_id INTEGER",
    "ALTER TABLE wellness_journal_entries ADD COLUMN IF NOT EXISTS statistics_excluded BOOLEAN NOT NULL DEFAULT false",
    # Старые ответы без структурирования не считать подтверждённой статистикой; с JSON оставить в сводках.
    """
    UPDATE wellness_journal_entries SET statistics_excluded = true
    WHERE role = 'user_reply' AND (extracted_json IS NULL OR btrim(extracted_json) = '')
    """,
]
