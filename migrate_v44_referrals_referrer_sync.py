"""Однократно выровнять referrals.referrer_id с users.referred_by (после merge аккаунтов)."""

STEPS = [
    """
    UPDATE referrals AS r
    SET referrer_id = u.referred_by
    FROM users AS u
    WHERE u.id = r.referred_id
      AND u.referred_by IS NOT NULL
      AND u.referred_by <> u.id
      AND r.referrer_id IS DISTINCT FROM u.referred_by
    """,
]
