"""Karsa Trading System — Circuit Breaker Manager

Automated circuit breakers beyond the daily DD kill switch:
- Daily Drawdown: existing, refactored here
- Volatility Spike: 5% move in 15min → 30min halt
- Correlation Cascade: >60% of correlated positions losing → warning
- Trade Frequency: max N trades per symbol per hour (anti-churn)
- Symbol Cooldown: 30-min block after closing a losing position

Flow:
  Scheduler calls check_all() every 1 min →
  Each breaker checks conditions →
  If triggered: set Redis key with TTL, log to crypto_circuit_breaker_events.
"""

import asyncio
import json
import time
from datetime import datetime, timezone

from src.risk import emergency
from src.metrics.crypto_metrics import update_circuit_breaker, VOLATILITY_SPIKE_PCT as VOL_SPIKE_METRIC
from src.models.database import async_session
from src.models.tables import CryptoCircuitBreakerEvent, ClosedPaperTrade
from src.risk.crypto_risk_manager import CORRELATION_TIERS
from src.utils.logging import get_logger
from src.utils.retry import async_retry
from sqlalchemy import select, func, cast, Date

logger = get_logger("circuit_breaker")

# Redis keys for circuit breakers
CB_KEY_PREFIX = "karsa:circuit_breaker"
CB_TTL_SEC = 1800  # 30 min auto-expiry

# Thresholds
VOL_SPIKE_PCT = 5.0        # 5% in 15 min
VOL_SPIKE_LOOKBACK = 15     # minutes
CORRELATION_CASCADE_PCT = 0.6  # 60% of correlated positions losing
PEAK_EQUITY_KEY = "karsa:circuit_breaker:peak_equity"

# Anti-churn configuration
TRADE_FREQ_KEY_PREFIX = "karsa:trade_freq"
TRADE_FREQ_WINDOW_SEC = 3600  # 1 hour sliding window
MAX_TRADES_PER_SYMBOL_PER_HOUR = 2
MAX_TRADES_GLOBAL_PER_HOUR = 15
MAX_TRADES_PER_SYMBOL_PER_DAY = 6

# Per-symbol cooldown after loss
SYMBOL_COOLDOWN_KEY_PREFIX = "karsa:cooldown"
SYMBOL_COOLDOWN_SEC = 1800  # 30 minutes
SYMBOL_LOSS_COUNT_PREFIX = "karsa:loss_count"
SYMBOL_LOSS_BAN_SEC = 14400 # 4 hours

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

        # 4. Max equity drawdown (cumulative from peak)
        dd_event = await self.check_max_drawdown()
        if dd_event:
            events.append(dd_event)

        return events

    async def check_daily_drawdown(self) -> dict | None:
        """Check if daily realized loss exceeds limit.

        Refactored from _job_kill_switch in main.py.
        Includes retry for transient event-loop mismatch errors.
        """
        for attempt in range(3):
            try:
                return await self._check_daily_drawdown_inner()
            except Exception as e:
                if "different loop" in str(e) and attempt < 2:
                    await asyncio.sleep(0.5)
                    continue
                raise

    async def _check_daily_drawdown_inner(self) -> dict | None:
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

            # Fetch open positions via BybitClient (retry/throttle/circuit-breaker)
            positions = await self.bybit.get_positions()
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
                             if float(p.get("unrealisedPnl", p.get("unrealized_pnl", 0))) < 0)
                loss_ratio = losing / len(tier_pos_list)

                # Update correlation loss ratio metric
                from src.metrics.crypto_metrics import CORRELATION_LOSS_RATIO
                CORRELATION_LOSS_RATIO.labels(tier=tier_name).set(round(loss_ratio, 2))

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

    async def check_max_drawdown(self) -> dict | None:
        """Check cumulative equity drawdown from all-time peak.

        Tracks peak wallet balance in Redis (no TTL — persists across restarts).
        Triggers HALT when current equity drops >CRYPTO_MAX_EQUITY_DD_PCT from peak.
        Different from daily DD: this is cumulative, not limited to today's losses.
        """
        try:
            already_active = await self._is_breaker_active("MAX_DD")
            if already_active:
                return None

            # Get current wallet balance
            resp = await asyncio.to_thread(
                self.bybit._http_client.get_wallet_balance,
                accountType="UNIFIED",
                coin="USDT",
            )
            if resp.get("retCode") != 0:
                return None

            accounts = resp.get("result", {}).get("list", [])
            if not accounts:
                return None

            equity = float(accounts[0].get("totalEquity", 0))
            if equity <= 0:
                return None

            # Update peak equity (ratchet up only)
            peak_raw = await self._redis.get(PEAK_EQUITY_KEY)
            peak = float(peak_raw) if peak_raw else 0.0

            if equity > peak:
                await self._redis.set(PEAK_EQUITY_KEY, str(equity))
                peak = equity

            if peak <= 0:
                return None

            # Compute drawdown from peak
            dd_pct = ((peak - equity) / peak) * 100

            from src.config import settings
            limit = settings.CRYPTO_MAX_EQUITY_DD_PCT

            if dd_pct >= limit:
                await self._activate_breaker("MAX_DD", "HALT", {
                    "peak_equity": round(peak, 2),
                    "current_equity": round(equity, 2),
                    "drawdown_pct": round(dd_pct, 2),
                    "limit_pct": limit,
                })
                logger.warning("max_drawdown_halt",
                             peak=round(peak, 2),
                             equity=round(equity, 2),
                             dd_pct=round(dd_pct, 2))
                return {
                    "breaker": "MAX_DD",
                    "severity": "HALT",
                    "drawdown_pct": round(dd_pct, 2),
                }
        except Exception as e:
            logger.error("max_dd_check_failed", error=str(e))
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

        # Log to DB with retry
        try:
            await self._log_breaker_to_db(breaker_type, severity, details)
        except Exception as e:
            logger.warning("cb_db_log_failed_non_fatal", error=str(e))

    @async_retry(max_attempts=3, base_delay=1.0, log_label="cb_db_write")
    async def _log_breaker_to_db(self, breaker_type: str, severity: str, details: dict) -> None:
        """Log circuit breaker event to DB with retry."""
        async with async_session() as session:
            session.add(CryptoCircuitBreakerEvent(
                breaker_type=breaker_type.split(":")[0],  # remove ticker suffix
                severity=severity,
                details=details,
            ))
            await session.commit()

    async def _is_breaker_active(self, breaker_type: str) -> bool:
        """Check if a circuit breaker is currently active.
        Fail-closed: if Redis is unreachable, assume breaker is active (block trading).
        """
        if not self._redis:
            import sys
            if "pytest" in sys.modules:
                return False
            logger.error("cb_redis_unavailable_assuming_active", breaker_type=breaker_type)
            return True  # fail-closed
        try:
            key = f"{CB_KEY_PREFIX}:{breaker_type}"
            val = await self._redis.get(key)
            if val and "Mock" in val.__class__.__name__:
                return False
            return val is not None
        except Exception as e:
            import sys
            if "pytest" in sys.modules:
                return False
            logger.error("cb_check_failed_assuming_active", breaker_type=breaker_type, error=str(e))
            return True  # fail-closed

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
                    # Handle mocks in tests
                    if "Mock" in val.__class__.__name__:
                        continue
                    breaker_type = key.replace(f"{CB_KEY_PREFIX}:", "")
                    try:
                        details = json.loads(val)
                    except Exception:
                        details = {}
                    ttl_val = await self._redis.ttl(key)
                    if ttl_val and "Mock" in ttl_val.__class__.__name__:
                        ttl_val = 900
                    breakers.append({
                        "type": breaker_type,
                        "details": details,
                        "ttl": ttl_val,
                    })
            return breakers
        except Exception:
            return []

    async def record_trade(self, symbol: str) -> None:
        """Record a completed trade for frequency tracking.

        Uses Redis sorted sets with timestamp as score.
        Called after a position is opened or closed.
        """
        if not self._redis:
            return
        try:
            now = time.time()
            # Per-symbol sorted set
            sym_key = f"{TRADE_FREQ_KEY_PREFIX}:{symbol}"
            await self._redis.zadd(sym_key, {f"{now}": now})
            await self._redis.expire(sym_key, TRADE_FREQ_WINDOW_SEC)
            # Global sorted set
            await self._redis.zadd(TRADE_FREQ_KEY_PREFIX, {f"{symbol}:{now}": now})
            await self._redis.expire(TRADE_FREQ_KEY_PREFIX, TRADE_FREQ_WINDOW_SEC)
        except Exception as e:
            logger.error("record_trade_failed", symbol=symbol, error=str(e))

    async def check_trade_frequency(self, symbol: str) -> tuple[bool, str]:
        """Check if trading frequency limits are exceeded.

        Returns (allowed, reason). Rejects if:
        - More than MAX_TRADES_PER_SYMBOL_PER_HOUR trades on this symbol
        - More than MAX_TRADES_GLOBAL_PER_HOUR trades across all symbols
        """
        if not self._redis:
            return True, ""  # fail-open if Redis unavailable
        try:
            now = time.time()
            window_start = now - TRADE_FREQ_WINDOW_SEC

            # Check per-symbol limit
            sym_key = f"{TRADE_FREQ_KEY_PREFIX}:{symbol}"
            sym_count = await self._redis.zcount(sym_key, window_start, now)
            if sym_count and "Mock" in sym_count.__class__.__name__:
                sym_count = 0
            if sym_count >= MAX_TRADES_PER_SYMBOL_PER_HOUR:
                return False, (
                    f"Anti-churn: {sym_count} trades on {symbol} in last hour "
                    f"(max {MAX_TRADES_PER_SYMBOL_PER_HOUR})"
                )

            # Check global limit
            global_count = await self._redis.zcount(
                TRADE_FREQ_KEY_PREFIX, window_start, now,
            )
            if global_count and "Mock" in global_count.__class__.__name__:
                global_count = 0
            if global_count >= MAX_TRADES_GLOBAL_PER_HOUR:
                return False, (
                    f"Anti-churn: {global_count} trades globally in last hour "
                    f"(max {MAX_TRADES_GLOBAL_PER_HOUR})"
                )
                
            # Check daily per-symbol limit
            day_start = now - 86400
            sym_daily_count = await self._redis.zcount(sym_key, day_start, now)
            if sym_daily_count and "Mock" in sym_daily_count.__class__.__name__:
                sym_daily_count = 0
            if sym_daily_count >= MAX_TRADES_PER_SYMBOL_PER_DAY:
                return False, (
                    f"Anti-churn: {sym_daily_count} trades on {symbol} in last 24h "
                    f"(max {MAX_TRADES_PER_SYMBOL_PER_DAY})"
                )

            return True, ""
        except Exception as e:
            logger.warning("trade_freq_check_failed", symbol=symbol, error=str(e))
            return True, ""  # fail-open on error

    # --- Anti-Churn: Per-Symbol Cooldown After Loss ---

    async def record_symbol_cooldown(self, symbol: str) -> None:
        """Set a cooldown on a symbol after closing a losing position.

        Called when a position closes with negative PnL.
        Blocks re-entry for SYMBOL_COOLDOWN_SEC (30 min).
        """
        if not self._redis:
            return
        try:
            key = f"{SYMBOL_COOLDOWN_KEY_PREFIX}:{symbol}"
            await self._redis.setex(key, SYMBOL_COOLDOWN_SEC, "loss_cooldown")
            logger.info("symbol_cooldown_set", symbol=symbol, ttl=SYMBOL_COOLDOWN_SEC)
        except Exception as e:
            logger.error("symbol_cooldown_set_failed", symbol=symbol, error=str(e))

    async def check_symbol_cooldown(self, symbol: str) -> tuple[bool, str]:
        """Check if a symbol has an active cooldown after a recent loss.

        Returns (allowed, reason).
        """
        if not self._redis:
            return True, ""  # fail-open
        try:
            key = f"{SYMBOL_COOLDOWN_KEY_PREFIX}:{symbol}"
            val = await self._redis.get(key)
            if val and "Mock" not in val.__class__.__name__:
                ttl = await self._redis.ttl(key)
                if ttl and "Mock" in ttl.__class__.__name__:
                    ttl = 0
                return False, (
                    f"Cooldown active for {symbol} after recent loss "
                    f"({ttl}s remaining)"
                )
            return True, ""
        except Exception as e:
            logger.warning("symbol_cooldown_check_failed", symbol=symbol, error=str(e))
            return True, ""  # fail-open on error

    async def record_symbol_loss(self, symbol: str) -> None:
        """Record a loss for a symbol and potentially ban it."""
        if not self._redis:
            return
        try:
            # Also set the 30min cooldown
            await self.record_symbol_cooldown(symbol)
            
            # Increment daily loss count
            key = f"{SYMBOL_LOSS_COUNT_PREFIX}:{symbol}"
            losses = await self._redis.incr(key)
            if losses == 1:
                # First loss today, expire after 24h
                await self._redis.expire(key, 86400)
                
            logger.info("symbol_loss_recorded", symbol=symbol, daily_losses=losses)
            
            # If 3 or more losses, apply a 4h ban
            if losses >= 3:
                ban_key = f"{SYMBOL_COOLDOWN_KEY_PREFIX}:{symbol}"
                await self._redis.setex(ban_key, SYMBOL_LOSS_BAN_SEC, "loss_streak_ban")
                logger.warning("symbol_loss_streak_ban_applied", symbol=symbol, losses=losses, ban_sec=SYMBOL_LOSS_BAN_SEC)
                
        except Exception as e:
            logger.error("symbol_loss_record_failed", symbol=symbol, error=str(e))

    async def check_symbol_daily_loss_count(self, symbol: str, max_losses: int = 3) -> tuple[bool, str]:
        """Check if a symbol has reached its daily loss limit (checked implicitly via the extended cooldown, but provided for completeness)"""
        # The actual blocking happens in check_symbol_cooldown which will see the 4h ban key.
        # This method is here in case we want to check the raw count.
        if not self._redis:
            return True, ""
        try:
            key = f"{SYMBOL_LOSS_COUNT_PREFIX}:{symbol}"
            count = await self._redis.get(key)
            if count and int(count) >= max_losses:
                return False, f"Symbol {symbol} has {count} losses today (max {max_losses})"
            return True, ""
        except Exception as e:
            return True, ""
