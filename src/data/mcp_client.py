"""Karsa Trading System - TradingView MCP HTTP Client"""

from datetime import datetime

import httpx

from src.config import settings
from src.data.cache import CacheManager
from src.utils.logging import get_logger

logger = get_logger("mcp_client")


class MCPClient:
    """HTTP client for TradingView MCP server."""

    def __init__(self, cache: CacheManager):
        self.base_url = settings.TRADINGVIEW_MCP_URL
        self.cache = cache
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.client.aclose()

    async def _call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call an MCP tool via HTTP."""
        try:
            response = await self.client.post(
                f"{self.base_url}/tools/{tool_name}",
                json=arguments,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("mcp_tool_error", tool=tool_name, status=e.response.status_code)
            raise
        except Exception as e:
            logger.error("mcp_connection_error", tool=tool_name, error=str(e))
            raise

    async def get_quote(self, ticker: str, market: str) -> dict:
        """Get real-time quote with caching (60s TTL)."""
        cached = await self.cache.get_quote(ticker, market)
        if cached:
            return cached

        symbol = self._format_symbol(ticker, market)
        result = await self._call_tool("get_quote", {"symbol": symbol})

        quote = {
            "ticker": ticker,
            "market": market,
            "price": float(result.get("price", 0)),
            "change": float(result.get("change", 0)),
            "change_pct": float(result.get("change_pct", 0)),
            "volume": int(result.get("volume", 0)),
            "timestamp": datetime.utcnow().isoformat(),
        }

        await self.cache.set_quote(ticker, market, quote)
        return quote

    async def get_ohlcv(self, ticker: str, market: str, timeframe: str = "1D", limit: int = 100) -> list[dict]:
        """Get OHLCV candle data with caching (1h TTL)."""
        cached = await self.cache.get_ohlcv(ticker, market, timeframe)
        if cached:
            return cached

        symbol = self._format_symbol(ticker, market)
        result = await self._call_tool("get_ohlcv", {
            "symbol": symbol,
            "interval": timeframe,
            "limit": limit,
        })

        candles = [
            {
                "timestamp": c["timestamp"],
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": int(c["volume"]),
            }
            for c in result.get("candles", [])
        ]

        await self.cache.set_ohlcv(ticker, market, timeframe, candles)
        return candles

    async def get_technical(self, ticker: str, market: str, indicator: str, params: dict | None = None) -> dict:
        """Get technical indicator data."""
        symbol = self._format_symbol(ticker, market)
        arguments = {"symbol": symbol, "indicator": indicator}
        if params:
            arguments.update(params)
        return await self._call_tool("get_technical", arguments)

    async def get_rsi(self, ticker: str, market: str, period: int = 14) -> float:
        result = await self.get_technical(ticker, market, "RSI", {"period": period})
        return float(result.get("value", 50))

    async def get_bollinger(self, ticker: str, market: str, period: int = 20, std_dev: float = 2.0) -> dict:
        result = await self.get_technical(ticker, market, "BB", {"period": period, "std_dev": std_dev})
        return {
            "upper": float(result.get("upper", 0)),
            "middle": float(result.get("middle", 0)),
            "lower": float(result.get("lower", 0)),
        }

    async def get_ema(self, ticker: str, market: str, period: int) -> float:
        result = await self.get_technical(ticker, market, "EMA", {"period": period})
        return float(result.get("value", 0))

    def _format_symbol(self, ticker: str, market: str) -> str:
        """Format ticker symbol for TradingView."""
        if market == "IDX":
            return f"{ticker}.JK"
        return ticker
