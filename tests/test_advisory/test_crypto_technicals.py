"""Tests for deterministic crypto technical indicators."""

import pytest
from src.advisory.crypto_technicals import (
    calculate_rsi, calculate_bollinger, calculate_ema,
    calculate_macd, calculate_atr, full_analysis,
)


@pytest.fixture
def uptrend():
    return [{"open": 100 + i * 0.5, "high": 101 + i * 0.5, "low": 99 + i * 0.5,
             "close": 100.5 + i * 0.5, "volume": 1000} for i in range(60)]


@pytest.fixture
def downtrend():
    return [{"open": 200 - i * 0.5, "high": 201 - i * 0.5, "low": 199 - i * 0.5,
             "close": 199.5 - i * 0.5, "volume": 1000} for i in range(60)]


@pytest.fixture
def flat():
    return [{"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}] * 60


class TestRSI:
    def test_uptrend_bullish(self, uptrend):
        r = calculate_rsi(uptrend)
        assert r["rsi"] > 50

    def test_downtrend_bearish(self, downtrend):
        r = calculate_rsi(downtrend)
        assert r["rsi"] < 50

    def test_insufficient_data(self):
        r = calculate_rsi([{"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])
        assert "error" in r

    def test_flat_neutral(self, flat):
        r = calculate_rsi(flat)
        assert 40 <= r["rsi"] <= 60


class TestBollinger:
    def test_bands_ordered(self, uptrend):
        r = calculate_bollinger(uptrend)
        assert r["upper"] > r["middle"] > r["lower"]

    def test_bandwidth_positive(self, uptrend):
        assert calculate_bollinger(uptrend)["bandwidth"] > 0


class TestEMA:
    def test_uptrend_above(self, uptrend):
        assert calculate_ema(uptrend, 20)["price_vs_ema"] == "above"

    def test_downtrend_below(self, downtrend):
        assert calculate_ema(downtrend, 20)["price_vs_ema"] == "below"


class TestATR:
    def test_positive(self, uptrend):
        assert calculate_atr(uptrend)["atr"] > 0

    def test_volatility_classified(self, uptrend):
        assert calculate_atr(uptrend)["volatility"] in ("low", "moderate", "high", "extreme")


class TestFullAnalysis:
    def test_all_indicators(self, uptrend):
        r = full_analysis(uptrend)
        for key in ("rsi", "bollinger", "ema_20", "ema_50", "macd", "atr"):
            assert key in r
