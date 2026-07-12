"""Karsa Trading System — Funding Rate Cost Tracker

Tracks per-position funding payments (8h intervals on Bybit).
Syncs from exchange and accumulates for P&L accuracy.

Funding times: 00:00, 08:00, 16:00 UTC
"""

from datetime import datetime, timezone
import asyncio

from src.config import settings
from src.metrics.crypto_metrics import FUNDING_RATE
from src.utils.logging import get_logger

logger = get_logger("funding_tracker")

CRYPTO_UNIVERSE = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
]


class FundingTracker:
    """Tracks and alerts on funding rate costs for crypto positions."""

    def __init__(self, bybit_client, db_session_factory=None):
        self.client = bybit_client
        self._db_factory = db_session_factory
        self.alert_threshold = settings.CRYPTO_FUNDING_ALERT_THRESHOLD / 100

    async def get_current_rates(self, symbols: list[str] | None = None) -> list[dict]:
        """Get current funding rates for symbols.

        Returns: [{"symbol": str, "funding_rate": float, "funding_cost_pct": float,
                    "annualized_pct": float, "alert": bool}]
        """
        if symbols is None:
            symbols = CRYPTO_UNIVERSE

        results = []
        for symbol in symbols:
            try:
                data = await self.client.get_funding_rate(symbol)
                rate = data.get("funding_rate", 0)
                annualized = rate * 3 * 365 * 100

                results.append({
                    "symbol": symbol,
                    "funding_rate": rate,
                    "funding_cost_pct": round(rate * 100, 4),
                    "annualized_pct": round(annualized, 2),
                    "alert": abs(rate) > self.alert_threshold,
                    "funding_time": data.get("funding_time"),
                })
                FUNDING_RATE.labels(ticker=symbol).set(round(rate * 100, 4))
            except Exception as e:
                logger.warning("funding_rate_fetch_failed", symbol=symbol, error=str(e))
                results.append({"symbol": symbol, "funding_rate": 0, "error": str(e)})

        return results

    def calculate_position_funding_cost(
        self, position_value_usdt: float, funding_rate: float, leverage: int = 1
    ) -> dict:
        """Calculate funding cost for a position at current rate."""
        payment = position_value_usdt * funding_rate
        payment_pct = funding_rate * 100
        daily_cost_pct = abs(funding_rate) * 3 * 100

        return {
            "payment_usdt": round(payment, 4),
            "payment_pct": round(payment_pct, 4),
            "daily_cost_pct": round(daily_cost_pct, 4),
        }

    async def sync_funding_from_exchange(self, symbol: str, since_ts: int | None = None) -> list[dict]:
        """Fetch funding history from Bybit."""
        try:
            params = {"category": "linear", "symbol": symbol, "limit": 200}
            if since_ts:
                params["startTime"] = since_ts

            resp = await asyncio.to_thread(
                self.client._http_client.get_funding_rate_history,
                **params,
            )

            if resp.get("retCode") != 0:
                logger.warning("funding_history_failed", symbol=symbol, error=resp.get("retMsg"))
                return []

            records = []
            for item in resp.get("result", {}).get("list", []):
                records.append({
                    "symbol": symbol,
                    "funding_rate": float(item.get("fundingRate", 0)),
                    "funding_fee": float(item.get("fundingFee", 0)),
                    "position_size": float(item.get("size", 0)),
                    "side": item.get("side", ""),
                    "funded_at": datetime.fromtimestamp(
                        int(item.get("fundingRateTimestamp", 0)) / 1000, tz=timezone.utc
                    ),
                })

            return records

        except Exception as e:
            logger.error("funding_sync_failed", symbol=symbol, error=str(e))
            return []

    async def get_cumulative_funding(self, symbol: str, since_ts: int) -> float:
        """Get cumulative funding cost for a position since it was opened.

        Args:
            symbol: e.g. "BTCUSDT"
            since_ts: Position open timestamp in milliseconds

        Returns:
            Total funding fee in USDT (negative = paid, positive = received)
        """
        records = await self.sync_funding_from_exchange(symbol, since_ts=since_ts)
        return sum(r.get("funding_fee", 0) for r in records)
