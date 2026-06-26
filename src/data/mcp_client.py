"""Karsa Trading System - Market Data Client

Imports tradingview-mcp-server functions directly (no MCP protocol overhead).
For IDX stocks: uses TradingView screener via analyze_coin().
For US stocks/ETFs: uses Yahoo Finance via get_price().
"""

from datetime import datetime

from src.data.cache import CacheManager
from src.utils.logging import get_logger

logger = get_logger("mcp_client")

# Lazy imports — these come from tradingview-mcp-server package
_tv_analyze_coin = None
_tv_get_price = None
_tv_get_market_snapshot = None


def _ensure_imports():
    global _tv_analyze_coin, _tv_get_price, _tv_get_market_snapshot
    if _tv_analyze_coin is None:
        try:
            from tradingview_mcp.core.services.screener_service import analyze_coin
            from tradingview_mcp.core.services.yahoo_finance_service import get_price, get_market_snapshot
            _tv_analyze_coin = analyze_coin
            _tv_get_price = get_price
            _tv_get_market_snapshot = get_market_snapshot
            logger.info("tradingview_mcp_imported")
        except ImportError as e:
            logger.error("tradingview_mcp_import_failed", error=str(e))
            raise


class MCPClient:
    """Market data client using tradingview-mcp-server directly."""

    def __init__(self, cache: CacheManager):
        self.cache = cache

    async def close(self):
        pass  # No resources to clean up

    async def get_quote(self, ticker: str, market: str) -> dict:
        """Get real-time quote with caching (60s TTL)."""
        cached = await self.cache.get_quote(ticker, market)
        if cached:
            return cached

        try:
            _ensure_imports()
            if market == "IDX":
                result = _tv_analyze_coin(ticker, "IDXJS", "1D")
            else:
                result = _tv_get_price(ticker)

            quote = self._parse_quote(ticker, market, result)
            await self.cache.set_quote(ticker, market, quote)
            return quote
        except Exception as e:
            logger.error("get_quote_failed", ticker=ticker, market=market, error=str(e))
            return {"ticker": ticker, "market": market, "price": 0, "error": str(e)}

    async def get_ohlcv(self, ticker: str, market: str, timeframe: str = "1D", limit: int = 100) -> list[dict]:
        """Get OHLCV data from analyze_coin response."""
        cached = await self.cache.get_ohlcv(ticker, market, timeframe)
        if cached:
            return cached

        try:
            _ensure_imports()
            exchange = "IDXJS" if market == "IDX" else "NASDAQ"
            result = _tv_analyze_coin(ticker, exchange, timeframe)
            candles = self._parse_ohlcv(result)
            if candles:
                await self.cache.set_ohlcv(ticker, market, timeframe, candles)
            return candles
        except Exception as e:
            logger.error("get_ohlcv_failed", ticker=ticker, market=market, error=str(e))
            return []

    async def get_technical(self, ticker: str, market: str, indicator: str, params: dict | None = None) -> dict:
        """Get full technical analysis for a ticker."""
        _ensure_imports()
        exchange = "IDXJS" if market == "IDX" else "NASDAQ"
        return _tv_analyze_coin(ticker, exchange, "1D")

    async def get_rsi(self, ticker: str, market: str, period: int = 14) -> float:
        result = await self.get_technical(ticker, market, "RSI")
        return self._extract_indicator(result, "RSI", 50.0)

    async def get_bollinger(self, ticker: str, market: str, period: int = 20, std_dev: float = 2.0) -> dict:
        result = await self.get_technical(ticker, market, "BB")
        indicators = result.get("indicators", {})
        return {
            "upper": float(indicators.get("BB_upper", 0)),
            "middle": float(indicators.get("BB_middle", 0)),
            "lower": float(indicators.get("BB_lower", 0)),
        }

    async def get_ema(self, ticker: str, market: str, period: int) -> float:
        result = await self.get_technical(ticker, market, "EMA")
        return self._extract_indicator(result, f"EMA{period}", 0.0)

    def _parse_quote(self, ticker: str, market: str, data: dict) -> dict:
        if not data or data.get("error"):
            return {"ticker": ticker, "market": market, "price": 0, "error": str(data.get("error", "empty"))}

        # analyze_coin returns indicators dict; yahoo_price returns direct fields
        indicators = data.get("indicators", {})
        price = (
            data.get("price")
            or indicators.get("close")
            or indicators.get("last")
            or 0
        )
        return {
            "ticker": ticker,
            "market": market,
            "price": float(price),
            "change": float(data.get("change", indicators.get("change", 0)) or 0),
            "change_pct": float(data.get("change_pct", data.get("changePercent", 0)) or 0),
            "volume": int(indicators.get("volume", data.get("volume", 0)) or 0),
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _parse_ohlcv(self, data: dict) -> list[dict]:
        if not data or data.get("error"):
            return []
        indicators = data.get("indicators", {})
        if not indicators:
            return []
        return [{
            "timestamp": datetime.utcnow().isoformat(),
            "open": float(indicators.get("open", indicators.get("close", 0))),
            "high": float(indicators.get("high", indicators.get("close", 0))),
            "low": float(indicators.get("low", indicators.get("close", 0))),
            "close": float(indicators.get("close", 0)),
            "volume": int(indicators.get("volume", 0)),
        }]

    def _extract_indicator(self, data: dict, key: str, default: float) -> float:
        if not data:
            return default
        indicators = data.get("indicators", {})
        for k, v in indicators.items():
            if key.lower() in str(k).lower():
                try:
                    return float(v)
                except (ValueError, TypeError):
                    pass
        return default
