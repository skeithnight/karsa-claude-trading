"""Tests for volatility-targeted position sizing."""
import pytest
from decimal import Decimal

from src.advisory.sizing import calculate_position_size, calculate_stop_loss


class TestCalculatePositionSize:
    def test_normal_case(self):
        # $100k equity, 1% risk, $50 entry, $2 ATR
        size = calculate_position_size(100000, 0.01, 50, 2)
        # risk = $1000, stop_distance = $4, size = 250 shares
        assert size == Decimal("250")

    def test_zero_atr(self):
        assert calculate_position_size(100000, 0.01, 50, 0) == Decimal(0)

    def test_zero_entry(self):
        assert calculate_position_size(100000, 0.01, 0, 2) == Decimal(0)

    def test_negative_atr(self):
        assert calculate_position_size(100000, 0.01, 50, -1) == Decimal(0)

    def test_high_volatility_smaller_position(self):
        low_vol = calculate_position_size(100000, 0.01, 100, 1)
        high_vol = calculate_position_size(100000, 0.01, 100, 5)
        assert low_vol > high_vol

    def test_custom_stop_multiplier(self):
        size_2x = calculate_position_size(100000, 0.01, 50, 2, stop_multiplier=2.0)
        size_3x = calculate_position_size(100000, 0.01, 50, 2, stop_multiplier=3.0)
        assert size_2x > size_3x  # Wider stop = smaller position


class TestCalculateStopLoss:
    def test_long_stop(self):
        stop = calculate_stop_loss(100, 2, "LONG")
        assert stop == 96.0  # 100 - 2*2

    def test_short_stop(self):
        stop = calculate_stop_loss(100, 2, "SHORT")
        assert stop == 104.0  # 100 + 2*2

    def test_custom_multiplier(self):
        stop = calculate_stop_loss(100, 2, "LONG", stop_multiplier=3.0)
        assert stop == 94.0  # 100 - 2*3
