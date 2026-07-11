"""Karsa Trading System — High-Frequency Risk Monitor (P0)

Decoupled from the 15-minute scan loop. Runs every 5 seconds to monitor:
- Global equity drop (Max Drawdown)
- Proximity to liquidation prices for open positions
- Immediate emergency market-close if thresholds breached

This is the last line of defense — it operates independently of the scan loop.
"""

import asyncio
import time
from src.utils.logging import get_logger

logger = get_logger("risk_monitor")

# Redis keys
REDIS_PEAK_EQUITY = "karsa:auto:peak_equity"
REDIS_MAX_DD = "karsa:auto:max_dd"
REDIS_MONITOR_ACTIVE = "karsa:risk_monitor:active"

# Defaults
DEFAULT_MAX_DD_PCT = 10.0
DEFAULT_LIQ_PROXIMITY_PCT = 15.0  # warn if within 15% of liquidation
CHECK_INTERVAL_SEC = 5


class HighFrequencyRiskMonitor:
    """Decoupled risk monitor — runs independently of scan loop.

    Monitors equity drawdown and liquidation proximity every 5 seconds.
    Triggers emergency halt + market close if thresholds breached.
    """

    def __init__(self, orchestrator, redis, bybit_client, chat_id: int = 0):
        self.orchestrator = orchestrator
        self.redis = redis
        self.bybit = bybit_client
        self.chat_id = chat_id
        self._running = False

    async def start(self):
        """Start the risk monitor loop."""
        if self._running:
            return
        self._running = True
        await self.redis.set(REDIS_MONITOR_ACTIVE, "1")
        logger.info("risk_monitor_started")
        asyncio.create_task(self._loop())

    async def stop(self):
        """Stop the risk monitor loop."""
        self._running = False
        await self.redis.delete(REDIS_MONITOR_ACTIVE)
        logger.info("risk_monitor_stopped")

    async def _loop(self):
        """Main monitoring loop — runs every 5 seconds."""
        while self._running:
            try:
                await self._check_drawdown()
                await self._check_liquidation_proximity()
            except Exception as e:
                logger.error("risk_monitor_check_failed", error=str(e))
            await asyncio.sleep(CHECK_INTERVAL_SEC)

    async def _check_drawdown(self):
        """Check if equity drawdown exceeds threshold."""
        try:
            peak = float(await self.redis.get(REDIS_PEAK_EQUITY) or 0)
            if peak <= 0:
                return

            wallet = await self.bybit.get_wallet_balance()
            current_eq = float(wallet.get("total_equity", wallet.get("balance", 0)))
            if current_eq <= 0:
                return

            dd_pct = (peak - current_eq) / peak * 100

            # Update peak if new high
            if current_eq > peak:
                await self.redis.set(REDIS_PEAK_EQUITY, str(current_eq))
                return

            # Get threshold from session config
            try:
                import json
                config_raw = await self.redis.get("karsa:auto:config")
                config = json.loads(config_raw) if config_raw else {}
                max_dd_pct = config.get("max_dd_pct", DEFAULT_MAX_DD_PCT)
            except Exception:
                max_dd_pct = DEFAULT_MAX_DD_PCT

            if dd_pct >= max_dd_pct:
                logger.warning("risk_monitor_dd_breach", dd_pct=round(dd_pct, 2), limit=max_dd_pct)
                await self._trigger_emergency(f"Max DD breached ({dd_pct:.1f}% ≥ {max_dd_pct}%)")

        except Exception as e:
            logger.debug("risk_monitor_dd_check_failed", error=str(e))

    async def _check_liquidation_proximity(self):
        """Check if any position is near liquidation price."""
        try:
            positions = await self.bybit.get_positions()
            for pos in (positions or []):
                size = float(pos.get("size", 0) or 0)
                if size <= 0:
                    continue

                mark = float(pos.get("current_price", 0) or 0)
                liq = float(pos.get("liq_price", 0) or 0)
                if mark <= 0 or liq <= 0:
                    continue

                # Calculate proximity to liquidation
                if pos.get("side") == "Buy":
                    # LONG: liquidation is below current price
                    proximity_pct = ((mark - liq) / mark * 100) if mark > 0 else 100
                else:
                    # SHORT: liquidation is above current price
                    proximity_pct = ((liq - mark) / mark * 100) if mark > 0 else 100

                if proximity_pct <= DEFAULT_LIQ_PROXIMITY_PCT and proximity_pct > 0:
                    symbol = pos.get("symbol", "?")
                    logger.warning(
                        "risk_monitor_liq_proximity",
                        symbol=symbol,
                        proximity_pct=round(proximity_pct, 2),
                    )
                    await self._trigger_emergency(
                        f"Liquidation proximity: {symbol} within {proximity_pct:.1f}% of liq price"
                    )
                    return  # One emergency at a time

        except Exception as e:
            logger.debug("risk_monitor_liq_check_failed", error=str(e))

    async def _trigger_emergency(self, reason: str):
        """Trigger emergency halt and close all positions."""
        try:
            # Set halt state
            from src.risk.emergency import activate_global_halt
            await activate_global_halt(reason=f"Risk Monitor: {reason}", operator="risk_monitor")

            # Close all positions
            from src.risk.sor import SmartOrderRouter
            sor = SmartOrderRouter(self.bybit)
            await sor.flatten_all()

            # Notify via Telegram (force=True — emergency always reaches user)
            if self.chat_id:
                try:
                    from src.main_crypto import telegram_app
                    from src.notifications.router import NotificationRouter, NotificationCategory
                    if telegram_app and telegram_app.bot:
                        notifier = NotificationRouter(telegram_app.bot, self.chat_id)
                        await notifier.send(
                            f"🚨 <b>RISK MONITOR EMERGENCY</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"Reason: {reason}\n"
                            f"All positions closed. Global halt active.\n"
                            f"Use /clear_halt to reset after review.",
                            NotificationCategory.RISK_ALERT,
                            force=True,
                        )
                except Exception:
                    pass

            logger.warning("risk_monitor_emergency_triggered", reason=reason)

        except Exception as e:
            logger.error("risk_monitor_emergency_failed", error=str(e))
