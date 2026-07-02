"""Tests for src/utils/market_hours.py — is_idx_open() and is_us_open()

Uses freezegun-style explicit patching of datetime.now() via
unittest.mock.patch to control the current time precisely.
"""

from datetime import datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from src.utils.market_hours import (
    IDX_TZ,
    US_TZ,
    is_idx_open,
    is_us_open,
)


def _make_dt(day_of_week: int, hour: int, minute: int, tz: ZoneInfo, date: tuple = None) -> datetime:
    """Build a timezone-aware datetime for a specific weekday.

    2026-07-06 is Monday (weekday=0). We offset from there.
    """
    base = datetime(2026, 7, 6 + day_of_week, hour, minute, tzinfo=tz)
    return base


# ── IDX Market Hours ───────────────────────────────────────────────
# Mon-Fri 09:00–15:30 WIB (Asia/Jakarta), closed 12:00–13:30 lunch

class TestIDXOpen:
    """is_idx_open checks local time in Asia/Jakarta"""

    def _check(self, weekday: int, hour: int, minute: int) -> bool:
        dt = _make_dt(weekday, hour, minute, IDX_TZ)
        with patch("src.utils.market_hours.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            return is_idx_open()

    # --- open hours (outside lunch) ---

    def test_morning_open_at_9_00(self):
        """Exact open time should be open (Monday 09:00)"""
        assert self._check(0, 9, 0) is True

    def test_mid_morning(self):
        assert self._check(0, 10, 30) is True

    def test_just_before_lunch(self):
        """11:59 should still be open"""
        assert self._check(0, 11, 59) is True

    def test_afternoon_after_lunch(self):
        """13:31 is just after lunch end (13:30) — should be open"""
        assert self._check(0, 13, 31) is True

    def test_late_afternoon(self):
        assert self._check(0, 14, 45) is True

    def test_exact_close_15_30(self):
        """15:30 is the close boundary — inclusive, still open"""
        assert self._check(0, 15, 30) is True

    # --- lunch break (12:00–13:30 closed) ---

    def test_lunch_start_12_00(self):
        assert self._check(0, 12, 0) is False

    def test_mid_lunch(self):
        assert self._check(0, 12, 30) is False

    def test_lunch_13_00(self):
        assert self._check(0, 13, 0) is False

    def test_lunch_end_13_29(self):
        """13:29 is still lunch"""
        assert self._check(0, 13, 29) is False

    def test_lunch_end_13_30(self):
        """13:30 is still lunch (inclusive upper bound)"""
        assert self._check(0, 13, 30) is False

    # --- before/after hours ---

    def test_before_open_8_59(self):
        assert self._check(0, 8, 59) is False

    def test_early_morning(self):
        assert self._check(0, 6, 0) is False

    def test_after_close_15_31(self):
        assert self._check(0, 15, 31) is False

    def test_evening(self):
        assert self._check(0, 20, 0) is False

    # --- weekends ---

    def test_saturday_closed(self):
        """Saturday (weekday=5)"""
        assert self._check(5, 10, 0) is False

    def test_sunday_closed(self):
        """Sunday (weekday=6)"""
        assert self._check(6, 10, 0) is False

    # --- edge: each weekday ---

    @pytest.mark.parametrize("weekday", [0, 1, 2, 3, 4])
    def test_all_weekdays_open_at_10am(self, weekday):
        assert self._check(weekday, 10, 0) is True


# ── US Market Hours ────────────────────────────────────────────────
# Mon-Fri 09:30–16:00 ET (America/New_York)

class TestUSOpen:
    """is_us_open checks local time in America/New_York"""

    def _check(self, weekday: int, hour: int, minute: int) -> bool:
        dt = _make_dt(weekday, hour, minute, US_TZ)
        with patch("src.utils.market_hours.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            return is_us_open()

    # --- open hours ---

    def test_exact_open_9_30(self):
        """09:30 should be open"""
        assert self._check(0, 9, 30) is True

    def test_mid_day(self):
        assert self._check(0, 12, 0) is True

    def test_late_afternoon(self):
        assert self._check(0, 15, 59) is True

    def test_exact_close_16_00(self):
        """16:00 is the close boundary — inclusive, still open"""
        assert self._check(0, 16, 0) is True

    # --- before/after hours ---

    def test_before_open_9_29(self):
        assert self._check(0, 9, 29) is False

    def test_early_morning(self):
        assert self._check(0, 4, 0) is False

    def test_after_close_16_01(self):
        assert self._check(0, 16, 1) is False

    def test_evening(self):
        assert self._check(0, 21, 0) is False

    # --- weekends ---

    def test_saturday_closed(self):
        assert self._check(5, 10, 0) is False

    def test_sunday_closed(self):
        assert self._check(6, 10, 0) is False

    # --- edge: each weekday ---

    @pytest.mark.parametrize("weekday", [0, 1, 2, 3, 4])
    def test_all_weekdays_open_at_noon(self, weekday):
        assert self._check(weekday, 12, 0) is True
