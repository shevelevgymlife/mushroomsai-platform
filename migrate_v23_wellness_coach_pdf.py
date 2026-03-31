"""migrate_v23 — PDF дневника по флагу админа, пауза «хватит на сегодня», напоминание о продлении подписки."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_journal_pdf_allowed BOOLEAN DEFAULT true",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_renewal_nudge_for_end TIMESTAMPTZ",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS wellness_coach_pause_until TIMESTAMPTZ",
]
