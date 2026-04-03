"""v46: резерв реф. вывода — сумма в заявке не смешивается с новыми начислениями."""

STEPS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_withdraw_reserved_rub NUMERIC(12,2) DEFAULT 0",
    """UPDATE users SET referral_withdraw_reserved_rub = 0
       WHERE referral_withdraw_reserved_rub IS NULL""",
    # Актуальные pending: переносим сумму заявки в резерв и уменьшаем «доступный» баланс.
    """UPDATE users u SET referral_withdraw_reserved_rub = sub.amt
       FROM (
         SELECT user_id, SUM(amount_rub)::numeric AS amt
         FROM referral_withdrawals
         WHERE status = 'pending'
         GROUP BY user_id
       ) sub
       WHERE u.id = sub.user_id""",
    """UPDATE users SET referral_balance = GREATEST(
         0,
         COALESCE(referral_balance, 0) - COALESCE(referral_withdraw_reserved_rub, 0)
       )
       WHERE COALESCE(referral_withdraw_reserved_rub, 0) > 0""",
]
