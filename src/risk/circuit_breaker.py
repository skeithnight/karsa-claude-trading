"""Karsa Trading System — Circuit Breaker Manager

Automated circuit breakers beyond the daily DD kill switch:
- Daily Drawdown: existing, refactored here
- Volatility Spike: 5% move in 15min → 30min halt
- Correlation Cascade: >60% of correlated positions losing → warning

Flow:
  Scheduler calls check_all() every 1 min →
  Each breaker checks conditions →
  If triggered: set Redis key with TTL, log to crypto_circuit_breaker_events.
"""

import asyncio
import json
from datetime import datetime, timezone

from src.risk import emergency
from src.metrics.crypto_metrics import update_circuit_breaker, VOLATILITY_SPIKE_PCT as VOL_SPIKE_METRIC
from src.models.database import async_session
from src.models.tables import CryptoCircuitBreakerEvent, ClosedPaperTrade
from src.risk.crypto_risk_manager import CORRELATION_TIERS
from src.utils.logging import get_logger
from sqlalchemy import select, func, cast, Date

logger = get_logger("circuit_breaker")

# Redis keys for circuit breakers
CB_KEY_PREFIX = "karsa:circuit_breaker"
CB_TTL_SEC = 1800  # 30 min auto-expiry

# Thresholds
VOL_SPIKE_PCT = 5.0        # 5% in 15 min
VOL_SPIKE_LOOKBACK = 15     # minutes
CORRELATION_CASCADE_PCT = 0.6  # 60% of correlated positions losing


class CircuitBreakerManager:
    """Automated circuit breakers for crypto trading."""

    def __init__(self, redis_client, bybit):
        self._redis = redis_client
        self.bybit = bybit

    async def check_all(self) -> list[dict]:
        """Run all circuit breakers. Returns list of triggered events."""
        events = []

        # 1. Daily drawdown (refactored from _job_kill_switch)
        dd_event = await self.check_daily_drawdown()
        if dd_event:
            events.append(dd_event)

        # 2. Volatility spike (top 3 liquid pairs)
        for ticker in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
            vol_event = await self.check_volatility_spike(ticker)
            if vol_event:
                events.append(vol_event)

        # 3. Correlation cascade
        corr_event = await self.check_correlation_cascade()
        if corr_event:
            events.append(corr_event)

        return events

    async def check_daily_drawdown(self) -> dict | None:
        """Check if daily realized loss exceeds limit.

        Refactored from _job_kill_switch in main.py.
        """
        try:
            async with async_session() as session:
                today = datetime.now(timezone.utc).date()
                result = await session.execute(
                    select(func.sum(ClosedPaperTrade.realized_pnl_pct))
                    .where(cast(ClosedPaperTrade.exit_date, Date) == today)
                )
                daily_pnl_pct = result.scalar() or 0.0

                from src.config import settings
                limit = settings.CRYPTO_DAILY_LOSS_LIMIT_PCT

                if daily_pnl_pct <= -limit:
                    # Already handled by emergency.activate() — just log
                    already_active = await self._is_breaker_active("DAILY_DD")
                    if not already_active:
                        await self._activate_breaker("DAILY_DD", "HALT", {
                            "daily_pnl_pct": round(float(daily_pnl_pct), 2),
                            "limit_pct": limit,
                        })
                        return {
                            "breaker": "DAILY_DD",
                            "severity": "HALT",
                            "daily_pnl_pct": round(float(daily_pnl_pct), 2),
                        }
        except Exception as e:
            logger.error("daily_dd_check_failed", error=str(e))
        return None

    async def check_volatility_spike(self, ticker: str) -> dict | None:
        """Check for extreme volatility spike (>5% in 15 min)."""
        try:
            already_active = await self._is_breaker_active(f"VOLATILITY:{ticker}")
            if already_active:
                return None

            # Fetch 1-min klines for last 15 min
            resp = await asyncio.to_thread(
                self.bybit._http_client.get_kline,
                category="linear",
                symbol=ticker,
                interval="1",
                limit=VOL_SPIKE_LOOKBACK,
            )
            if resp.get("retCode") != 0:
                return None

            klines = resp.get("result", {}).get("list", [])
            if len(klines) < 2:
                return None

            # Calculate max move in window
            high_prices = [float(k[2]) for k in klines]
            low_prices = [float(k[3]) for k in klines]

            max_high = max(high_prices)
            min_low = min(low_prices)
            if min_low <= 0:
                return None

            move_pct = ((max_high - min_low) / min_low) * 100

            # Always report current volatility to Prometheus
            VOL_SPIKE_METRIC.labels(ticker=ticker).set(round(move_pct, 2))

            if move_pct >= VOL_SPIKE_PCT:
                await self._activate_breaker(f"VOLATILITY:{ticker}", "WARNING", {
                    "ticker": ticker,
                    "move_pct": round(move_pct, 2),
                    "lookback_min": VOL_SPIKE_LOOKBACK,
                    "high": max_high,
                    "low": min_low,
                })
                logger.warning("volatility_spike", ticker=ticker, move_pct=round(move_pct, 2))
                return {
                    "breaker": "VOLATILITY",
                    "severity": "WARNING",
                    "ticker": ticker,
                    "move_pct": round(move_pct, 2),
                }
        except Exception as e:
            logger.error("vol_spike_check_failed", ticker=ticker, error=str(e))
        return None

    async def check_correlation_cascade(self) -> dict | None:
        """Check if >60% of positions in same correlation tier are losing."""
        try:
            already_active = await self._is_breaker_active("CORRELATION")
            if already_active:
                return None

            # Fetch open positions
            resp = await asyncio.to_thread(
                self.bybit._http_client.get_positions,
                category="linear",
                settleCoin="USDT",
            )
            if resp.get("retCode") != 0:
                return None

            positions = [p for p in resp.get("result", {}).get("list", [])
                         if float(p.get("size", 0)) > 0]
            if len(positions) < 2:
                return None

            # Group by tier
            tier_positions: dict[str, list] = {}
            for pos in positions:
                symbol = pos.get("symbol", "")
                for tier_name, tier in CORRELATION_TIERS.items():
                    if symbol in tier["symbols"]:
                        tier_positions.setdefault(tier_name, []).append(pos)
                        break

            # Check each tier
            for tier_name, tier_pos_list in tier_positions.items():
                if len(tier_pos_list) < 2:
                    continue

                losing = sum(1 for p in tier_pos_list
                             if float(p.get("unrealized_pnl", 0)) < 0)
                loss_ratio = losing / len(tier_pos_list)

                if loss_ratio >= CORRELATION_CASCADE_PCT:
                    await self._activate_breaker("CORRELATION", "WARNING", {
                        "tier": tier_name,
                        "total_positions": len(tier_pos_list),
                        "losing_positions": losing,
                        "loss_ratio": round(loss_ratio, 2),
                    })
                    logger.warning("correlation_cascade",
                                   tier=tier_name,
                                   losing=losing,
                                   total=len(tier_pos_list))
                    return {
                        "breaker": "CORRELATION",
                        "severity": "WARNING",
                        "tier": tier_name,
                        "loss_ratio": round(loss_ratio, 2),
                    }
        except Exception as e:
            logger.error("correlation_check_failed", error=str(e))
        return None

    async def _activate_breaker(self, breaker_type: str, severity: str, details: dict) -> None:
        """Activate a circuit breaker — set Redis key with TTL and log to DB."""
        # Update Prometheus gauge (strip ticker suffix for label)
        update_circuit_breaker(breaker_type.split(":")[0], True)
        try:
            from src.metrics.crypto_metrics import update_risk_status
            update_risk_status(cb_active=True)
        except Exception:
            pass

        # Set Redis key with TTL
        if self._redis:
            try:
                key = f"{CB_KEY_PREFIX}:{breaker_type}"
                await self._redis.setex(key, CB_TTL_SEC, json.dumps(details))
            except Exception:
                pass

        # Log to DB
        try:
            async with async_session() as session:
                session.add(CryptoCircuitBreakerEvent(
                    breaker_type=breaker_type.split(":")[0],  # remove ticker suffix
                    severity=severity,
                    details=details,
                ))
                await session.commit()
        except Exception as e:
            logger.error("cb_log_failed", breaker_type=breaker_type, error=str(e))

    async def _is_breaker_active(self, breaker_type: str) -> bool:
        """Check if a circuit breaker is currently active."""
        if not self._redis:
            return False
        try:
            key = f"{CB_KEY_PREFIX}:{breaker_type}"
            val = await self._redis.get(key)
            return val is not None
        except Exception:
            return False  # fail-open

    async def get_active_breakers(self) -> list[dict]:
        """Get all currently active circuit breakers."""
        if not self._redis:
            return []
        try:
            pattern = f"{CB_KEY_PREFIX}:*"
            keys = []
            async for key in self._redis.scan_iter(match=pattern):
                keys.append(key)

            breakers = []
            for key in keys:
                val = await self._redis.get(key)
                if val:
                    breaker_type = key.replace(f"{CB_KEY_PREFIX}:", "")
                    breakers.append({
                        "type": breaker_type,
                        "details": json.loads(val),
                        "ttl": await self._redis.ttl(key),
                    })
            return breakers
        except Exception:
            return []
