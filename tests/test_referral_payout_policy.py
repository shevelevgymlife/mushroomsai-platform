"""
Проверка правил окна вывода и границ без БД (python -m pytest tests/test_referral_payout_policy.py
или python tests/test_referral_payout_policy.py).
"""
from __future__ import annotations


def test_moscow_window_inside_range():
    from services.referral_payout_settings import moscow_calendar_day_in_window

    assert moscow_calendar_day_in_window(1, 1, 5) is True
    assert moscow_calendar_day_in_window(5, 1, 5) is True
    assert moscow_calendar_day_in_window(3, 1, 5) is True
    assert moscow_calendar_day_in_window(15, 1, 5) is False
    assert moscow_calendar_day_in_window(31, 10, 31) is True


def test_moscow_window_swapped_bounds_normalized():
    from services.referral_payout_settings import moscow_calendar_day_in_window

    assert moscow_calendar_day_in_window(3, 5, 1) is True


def test_clamp_min_rub():
    from services.referral_payout_settings import _clamp_min_rub

    assert _clamp_min_rub("5000", 100) == 5000
    assert _clamp_min_rub("1", 100) == 1
    assert _clamp_min_rub("bad", 999) == 999


def test_clamp_day():
    from services.referral_payout_settings import _clamp_day

    assert _clamp_day("1", 5) == 1
    assert _clamp_day("31", 5) == 31
    assert _clamp_day("0", 3) == 1
    assert _clamp_day("99", 3) == 31


if __name__ == "__main__":
    test_moscow_window_inside_range()
    test_moscow_window_swapped_bounds_normalized()
    test_clamp_min_rub()
    test_clamp_day()
    print("ok")
