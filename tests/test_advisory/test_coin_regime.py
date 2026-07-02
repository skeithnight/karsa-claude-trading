import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.advisory.coin_regime import CoinRegimeEngine, CoinRegime

@pytest.fixture
def mock_mcp():
    mcp = AsyncMock()
    dummy_ohlcv = [{"high": 100, "low": 90, "close": 95} for _ in range(150)]
    mcp.get_ohlcv.return_value = dummy_ohlcv
    return mcp

@pytest.fixture
def mock_cache():
    cache = MagicMock()
    cache.client = None
    return cache

@pytest.mark.asyncio
async def test_coin_regime_full_alignment(mock_mcp, mock_cache):
    """ADX > 25 on all timeframes -> FULL_ALIGNMENT."""
    engine = CoinRegimeEngine(mock_mcp, mock_cache)

    with patch.object(engine, '_calc_adx', return_value=30.0), \
         patch('src.advisory.coin_regime.calculate_bollinger', return_value={"bbw_percentile": 50.0}):

        regime = await engine.get_regime("BTCUSDT")

        assert regime.symbol == "BTCUSDT"
        assert regime.state == "FULL_ALIGNMENT"
        assert regime.adx_15m == 30.0
        assert regime.adx_4h == 30.0
        assert regime.adx_1d == 30.0
        assert regime.bbw_percentile_15m == 50.0
        assert isinstance(regime.updated_at, float)


@pytest.mark.asyncio
async def test_coin_regime_dead_chop(mock_mcp, mock_cache):
    """ADX < 20 on all timeframes -> DEAD_CHOP."""
    engine = CoinRegimeEngine(mock_mcp, mock_cache)

    with patch.object(engine, '_calc_adx', return_value=10.0), \
         patch('src.advisory.coin_regime.calculate_bollinger', return_value={"bbw_percentile": 50.0}):

        regime = await engine.get_regime("BTCUSDT")
        assert regime.state == "DEAD_CHOP"


@pytest.mark.asyncio
async def test_coin_regime_squeeze_alert(mock_mcp, mock_cache):
    """BBW percentile <= 10 and ADX 4h > 20 -> SQUEEZE_ALERT."""
    engine = CoinRegimeEngine(mock_mcp, mock_cache)

    # Mock returns same 150-item list for all calls, so use call count to differentiate:
    # call 0 = 15m (limit=150), call 1 = 4h (limit=60), call 2 = 1d (limit=60)
    call_count = {"n": 0}

    def adx_selective(ohlcv):
        n = call_count["n"]
        call_count["n"] += 1
        if n == 0:
            return 10.0   # 15m: low ADX (< 20)
        return 25.0        # 4h, 1d: high ADX (> 20)

    with patch.object(engine, '_calc_adx', side_effect=adx_selective), \
         patch('src.advisory.coin_regime.calculate_bollinger', return_value={"bbw_percentile": 5.0}):

        regime = await engine.get_regime("BTCUSDT")
        assert regime.state == "SQUEEZE_ALERT"


@pytest.mark.asyncio
async def test_coin_regime_api_error(mock_mcp, mock_cache):
    """API error -> UNKNOWN."""
    engine = CoinRegimeEngine(mock_mcp, mock_cache)
    mock_mcp.get_ohlcv.side_effect = Exception("API error")

    regime = await engine.get_regime("BTCUSDT")
    assert regime.state == "UNKNOWN"
