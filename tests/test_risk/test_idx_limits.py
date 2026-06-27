"""Tests for IDX market compliance rules."""
import pytest
from datetime import date, timedelta

from src.risk.idx_limits import (
    tick_size, round_to_tick, ara_ceiling, arb_floor,
    validate_order, max_lots_by_adv, settlement_date, is_settled,
)


class TestTickSize:
    def test_under_200(self):
        assert tick_size(100) == 1
        assert tick_size(199) == 1

    def test_200_to_499(self):
        assert tick_size(200) == 2
        assert tick_size(350) == 2
        assert tick_size(499) == 2

    def test_500_to_1999(self):
        assert tick_size(500) == 5
        assert tick_size(1000) == 5
        assert tick_size(1999) == 5

    def test_2000_to_4999(self):
        assert tick_size(2000) == 10
        assert tick_size(3500) == 10
        assert tick_size(4999) == 10

    def test_5000_and_above(self):
        assert tick_size(5000) == 25
        assert tick_size(9500) == 25
        assert tick_size(50000) == 25


class TestRoundToTick:
    def test_rounds_to_nearest_tick(self):
        assert round_to_tick(9500) == 9500
        assert round_to_tick(9501) == 9500
        assert round_to_tick(9512) == 9500
        assert round_to_tick(9513) == 9525

    def test_small_prices(self):
        assert round_to_tick(100) == 100
        assert round_to_tick(101) == 101


class TestARALimits:
    def test_ara_ceiling(self):
        assert ara_ceiling(10000) == 12500
        assert ara_ceiling(8000) == 10000

    def test_arb_floor(self):
        assert arb_floor(10000) == 7500
        assert arb_floor(8000) == 6000


class TestValidateOrder:
    def test_valid_order(self):
        validate_order("BBCA", 8500, 8400, 10)  # Should not raise

    def test_minimum_lots(self):
        with pytest.raises(ValueError, match="minimum 1 lot"):
            validate_order("BBCA", 8500, 8400, 0)

    def test_invalid_tick_price(self):
        with pytest.raises(ValueError, match="not a valid tick price"):
            validate_order("BBCA", 8501, 8400, 10)

    def test_exceeds_ara(self):
        with pytest.raises(ValueError, match="exceeds ARA ceiling"):
            validate_order("BBCA", 12600, 10000, 10)

    def test_below_arb(self):
        with pytest.raises(ValueError, match="below ARB floor"):
            validate_order("BBCA", 7400, 10000, 10)

    def test_without_adv(self):
        validate_order("BBCA", 8500, 8400, 500)  # No ADV check

    def test_with_adv_pass(self):
        validate_order("BBCA", 8500, 8400, 50, adv_20d=100000)

    def test_with_adv_fail(self):
        with pytest.raises(ValueError, match="exceeds 10% ADV"):
            validate_order("BBCA", 8500, 8400, 200, adv_20d=100000)

    def test_adv_too_low(self):
        with pytest.raises(ValueError, match="ADV too low"):
            validate_order("SMALL", 100, 99, 1, adv_20d=500)


class TestMaxLotsByAdv:
    def test_normal(self):
        assert max_lots_by_adv(100000, 5000) == 100

    def test_zero_adv(self):
        assert max_lots_by_adv(0, 5000) == 0

    def test_low_adv(self):
        assert max_lots_by_adv(500, 5000) == 0

    def test_custom_max_pct(self):
        assert max_lots_by_adv(100000, 5000, max_adv_pct=0.05) == 50


class TestSettlement:
    def test_t2_weekday(self):
        # Monday -> Wednesday
        monday = date(2026, 6, 22)
        assert settlement_date(monday) == date(2026, 6, 24)

    def test_t2_friday(self):
        # Friday -> Tuesday
        friday = date(2026, 6, 26)
        assert settlement_date(friday) == date(2026, 6, 30)

    def test_is_settled(self):
        old_date = date(2026, 1, 1)
        assert is_settled(old_date) is True
