"""Tests for CryptoMarketWatchEngine."""
import pytest
from unittest.mock import AsyncMock
from src.advisory.crypto_market_watch import CryptoMarketWatchEngine

@pytest.mark.asyncio
async def test_get_top_movers():
    mcp = AsyncMock()
    mcp.get_quote.side_effect = lambda symbol, market: {
        "price": 100.0,
        "change_pct": 5.0 if symbol == "BTCUSDT" else -2.0 if symbol == "ETHUSDT" else 0.1,
        "funding_rate": 0.0001,
        "open_interest": 1000000.0
    }
    
    movers = await CryptoMarketWatchEngine.get_top_movers(mcp, n=2)
    assert len(movers) == 2
    assert movers[0]["symbol"] == "BTCUSDT"
    assert movers[0]["change_24h_pct"] == 5.0
    assert movers[1]["symbol"] == "ETHUSDT"

@pytest.mark.asyncio
async def test_get_funding_alerts():
    mcp = AsyncMock()
    mcp.get_quote.side_effect = lambda symbol, market: {
        "price": 100.0,
        "change_pct": 0.0,
        "funding_rate": 0.0006 if symbol == "BTCUSDT" else -0.0008 if symbol == "ETHUSDT" else 0.0001,
        "open_interest": 1000000.0
    }
    
    alerts = await CryptoMarketWatchEngine.get_funding_alerts(mcp, threshold=0.0005)
    assert len(alerts) == 2
    assert alerts[0]["symbol"] == "BTCUSDT"
    assert alerts[0]["funding_rate"] == 0.0006
    assert alerts[1]["symbol"] == "ETHUSDT"
    assert alerts[1]["funding_rate"] == -0.0008

@pytest.mark.asyncio
async def test_get_market_snapshot():
    mcp = AsyncMock()
    mcp.get_quote.return_value = {
        "price": 50000.0,
        "change_pct": 2.5,
        "funding_rate": 0.0002,
        "open_interest": 5000000.0
    }
    mcp.get_ohlcv.return_value = [{"open": 50000, "high": 51000, "low": 49000, "close": 50500, "volume": 1000}] * 60
    
    snapshot = await CryptoMarketWatchEngine.get_market_snapshot(mcp, "BTCUSDT")
    assert snapshot["ticker"] == "BTCUSDT"
    assert snapshot["quote"]["last_price"] == 50000.0
    assert snapshot["funding"] == 0.0002
    assert "ta" in snapshot
    assert "rsi" in snapshot["ta"]
