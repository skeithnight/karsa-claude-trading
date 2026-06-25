"""Karsa Trading System - IDX Foreign Flow Data Adapter"""

import httpx

from src.config import settings
from src.data.cache import CacheManager
from src.utils.logging import get_logger

logger = get_logger("idx_adapter")


class IDXDataAdapter:
    """Fetches IDX-specific data not available via TradingView MCP.

    Primary data source: Stockbit/RTI API for foreign flow.
    """

    def __init__(self, cache: CacheManager):
        self.base_url = settings.IDX_DATA_API_URL
        self.api_key = settings.IDX_DATA_API_KEY
        self.cache = cache
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )

    async def close(self):
        await self.client.aclose()

    async def get_foreign_flow(self, ticker: str, days: int = 5) -> dict:
        """Get foreign net buy/sell flow for an IDX stock.

        Returns:
            {
                "ticker": "BBCA",
                "days": [...],
                "consecutive_net_buy_days": 3,
                "avg_foreign_pct": 7.2,
                "signal_strength": "strong"
            }
        """
        cached = await self.cache.get_foreign_flow(ticker)
        if cached:
            return cached

        try:
            response = await self.client.get(
                f"{self.base_url}/stocks/{ticker}/foreign-flow",
                params={"days": days},
            )
            response.raise_for_status()
            raw = response.json()
        except Exception as e:
            logger.error("idx_foreign_flow_error", ticker=ticker, error=str(e))
            raise

        daily_flows = raw.get("data", [])

        consecutive = 0
        for day in reversed(daily_flows):
            if day.get("foreign_net_buy", 0) > 0:
                consecutive += 1
            else:
                break

        avg_pct = 0.0
        if daily_flows:
            pcts = [
                abs(d.get("foreign_net_buy", 0)) / max(d.get("total_volume", 1), 1) * 100
                for d in daily_flows
            ]
            avg_pct = sum(pcts) / len(pcts)

        result = {
            "ticker": ticker,
            "days": daily_flows,
            "consecutive_net_buy_days": consecutive,
            "avg_foreign_pct": round(avg_pct, 2),
            "signal_strength": _classify_signal(consecutive, avg_pct),
        }

        await self.cache.set_foreign_flow(ticker, result, ttl=86400)
        return result

    async def get_ara_limit(self, ticker: str) -> dict:
        """Get Auto-Rejection limits for an IDX stock.

        Returns:
            {"upper": 10400, "lower": 8600, "last_close": 9500}
        """
        try:
            response = await self.client.get(
                f"{self.base_url}/stocks/{ticker}/ara-limits",
            )
            response.raise_for_status()
            data = response.json().get("data", {})
            return {
                "upper": float(data.get("upper_limit", 0)),
                "lower": float(data.get("lower_limit", 0)),
                "last_close": float(data.get("last_close", 0)),
            }
        except Exception as e:
            logger.error("idx_ara_limit_error", ticker=ticker, error=str(e))
            raise

    async def get_lot_info(self, ticker: str) -> dict:
        """Get lot size info for an IDX stock."""
        # ponytail: IDX lot size is always 100 shares, hardcoded.
        # Add dynamic lookup if BEI ever changes this per-stock.
        return {"lot_size": 100, "min_lots": 1}


def _classify_signal(consecutive_days: int, avg_pct: float) -> str:
    """Classify foreign flow signal strength."""
    if consecutive_days >= 3 and avg_pct >= 5.0:
        return "strong"
    elif consecutive_days >= 2 and avg_pct >= 3.0:
        return "moderate"
    elif consecutive_days >= 1:
        return "weak"
    return "none"
