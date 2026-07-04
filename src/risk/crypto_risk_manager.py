"""Karsa Trading System - Crypto Risk Manager

Sits between signal generation and order execution.
All deterministic math — no LLM involvement.

Gates:
1. 1% portfolio risk per trade (configurable)
2. Max position size cap (10% of portfolio)
3. Max concurrent positions (5)
4. Daily loss limit (3% of portfolio)
5. Regime-adjusted sizing
6. Stop-loss + take-profit calculation (ATR-based)
7. Liquidation proximity monitoring
8. Correlation-aware position limits
9. Kill switch with account-level P&L check
"""

import time

from src.config import settings
from src.metrics.crypto_metrics import LIQ_DISTANCE_PCT, OPEN_POSITIONS
from src.utils.logging import get_logger

logger = get_logger("crypto_risk_manager")

# Correlation tiers — relaxed limits for small capital
CORRELATION_TIERS = {
    "tier1": {"symbols": {"BTCUSDT", "ETHUSDT"}, "max_positions": 2, "max_combined_pct": 0.15},
    "tier2": {"symbols": {"SOLUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT", "BNBUSDT", "NEARUSDT"}, "max_positions": 2, "max_combined_pct": 0.15},
    "tier3": {"symbols": {"DOGEUSDT", "XRPUSDT", "ADAUSDT", "PEPEUSDT", "DOTUSDT", "MATICUSDT"}, "max_positions": 2, "max_combined_pct": 0.10},
}

MAX_LEVERAGE_BY_TIER = {"tier1": 10, "tier2": 5, "tier3": 3}

REGIME_RISK_MAPPING = {
    "FULL_TREND_ALIGNMENT": {
        "min_confidence": 50.0,
        "size_multiplier": 1.0,
        "stop_loss_atr_mult": 1.5
    },
    "MACRO_BULL_MICRO_PULLBACK": {
        "min_confidence": 60.0,
        "size_multiplier": 0.8,
        "stop_loss_atr_mult": 2.0
    },
    "MACRO_BEAR_MICRO_PULLBACK": {
        "min_confidence": 60.0,
        "size_multiplier": 0.8,
        "stop_loss_atr_mult": 2.0
    },
    "MICRO_BREAKOUT_NO_MACRO": {
        "min_confidence": 75.0,
        "size_multiplier": 0.5,
        "stop_loss_atr_mult": 1.0
    },
    "PURE_DEAD_CHOP": {
        "min_confidence": 90.0,
        "size_multiplier": 0.0,
        "stop_loss_atr_mult": 1.0
    },
    "MEAN_REVERSION": {
        "min_confidence": 65.0,
        "size_multiplier": 0.8,
        "stop_loss_atr_mult": 1.5
    }
}


def _get_tier(symbol: str) -> str:
    for tier_name, tier in CORRELATION_TIERS.items():
        if symbol in tier["symbols"]:
            return tier_name
    return "tier3"


class CryptoRiskManager:
    """Evaluates crypto signals and calculates risk-managed position parameters."""

    def __init__(self, mcp=None, redis_client=None):
        self.mcp = mcp
        self._redis = redis_client
        self.max_risk_pct = settings.CRYPTO_MAX_RISK_PER_TRADE_PCT / 100  # 0.01
        self.max_position_pct = settings.CRYPTO_MAX_POSITION_PCT / 100    # 0.10
        self.max_concurrent = settings.CRYPTO_MAX_CONCURRENT_POSITIONS    # 5
        self.daily_loss_limit = settings.CRYPTO_DAILY_LOSS_LIMIT_PCT / 100  # 0.03

        # Liquidation proximity thresholds
        self.liq_warn_pct = settings.CRYPTO_LIQUIDATION_WARN_PCT / 100
        self.liq_alert_pct = settings.CRYPTO_LIQUIDATION_ALERT_PCT / 100
        self.liq_force_pct = settings.CRYPTO_LIQUIDATION_FORCE_CLOSE_PCT / 100

        # Kill switch state
        self._kill_switch_active = False
        self._kill_switch_reason = ""
        self._last_kill_check = 0.0
        self._kill_check_interval = 60

    # --- Kill Switch ---

    def is_kill_switch_active(self) -> bool:
        return self._kill_switch_active

    def activate_kill_switch(self, reason: str) -> None:
        self._kill_switch_active = True
        self._kill_switch_reason = reason
        logger.critical("crypto_kill_switch_activated", reason=reason)

    async def activate_kill_switch_redis(self, reason: str, operator: str = "risk-manager") -> None:
        """Activate kill switch and persist to Redis (survives restarts)."""
        self.activate_kill_switch(reason)
        try:
            from src.risk import emergency
            await emergency.activate(reason=reason, operator=operator)
        except Exception as e:
            logger.error("kill_switch_redis_activate_failed", error=str(e))

    def deactivate_kill_switch(self) -> None:
        self._kill_switch_active = False
        self._kill_switch_reason = ""
        logger.info("crypto_kill_switch_deactivated")

    async def deactivate_kill_switch_redis(self, operator: str = "risk-manager") -> None:
        """Deactivate kill switch and clear Redis keys."""
        self.deactivate_kill_switch()
        try:
            from src.risk import emergency
            await emergency.deactivate(operator=operator)
        except Exception as e:
            logger.error("kill_switch_redis_deactivate_failed", error=str(e))

    async def check_kill_switch(self, bybit_client) -> dict:
        """Check if kill switch should trigger based on account-level P&L.

        Checks both in-memory state and Redis-backed emergency stop.
        P&L = unrealized (from positions) — funding costs are included in
        Bybit's unrealisedPnl field for unified positions.
        """
        now = time.time()
        if now - self._last_kill_check < self._kill_check_interval:
            return {"triggered": self._kill_switch_active, "reason": self._kill_switch_reason}
        self._last_kill_check = now

        if self._kill_switch_active:
            return {"triggered": True, "reason": self._kill_switch_reason}

        # Check Redis-backed emergency stop (survives container restarts)
        try:
            from src.risk import emergency
            if await emergency.is_active():
                self._kill_switch_active = True
                self._kill_switch_reason = "Redis emergency stop is active"
                return {"triggered": True, "reason": self._kill_switch_reason}
        except Exception:
            pass  # Redis unavailable — fall through to in-memory check

        try:
            wallet = await bybit_client.get_wallet_balance()
            positions = await bybit_client.get_positions()

            account_value = wallet.get("balance", 0)
            if account_value <= 0:
                return {"triggered": False}

            total_unrealized = sum(p.get("unrealized_pnl", 0) for p in positions)
            loss_pct = abs(total_unrealized) / account_value * 100 if total_unrealized < 0 else 0

            result = {
                "triggered": False,
                "account_value": account_value,
                "unrealized_pnl": total_unrealized,
                "loss_pct": loss_pct,
            }

            if loss_pct >= self.daily_loss_limit * 100:
                await self.activate_kill_switch_redis(
                    f"Daily loss {loss_pct:.2f}% exceeds {self.daily_loss_limit*100:.1f}% limit",
                    operator="crypto-kill-switch",
                )
                result["triggered"] = True
                result["reason"] = self._kill_switch_reason

            return result

        except Exception as e:
            logger.error("kill_switch_check_failed", error=str(e))
            return {"triggered": False, "error": str(e)}

    # --- Liquidation Proximity ---

    def check_liquidation_proximity(self, position: dict) -> dict:
        """Check how close a position is to liquidation."""
        liq_price = position.get("liquidation_price")
        current_price = position.get("current_price")
        entry_price = position.get("entry_price")
        side = position.get("side", "Buy")

        if not liq_price or not current_price or not entry_price:
            return {"level": "unknown", "distance_pct": 0, "should_close": False}

        if side == "Buy":
            distance_pct = (current_price - liq_price) / entry_price * 100
        else:
            distance_pct = (liq_price - current_price) / entry_price * 100

        distance_pct = max(0, distance_pct)

        if distance_pct <= self.liq_force_pct * 100:
            level, should_close = "force_close", True
        elif distance_pct <= self.liq_alert_pct * 100:
            level, should_close = "danger", False
        elif distance_pct <= self.liq_warn_pct * 100:
            level, should_close = "warning", False
        else:
            level, should_close = "safe", False

        return {
            "level": level,
            "distance_pct": round(distance_pct, 2),
            "liquidation_price": liq_price,
            "should_close": should_close,
        }

    async def check_all_positions_health(self, bybit_client) -> list[dict]:
        """Scan all positions for liquidation proximity and excessive loss."""
        try:
            positions = await bybit_client.get_positions()
            OPEN_POSITIONS.set(len(positions) if positions else 0)
            alerts = []

            for pos in positions:
                symbol = pos.get("symbol", "")
                side = pos.get("side", "Buy")
                liq_check = self.check_liquidation_proximity(pos)

                # Update Prometheus gauges per position
                LIQ_DISTANCE_PCT.labels(ticker=symbol, side=side).set(liq_check["distance_pct"])

                entry_price = pos.get("entry_price", 0)
                size = pos.get("size", 0)
                unrealized = pos.get("unrealized_pnl", 0)
                position_value = entry_price * size if entry_price and size else 1
                loss_pct = abs(unrealized) / position_value * 100 if unrealized < 0 and position_value > 0 else 0

                if liq_check["level"] != "safe" or loss_pct > 10:
                    alerts.append({
                        "symbol": symbol,
                        "side": side,
                        "unrealized_pnl": unrealized,
                        "loss_pct": round(loss_pct, 2),
                        "liquidation": liq_check,
                    })

            return alerts

        except Exception as e:
            logger.error("position_health_check_failed", error=str(e))
            return []

    # --- Correlation-Aware Risk ---

    def check_correlation_limits(self, symbol: str, open_positions: list[dict], wallet_balance: float) -> dict:
        """Check if adding symbol violates correlation tier limits."""
        tier = _get_tier(symbol)
        tier_config = CORRELATION_TIERS[tier]
        tier_symbols = tier_config["symbols"]

        tier_positions = [p for p in open_positions if p.get("symbol") in tier_symbols]
        tier_exposure = sum(p.get("entry_price", 0) * p.get("size", 0) for p in tier_positions)
        tier_exposure_pct = tier_exposure / wallet_balance if wallet_balance > 0 else 0

        if len(tier_positions) >= tier_config["max_positions"]:
            return {
                "allowed": False,
                "reason": f"Tier {tier[-1]} max {tier_config['max_positions']} positions reached",
                "tier": tier,
            }

        if tier_exposure_pct >= tier_config["max_combined_pct"]:
            return {
                "allowed": False,
                "reason": f"Tier {tier[-1]} combined exposure {tier_exposure_pct*100:.1f}% exceeds {tier_config['max_combined_pct']*100:.0f}%",
                "tier": tier,
            }

        max_leverage = MAX_LEVERAGE_BY_TIER.get(tier, 3)
        return {
            "allowed": True,
            "tier": tier,
            "max_leverage": max_leverage,
            "current_tier_positions": len(tier_positions),
            "current_tier_exposure_pct": round(tier_exposure_pct * 100, 2),
        }

    async def evaluate(
        self,
        signal: dict,
        open_positions: list[dict],
        wallet_balance: float,
        regime: dict | None = None,
        daily_pnl_pct: float = 0.0,
        profile_config=None,
    ) -> dict:
        """Evaluate a signal against risk constraints.

        Args:
            signal: Agent output dict with ticker, direction, confidence_score, entry_price, etc.
            open_positions: Current open positions from Bybit
            wallet_balance: Total USDT equity
            regime: CryptoRegimeFilter output (optional)
            daily_pnl_pct: Today's realized P&L as percentage
            profile_config: RiskProfileConfig (optional) — overrides thresholds

        Returns:
            {
                "approved": bool,
                "reason": str,
                "qty": float,          # position size in base currency
                "stop_loss": float,
                "take_profit": float,
                "risk_amount": float,  # USD at risk
                "leverage": int,
            }
        """
        ticker = signal.get("ticker", "")
        direction = signal.get("direction", "LONG")
        confidence = signal.get("confidence_score", 0)
        entry_price = signal.get("entry_price", 0)

        # Profile-aware thresholds
        min_confidence = 60
        max_concurrent = self.max_concurrent
        max_risk_pct = self.max_risk_pct
        max_position_pct = self.max_position_pct
        # ASM override: allow full balance for position sizing
        if signal.get("_override_max_position_pct"):
            max_position_pct = signal["_override_max_position_pct"]
            logger.info("asm_override_max_position", pct=max_position_pct, ticker=signal.get("ticker"))
        sl_mult = 1.5
        tp_mult = 3.0
        if profile_config:
            min_confidence = profile_config.min_confidence
            max_concurrent = min(profile_config.max_open_positions, self.max_concurrent)
            max_risk_pct = min(profile_config.max_position_size_pct, self.max_position_pct)
            sl_mult = profile_config.stop_loss_atr_mult
            tp_mult = profile_config.take_profit_atr_mult

        # --- Gate 0: Basic signal validation ---
        if not entry_price or entry_price <= 0:
            return self._reject("Missing or invalid entry price")

        if confidence < min_confidence:
            return self._reject(f"Confidence {confidence} below {min_confidence} threshold")

        if direction not in ("LONG", "SHORT"):
            return self._reject(f"Invalid direction: {direction}")

        # --- Gate 1: Daily loss limit ---
        if daily_pnl_pct <= -self.daily_loss_limit * 100:
            return self._reject(f"Daily loss limit breached: {daily_pnl_pct:+.2f}%")

        # --- Gate 2: Max concurrent positions ---
        if len(open_positions) >= max_concurrent:
            return self._reject(f"Max {max_concurrent} concurrent positions reached ({len(open_positions)} open)")

        # --- Gate 3: Already have position in this ticker ---
        existing = [p for p in open_positions if p.get("symbol") == ticker or p.get("ticker") == ticker]
        if existing:
            return self._reject(f"Already have open position in {ticker}")

        # --- Gate 3b: Correlation tier limits ---
        corr = self.check_correlation_limits(ticker, open_positions, wallet_balance)
        if not corr.get("allowed"):
            return self._reject(corr["reason"])

        # --- Gate 4: Cooldown check ---
        try:
            if self._redis:
                cooldown = await self._redis.get("karsa:crypto_cooldown")
            else:
                import redis.asyncio as redis_mod
                r = redis_mod.from_url(settings.REDIS_URL, decode_responses=True)
                cooldown = await r.get("karsa:crypto_cooldown")
                await r.close()
            if cooldown:
                return self._reject("Cooldown active (sellall was triggered)")
        except Exception as e:
            logger.warning("cooldown_check_failed", error=str(e))
            # Fail closed on cooldown — if Redis is down, don't trade
            return self._reject("Cooldown check failed (Redis unavailable)")

        # --- Calculate position size ---
        if wallet_balance <= 0:
            return self._reject("Wallet balance is zero")

        # Regime adjustment (Contextual Regime Engine)
        size_multiplier = 1.0
        # ASM override: bypass regime multiplier entirely
        if signal.get("_override_max_position_pct"):
            size_multiplier = 1.0
            regime = None  # skip regime checks
        elif regime:
            regime_state = regime.get("state", "UNKNOWN")
            
            # Apply contextual mapping if available
            mapping = REGIME_RISK_MAPPING.get(regime_state)
            if mapping:
                req_conf = mapping["min_confidence"]
                if profile_config:
                    # Adjust regime strictness offset relative to the profile's baseline (60 is default)
                    req_conf = max(10, profile_config.min_confidence + (req_conf - 60.0))

                if confidence < req_conf:
                    return self._reject(f"Regime {regime_state} requires confidence >= {req_conf} (got {confidence})")
                
                size_multiplier = mapping["size_multiplier"]
                # Override stop-loss multiplier if profile hasn't strictly set it
                if not profile_config:
                    sl_mult = mapping["stop_loss_atr_mult"]
                
                if size_multiplier <= 0:
                    return self._reject(f"Regime {regime_state} enforces a 0x size multiplier (Do not trade)")
            else:
                # Fallback for unknown regimes
                if regime_state == "CHOP" and confidence < 75:
                    return self._reject(f"CHOP regime requires confidence >= 75 (got {confidence})")
                size_multiplier = regime.get("size_multiplier", 1.0)

        # ATR-based stop distance
        stop_loss = signal.get("stop_loss_price")
        atr = 0.0

        if stop_loss and stop_loss > 0:
            if direction == "LONG":
                stop_distance = entry_price - stop_loss
            else:
                stop_distance = stop_loss - entry_price

            if stop_distance <= 0:
                return self._reject("Stop-loss on wrong side of entry")
        else:
            # Calculate from ATR (2x ATR default)
            if self.mcp:
                try:
                    ohlcv = await self.mcp.get_ohlcv(ticker, "CRYPTO", timeframe="4h", limit=14)
                    if ohlcv and len(ohlcv) >= 2:
                        highs = [c["high"] for c in ohlcv[-14:]]
                        lows = [c["low"] for c in ohlcv[-14:]]
                        closes = [c["close"] for c in ohlcv[-14:]]
                        tr_list = []
                        for i in range(1, len(highs)):
                            tr_list.append(max(
                                highs[i] - lows[i],
                                abs(highs[i] - closes[i - 1]),
                                abs(lows[i] - closes[i - 1]),
                            ))
                        atr = sum(tr_list) / len(tr_list) if tr_list else entry_price * 0.02
                except Exception:
                    atr = entry_price * 0.02
            else:
                atr = entry_price * 0.02

            stop_distance = atr * sl_mult
            if direction == "LONG":
                stop_loss = entry_price - stop_distance
            else:
                stop_loss = entry_price + stop_distance

        # Ensure minimum stop distance (0.5% of price)
        min_stop = entry_price * 0.005
        if abs(stop_distance) < min_stop:
            stop_distance = min_stop
            if direction == "LONG":
                stop_loss = entry_price - stop_distance
            else:
                stop_loss = entry_price + stop_distance

        stop_pct = stop_distance / entry_price

        # Position size: risk_amount / stop_distance_per_unit
        risk_amount = wallet_balance * max_risk_pct * size_multiplier
        qty = risk_amount / stop_distance if stop_distance > 0 else 0

        # --- Gate 5: Max position cap ---
        position_value = qty * entry_price
        max_position_value = wallet_balance * max_position_pct
        if position_value > max_position_value:
            qty = max_position_value / entry_price
            position_value = max_position_value
            risk_amount = qty * stop_distance

        actual_risk_pct = (risk_amount / wallet_balance) * 100 if wallet_balance > 0 else 0

        # --- Gate 6: Minimum order size ---
        min_order_value = 5.0  # $5 minimum
        if position_value < min_order_value and not signal.get("_override_max_position_pct"):
            return self._reject(f"Position value ${position_value:.2f} below minimum ${min_order_value}")
        elif position_value < min_order_value and signal.get("_override_max_position_pct"):
            # ASM override: size up to $5 minimum using leverage
            target_notional = min_order_value
            leverage_for_margin = max(signal.get("_override_leverage", 1), 1)
            margin_needed = target_notional / leverage_for_margin
            if margin_needed <= wallet_balance * 0.95:
                qty = target_notional / entry_price
                position_value = target_notional
                # Validate: if qty is too small for lot rounding, reject early
                # (SOR._round_qty will floor it to zero, which is a wasted API call)
                if qty * entry_price < 0.10:  # sanity: notional must be > $0.10 after sizing
                    return self._reject(
                        f"Qty {qty:.6f} too small for lot rounding "
                        f"(notional ${qty * entry_price:.2f}, need higher leverage or skip coin)"
                    )
            # If margin doesn't fit, keep the original qty (will be rejected by Bybit)

        # --- Gate 7: Funding rate check (prevent entering crowded trades) ---
        if self.mcp:
            try:
                funding = await self.mcp.get_funding_rate(ticker)
                rate = funding.get("funding_rate", 0)
                hard_pct = settings.CRYPTO_FUNDING_HARD_REJECT_PCT / 100  # config in %, compare as decimal

                # Hard reject: extreme funding
                if direction == "LONG" and rate > hard_pct:
                    return self._reject(f"Funding rate {rate*100:.3f}% > {settings.CRYPTO_FUNDING_HARD_REJECT_PCT}% threshold (crowded long)")
                elif direction == "SHORT" and rate < -hard_pct:
                    return self._reject(f"Funding rate {rate*100:.3f}% > {settings.CRYPTO_FUNDING_HARD_REJECT_PCT}% threshold (crowded short)")

                # Premium Index check: real-time proxy for next funding direction
                # If perp trades at discount to index, next funding is guaranteed negative
                ticker_data = await self.bybit.get_ticker(ticker) if self.bybit else {}
                index_price = ticker_data.get("index_price", 0)
                mark_price = ticker_data.get("mark_price", 0)
                if index_price > 0 and mark_price > 0:
                    premium_pct = (mark_price - index_price) / index_price * 100
                    if direction == "LONG" and premium_pct < -0.1:
                        logger.info("funding_premium_negative",
                                    ticker=ticker, premium=f"{premium_pct:.3f}%",
                                    hint="perp at discount to index, next funding likely negative for longs")
                    elif direction == "SHORT" and premium_pct > 0.1:
                        logger.info("funding_premium_positive",
                                    ticker=ticker, premium=f"{premium_pct:.3f}%",
                                    hint="perp at premium to index, next funding likely positive for shorts")

                # Funding cost projection: daily cost vs ATR-based conservative target
                daily_funding_cost_pct = abs(rate) * 3 * 100  # 3 settlements/day
                # Use ATR-based target (conservative) instead of TP-based (optimistic)
                conservative_target_pct = (atr / entry_price * 100) if atr else (stop_distance / entry_price * rr_ratio * 100)
                max_drag = settings.CRYPTO_FUNDING_DRAG_MAX_PCT
                drag_ratio = daily_funding_cost_pct / conservative_target_pct * 100 if conservative_target_pct > 0 else 0

                logger.info("funding_analysis",
                            ticker=ticker, rate=f"{rate*100:.4f}%",
                            daily_cost=f"{daily_funding_cost_pct:.3f}%",
                            atr_target=f"{conservative_target_pct:.2f}%",
                            drag_ratio=f"{drag_ratio:.0f}%",
                            threshold=f"{max_drag:.0f}%")

                if conservative_target_pct > 0 and drag_ratio > max_drag:
                    return self._reject(
                        f"Funding drag {drag_ratio:.0f}% exceeds {max_drag}% of ATR target "
                        f"(daily cost {daily_funding_cost_pct:.3f}% vs target {conservative_target_pct:.2f}%)"
                    )
            except Exception:
                pass  # non-fatal — proceed without funding check

        # --- Take profit: profile-aware R/R ---
        rr_ratio = tp_mult
        if direction == "LONG":
            take_profit = entry_price + (stop_distance * rr_ratio)
        else:
            take_profit = entry_price - (stop_distance * rr_ratio)

        # --- Leverage: tier-based cap ---
        # ASM override: force leverage for small equity
        if signal.get("_override_leverage"):
            leverage = min(signal["_override_leverage"], settings.CRYPTO_MAX_LEVERAGE)
        else:
            tier = _get_tier(ticker)
            max_lev = min(MAX_LEVERAGE_BY_TIER.get(tier, 3), settings.CRYPTO_MAX_LEVERAGE)
            leverage = 1
            for candidate in range(1, max_lev + 1):
                required_margin = position_value / candidate
                if required_margin <= wallet_balance * 0.5:
                    leverage = candidate
                    break
            else:
                # ponytail: no leverage fits within 50% wallet rule — reject
                return self._reject(
                    f"Position too large: even {max_lev}x leverage needs "
                    f"${position_value / max_lev:.2f} margin "
                    f"(>{wallet_balance * 0.5:.2f} limit)",
                    ticker=ticker)

        return {
            "approved": True,
            "reason": f"Risk OK: {actual_risk_pct:.1f}% risk, {size_multiplier:.1f}x regime adj",
            "qty": round(qty, 6),
            "entry_price": entry_price,
            "stop_loss": round(stop_loss, 4),
            "take_profit": round(take_profit, 4),
            "risk_amount": round(risk_amount, 2),
            "risk_pct": round(actual_risk_pct, 2),
            "position_value": round(position_value, 2),
            "leverage": leverage,
            "rr_ratio": rr_ratio,
            "atr": round(atr, 4) if atr else None,
            "stop_pct": round(stop_pct * 100, 2),
        }

    def _reject(self, reason: str) -> dict:
        logger.info("risk_rejected", reason=reason)
        return {
            "approved": False,
            "reason": reason,
            "qty": 0,
            "stop_loss": 0,
            "take_profit": 0,
            "risk_amount": 0,
            "leverage": 1,
        }
