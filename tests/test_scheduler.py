"""Tests de la logique de période des abonnements (fonction pure)."""

from datetime import date

from app.services.scheduler import _add_period


def test_daily_period():
    assert _add_period(date(2026, 1, 1), "daily", 5) == date(2026, 1, 6)


def test_weekly_period():
    assert _add_period(date(2026, 1, 1), "weekly", 2) == date(2026, 1, 15)


def test_monthly_period():
    assert _add_period(date(2026, 1, 15), "monthly", 1) == date(2026, 2, 15)


def test_monthly_period_year_rollover():
    assert _add_period(date(2026, 11, 10), "monthly", 3) == date(2027, 2, 10)


def test_monthly_clamps_day_for_february():
    # Le 31 janvier + 1 mois doit rester valide (jour ramené à ≤ 28).
    assert _add_period(date(2026, 1, 31), "monthly", 1) == date(2026, 2, 28)


def test_quarterly_period():
    assert _add_period(date(2026, 1, 1), "quarterly", 1) == date(2026, 4, 1)


def test_yearly_period():
    assert _add_period(date(2026, 6, 8), "yearly", 1) == date(2027, 6, 8)
