"""Karsa Trading System - Market Data Client

Data sources:
- IDX stocks: tradingview_ta (screener='indonesia', exchange='IDX')
- US stocks/ETFs: tradingview_ta (screener='america', exchange='NASDAQ'/'NYSE')
- Technical indicators: tradingview_ta (RSI, MACD, BB, EMA, SMA)
"""

from datetime import datetime

from src.data.cache import CacheManager
from src.utils.logging import get_logger

logger = get_logger("mcp_client")

_ta_handler = None

SCREENER_MAP = {"IDX": "indonesia", "US": "america", "ETF": "america"}
EXCHANGE_MAP = {"IDX": "IDX", "US": "NASDAQ", "ETF": "AMEX"}


def _ensure_imports():
    global _ta_handler
    if _ta_handler is None:
        from tradingview_ta import TA_Handler, Interval
        _ta_handler = (TA_Handler, Interval)


class MCPClient:
    """Market data client using tradingview_ta."""

    def __init__(self, cache: CacheManager):
        self.cache = cache

    async def close(self):
        pass

    def _get_ta(self, ticker: str, market: str, timeframe: str = "1D"):
        _ensure_imports()
        TA_Handler, Interval = _ta_handler
        interval_map = {
            "1m": Interval.INTERVAL_1_MINUTE, "5m": Interval.INTERVAL_5_MINUTES,
            "15m": Interval.INTERVAL_15_MINUTES, "1h": Interval.INTERVAL_1_HOUR,
            "4h": Interval.INTERVAL_4_HOURS, "1D": Interval.INTERVAL_1_DAY,
            "1W": Interval.INTERVAL_1_WEEK,
        }
        screener = SCREENER_MAP.get(market, "america")
        exchanges = [EXCHANGE_MAP.get(market, "NASDAQ")]
        # For US/ETF: try primary exchange, then fallbacks
        if market in ("US", "ETF"):
            exchanges = ["NASDAQ", "NYSE", "AMEX"]
        elif market == "ETF":
            exchanges = ["AMEX", "NYSE", "NASDAQ"]

        last_err = None
        for ex in exchanges:
            try:
                return TA_Handler(
                    symbol=ticker, screener=screener, exchange=ex,
                    interval=interval_map.get(timeframe, Interval.INTERVAL_1_DAY),
                ).get_analysis()
            except Exception as e:
                last_err = e
                continue
        raise last_err or Exception("No exchange found")

    async def get_quote(self, ticker: str, market: str) -> dict:
        cached = await self.cache.get_quote(ticker, market)
        if cached:
            return cached
        try:
            analysis = self._get_ta(ticker, market, "1D")
            ind = analysis.indicators
            price = float(ind.get("close", 0))
            prev = float(ind.get("open", price))
            change = round(price - prev, 2)
            pct = round(change / prev * 100, 2) if prev else 0
            quote = {
                "ticker": ticker, "market": market, "price": price,
                "change": change, "change_pct": pct,
                "volume": int(ind.get("volume", 0) or 0),
                "open": float(ind.get("open", 0)), "high": float(ind.get("high", 0)),
                "low": float(ind.get("low", 0)), "timestamp": datetime.utcnow().isoformat(),
            }
            await self.cache.set_quote(ticker, market, quote)
            return quote
        except Exception as e:
            logger.error("get_quote_failed", ticker=ticker, market=market, error=str(e))
            return {"ticker": ticker, "market": market, "price": 0, "error": str(e)}

    async def get_ohlcv(self, ticker: str, market: str, timeframe: str = "1D", limit: int = 100) -> list[dict]:
        cached = await self.cache.get_ohlcv(ticker, market, timeframe)
        if cached:
            return cached
        try:
            analysis = self._get_ta(ticker, market, timeframe)
            ind = analysis.indicators
            candle = {
                "timestamp": datetime.utcnow().isoformat(),
                "open": float(ind.get("open", 0)), "high": float(ind.get("high", 0)),
                "low": float(ind.get("low", 0)), "close": float(ind.get("close", 0)),
                "volume": int(ind.get("volume", 0) or 0),
            }
            await self.cache.set_ohlcv(ticker, market, timeframe, [candle])
            return [candle]
        except Exception as e:
            logger.error("get_ohlcv_failed", ticker=ticker, market=market, error=str(e))
            return []

    async def get_technical(self, ticker: str, market: str, indicator: str, params: dict | None = None) -> dict:
        try:
            analysis = self._get_ta(ticker, market, "1D")
            return {"indicators": analysis.indicators, "ticker": ticker, "market": market}
        except Exception as e:
            logger.error("get_technical_failed", ticker=ticker, error=str(e))
            return {"indicators": {}, "error": str(e)}

    async def get_rsi(self, ticker: str, market: str, period: int = 14) -> float:
        r = await self.get_technical(ticker, market, "RSI")
        return float(r.get("indicators", {}).get("RSI", 50))

    async def get_bollinger(self, ticker: str, market: str, period: int = 20, std_dev: float = 2.0) -> dict:
        r = await self.get_technical(ticker, market, "BB")
        ind = r.get("indicators", {})
        return {"upper": float(ind.get("BB.upper", 0)), "middle": float(ind.get("BB.middle", 0)), "lower": float(ind.get("BB.lower", 0))}

    async def get_ema(self, ticker: str, market: str, period: int) -> float:
        r = await self.get_technical(ticker, market, "EMA")
        return float(r.get("indicators", {}).get(f"EMA{period}", 0))
