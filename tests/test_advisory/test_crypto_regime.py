"""Tests for CryptoRegimeClassifier — Hurst + ADX deterministic regime detection."""

import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock

from src.advisory.crypto_regime import (
    _adx,
    _hurst_exponent,
    _size_multiplier,
    _get_cached,
    _set_cached,
    _regime_cache,
    _regime_cache_ttl,
    CryptoRegimeFilter,
)


# ── _adx ───────────────────────────────────────────────────────────────────────

class TestADX:
    def test_uptrend_returns_high_adx(self):
        """Steadily rising prices should produce ADX > 25."""
        n = 60
        highs = [100 + i * 1.5 for i in range(n)]
        lows = [99 + i * 1.5 for i in range(n)]
        closes = [100 + i * 1.5 - 0.5 for i in range(n)]
        adx = _adx(highs, lows, closes)
        assert adx > 25, f"Uptrend ADX {adx} should be > 25"

    def test_downtrend_returns_high_adx(self):
        """Steadily falling prices should also produce ADX > 25."""
        n = 60
        highs = [200 - i * 1.5 for i in range(n)]
        lows = [199 - i * 1.5 for i in range(n)]
        closes = [200 - i * 1.5 - 0.5 for i in range(n)]
        adx = _adx(highs, lows, closes)
        assert adx > 25, f"Downtrend ADX {adx} should be > 25"

    def test_flat_returns_low_adx(self):
        """Sideways price action should produce ADX < 20."""
        n = 60
        highs = [100.5] * n
        lows = [99.5] * n
        closes = [100.0] * n
        adx = _adx(highs, lows, closes)
        assert adx < 20, f"Flat ADX {adx} should be < 20"

    def test_insufficient_data_returns_zero(self):
        """Fewer than period+1 data points returns 0."""
        adx = _adx([1, 2], [0.5, 1.5], [0.8, 1.8], period=14)
        assert adx == 0.0

    def test_empty_returns_zero(self):
        assert _adx([], [], []) == 0.0

    def test_single_element(self):
        assert _adx([100], [99], [99.5]) == 0.0

    def test_exact_period_plus_one(self):
        """Exactly period+1 points should compute (minimum viable)."""
        n = 15  # period=14 needs at least 15
        highs = [100 + i * 0.5 for i in range(n)]
        lows = [99 + i * 0.5 for i in range(n)]
        closes = [99.5 + i * 0.5 for i in range(n)]
        adx = _adx(highs, lows, closes)
        assert adx >= 0

    def test_choppy_range(self):
        """Alternating up/down within a range should give low ADX."""
        n = 60
        highs = [101 if i % 2 == 0 else 100.5 for i in range(n)]
        lows = [99 if i % 2 == 0 else 99.5 for i in range(n)]
        closes = [100 if i % 2 == 0 else 100.2 for i in range(n)]
        adx = _adx(highs, lows, closes)
        assert adx < 25, f"Choppy ADX {adx} should be < 25"


# ── _hurst_exponent ────────────────────────────────────────────────────────────

class TestHurstExponent:
    def test_trending_prices_high_hurst(self):
        """Monotonically increasing prices should give H > 0.5."""
        prices = [100 + i * 0.5 for i in range(200)]
        h = _hurst_exponent(prices)
        assert h > 0.5, f"Trending Hurst {h} should be > 0.5"

    def test_insufficient_data_returns_half(self):
        """Fewer than 20 prices returns 0.5 (random walk assumption)."""
        assert _hurst_exponent([1, 2, 3]) == 0.5
        assert _hurst_exponent([]) == 0.5

    def test_random_walk_around_half(self):
        """Noisy data should cluster around 0.5."""
        import random
        random.seed(42)
        prices = [100]
        for _ in range(200):
            prices.append(prices[-1] * (1 + random.uniform(-0.02, 0.02)))
        h = _hurst_exponent(prices)
        # Should be near 0.5 (random walk), allow wide tolerance
        assert 0.3 < h < 0.8, f"Random walk Hurst {h} should be near 0.5"

    def test_bounded_0_to_1(self):
        prices = [100 + i for i in range(200)]
        h = _hurst_exponent(prices)
        assert 0.0 <= h <= 1.0


# ── _size_multiplier ───────────────────────────────────────────────────────────

class TestSizeMultiplier:
    def test_trend_bull(self):
        assert _size_multiplier("TREND_BULL") == 1.2

    def test_trend_bear(self):
        assert _size_multiplier("TREND_BEAR") == 0.5

    def test_mean_reversion(self):
        assert _size_multiplier("MEAN_REVERSION") == 0.8

    def test_chop(self):
        assert _size_multiplier("CHOP") == 0.5

    def test_unknown_defaults_to_one(self):
        assert _size_multiplier("UNKNOWN") == 1.0
        assert _size_multiplier("NOT_A_REGIME") == 1.0


# ── Cache ──────────────────────────────────────────────────────────────────────

class TestCache:
    def setup_method(self):
        _regime_cache.clear()

    def teardown_method(self):
        _regime_cache.clear()

    def test_get_cached_miss(self):
        assert _get_cached() is None

    def test_set_and_get_cached(self):
        data = {"state": "TREND_BULL", "benchmark_price": 60000}
        _set_cached(data)
        assert _get_cached() == data

    def test_cache_expired(self):
        data = {"state": "CHOP"}
        _regime_cache["CRYPTO"] = {"data": data, "ts": time.time() - 400}  # 400s ago
        assert _get_cached() is None  # TTL is 300s

    def test_cache_within_ttl(self):
        data = {"state": "TREND_BEAR"}
        _regime_cache["CRYPTO"] = {"data": data, "ts": time.time() - 100}  # 100s ago
        assert _get_cached() == data

    def test_cache_ttl_constant(self):
        assert _regime_cache_ttl == 300


# ── CryptoRegimeFilter._unknown ───────────────────────────────────────────────

class TestUnknown:
    def test_returns_unknown_state(self):
        client = AsyncMock()
        f = CryptoRegimeFilter(client)
        result = f._unknown("test reason")
        assert result["state"] == "UNKNOWN"
        assert result["benchmark"] == "BTCUSDT"
        assert result["benchmark_price"] == "N/A"
        assert result["hurst"] == "N/A"
        assert result["adx"] == "N/A"
        assert result["ema_200_proxy"] == "N/A"
        assert "test reason" in result["recommendation"]
        assert result["size_multiplier"] == 1.0

    def test_required_keys_present(self):
        client = AsyncMock()
        f = CryptoRegimeFilter(client)
        result = f._unknown("r")
        expected = {"state", "benchmark", "benchmark_price", "hurst", "adx", "ema_200_proxy", "recommendation", "size_multiplier"}
        assert expected.issubset(result.keys())


# ── CryptoRegimeFilter.get_current_regime ──────────────────────────────────────

def _make_candles(n, start=60000, step=100):
    """Generate synthetic OHLCV candles for testing."""
    candles = []
    for i in range(n):
        base = start + i * step
        candles.append({
            "open": base,
            "high": base + 50,
            "low": base - 50,
            "close": base + 25,
            "volume": 1000 + i * 10,
        })
    return candles


class TestGetCurrentRegime:
    def setup_method(self):
        _regime_cache.clear()

    def teardown_method(self):
        _regime_cache.clear()

    @pytest.mark.asyncio
    async def test_cache_hit_skips_api(self):
        """If cache is populated, no API calls should be made."""
        cached_data = {"state": "TREND_BULL", "benchmark_price": 60000}
        _set_cached(cached_data)

        mcp = AsyncMock()
        f = CryptoRegimeFilter(mcp)
        result = await f.get_current_regime()

        assert result == cached_data
        mcp.get_ohlcv.assert_not_called()

    @pytest.mark.asyncio
    async def test_insufficient_data_returns_unknown(self):
        mcp = AsyncMock()
        mcp.get_ohlcv.return_value = _make_candles(10)  # < 60
        f = CryptoRegimeFilter(mcp)

        with patch("src.advisory.crypto_regime._get_btc_dominance", new_callable=AsyncMock, return_value={"btc_dominance": 50, "eth_dominance": 20, "season": "NEUTRAL"}):
            result = await f.get_current_regime()

        assert result["state"] == "UNKNOWN"
        assert "Insufficient" in result["recommendation"]

    @pytest.mark.asyncio
    async def test_empty_ohlcv_returns_unknown(self):
        mcp = AsyncMock()
        mcp.get_ohlcv.return_value = None
        f = CryptoRegimeFilter(mcp)

        with patch("src.advisory.crypto_regime._get_btc_dominance", new_callable=AsyncMock, return_value={"btc_dominance": 50, "eth_dominance": 20, "season": "NEUTRAL"}):
            result = await f.get_current_regime()

        assert result["state"] == "UNKNOWN"

    @pytest.mark.asyncio
    async def test_api_error_falls_back_to_cache(self):
        mcp = AsyncMock()
        mcp.get_ohlcv.side_effect = ConnectionError("timeout")
        f = CryptoRegimeFilter(mcp)

        # Pre-populate cache
        stale_data = {"state": "CHOP", "benchmark_price": 55000}
        _regime_cache["CRYPTO"] = {"data": stale_data, "ts": time.time() - 400}

        # Even though cache is expired, on error the code checks _regime_cache directly
        result = await f.get_current_regime()
        assert result["state"] == "CHOP"

    @pytest.mark.asyncio
    async def test_api_error_no_cache_returns_unknown(self):
        mcp = AsyncMock()
        mcp.get_ohlcv.side_effect = RuntimeError("boom")
        f = CryptoRegimeFilter(mcp)

        result = await f.get_current_regime()
        assert result["state"] == "UNKNOWN"
        assert "boom" in result["recommendation"]

    @pytest.mark.asyncio
    async def test_trend_bull_regime(self):
        """Rising prices, high ADX, H > 0.5, price > EMA200."""
        n = 200
        # Strong uptrend: prices rise steadily
        candles_4h = _make_candles(n, start=50000, step=200)
        candles_1d = _make_candles(60, start=40000, step=500)

        mcp = AsyncMock()
        mcp.get_ohlcv = AsyncMock(side_effect=lambda sym, mkt, timeframe="4h", limit=200: candles_4h if timeframe == "4h" else candles_1d)

        f = CryptoRegimeFilter(mcp)
        with patch("src.advisory.crypto_regime._get_btc_dominance", new_callable=AsyncMock, return_value={"btc_dominance": 52, "eth_dominance": 18, "season": "NEUTRAL"}):
            result = await f.get_current_regime()

        assert result["state"] == "TREND_BULL"
        assert result["benchmark"] == "BTCUSDT"
        assert result["size_multiplier"] == 1.2
        assert "bullish" in result["recommendation"].lower() or "Bullish" in result["recommendation"]

    @pytest.mark.asyncio
    async def test_trend_bear_regime(self):
        """Falling prices, high ADX, H > 0.5, price < EMA200."""
        n = 200
        # Strong downtrend
        candles_4h = _make_candles(n, start=80000, step=-200)
        candles_1d = _make_candles(60, start=90000, step=-500)

        mcp = AsyncMock()
        mcp.get_ohlcv = AsyncMock(side_effect=lambda sym, mkt, timeframe="4h", limit=200: candles_4h if timeframe == "4h" else candles_1d)

        f = CryptoRegimeFilter(mcp)
        with patch("src.advisory.crypto_regime._get_btc_dominance", new_callable=AsyncMock, return_value={"btc_dominance": 52, "eth_dominance": 18, "season": "NEUTRAL"}):
            result = await f.get_current_regime()

        assert result["state"] == "TREND_BEAR"
        assert result["size_multiplier"] == 0.5

    @pytest.mark.asyncio
    async def test_chop_regime(self):
        """Flat prices → low ADX → CHOP."""
        n = 200
        # Flat: all candles at same level
        candles_4h = _make_candles(n, start=60000, step=0)
        candles_1d = _make_candles(60, start=60000, step=0)

        mcp = AsyncMock()
        mcp.get_ohlcv = AsyncMock(side_effect=lambda sym, mkt, timeframe="4h", limit=200: candles_4h if timeframe == "4h" else candles_1d)

        f = CryptoRegimeFilter(mcp)
        with patch("src.advisory.crypto_regime._get_btc_dominance", new_callable=AsyncMock, return_value={"btc_dominance": 50, "eth_dominance": 20, "season": "NEUTRAL"}):
            result = await f.get_current_regime()

        assert result["state"] == "CHOP"
        assert result["size_multiplier"] == 0.5

    @pytest.mark.asyncio
    async def test_regime_result_is_cached(self):
        """After successful get_current_regime, result should be in cache."""
        n = 200
        candles_4h = _make_candles(n, start=60000, step=0)
        candles_1d = _make_candles(60, start=60000, step=0)

        mcp = AsyncMock()
        mcp.get_ohlcv = AsyncMock(side_effect=lambda sym, mkt, timeframe="4h", limit=200: candles_4h if timeframe == "4h" else candles_1d)

        f = CryptoRegimeFilter(mcp)
        with patch("src.advisory.crypto_regime._get_btc_dominance", new_callable=AsyncMock, return_value={"btc_dominance": 50, "eth_dominance": 20, "season": "NEUTRAL"}):
            await f.get_current_regime()

        cached = _get_cached()
        assert cached is not None
        assert "state" in cached
        assert "benchmark_price" in cached
        assert "hurst" in cached
        assert "adx" in cached

    @pytest.mark.asyncio
    async def test_result_has_btc_dominance_fields(self):
        n = 200
        candles_4h = _make_candles(n, start=60000, step=0)
        candles_1d = _make_candles(60, start=60000, step=0)

        mcp = AsyncMock()
        mcp.get_ohlcv = AsyncMock(side_effect=lambda sym, mkt, timeframe="4h", limit=200: candles_4h if timeframe == "4h" else candles_1d)

        f = CryptoRegimeFilter(mcp)
        with patch("src.advisory.crypto_regime._get_btc_dominance", new_callable=AsyncMock, return_value={"btc_dominance": 55.5, "eth_dominance": 18.2, "season": "BTC_SEASON"}):
            result = await f.get_current_regime()

        assert result["btc_dominance"] == 55.5
        assert result["eth_dominance"] == 18.2
        assert result["market_season"] == "BTC_SEASON"

    @pytest.mark.asyncio
    async def test_4h_data_sufficient_1d_insufficient(self):
        """When 1d data is too short, ADX defaults to 0 → CHOP."""
        n = 200
        candles_4h = _make_candles(n, start=60000, step=50)
        candles_1d = _make_candles(5, start=60000, step=50)  # < 20

        mcp = AsyncMock()
        mcp.get_ohlcv = AsyncMock(side_effect=lambda sym, mkt, timeframe="4h", limit=200: candles_4h if timeframe == "4h" else candles_1d)

        f = CryptoRegimeFilter(mcp)
        with patch("src.advisory.crypto_regime._get_btc_dominance", new_callable=AsyncMock, return_value={"btc_dominance": 50, "eth_dominance": 20, "season": "NEUTRAL"}):
            result = await f.get_current_regime()

        # ADX defaults to 0 → CHOP (adx < 20)
        assert result["state"] == "CHOP"
        assert result["adx"] == 0.0
