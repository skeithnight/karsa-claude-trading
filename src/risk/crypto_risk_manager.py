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
"""

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("crypto_risk_manager")


class CryptoRiskManager:
    """Evaluates crypto signals and calculates risk-managed position parameters."""

    def __init__(self, mcp=None, redis_client=None):
        self.mcp = mcp
        self._redis = redis_client
        self.max_risk_pct = settings.CRYPTO_MAX_RISK_PER_TRADE_PCT / 100  # 0.01
        self.max_position_pct = settings.CRYPTO_MAX_POSITION_PCT / 100    # 0.10
        self.max_concurrent = settings.CRYPTO_MAX_CONCURRENT_POSITIONS    # 5
        self.daily_loss_limit = settings.CRYPTO_DAILY_LOSS_LIMIT_PCT / 100  # 0.03

    async def evaluate(
        self,
        signal: dict,
        open_positions: list[dict],
        wallet_balance: float,
        regime: dict | None = None,
        daily_pnl_pct: float = 0.0,
    ) -> dict:
        """Evaluate a signal against risk constraints.

        Args:
            signal: Agent output dict with ticker, direction, confidence_score, entry_price, etc.
            open_positions: Current open positions from Bybit
            wallet_balance: Total USDT equity
            regime: CryptoRegimeFilter output (optional)
            daily_pnl_pct: Today's realized P&L as percentage

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

        # --- Gate 0: Basic signal validation ---
        if not entry_price or entry_price <= 0:
            return self._reject("Missing or invalid entry price")

        if confidence < 60:
            return self._reject(f"Confidence {confidence} below 60 threshold")

        if direction not in ("LONG", "SHORT"):
            return self._reject(f"Invalid direction: {direction}")

        # --- Gate 1: Daily loss limit ---
        if daily_pnl_pct <= -self.daily_loss_limit * 100:
            return self._reject(f"Daily loss limit breached: {daily_pnl_pct:+.2f}%")

        # --- Gate 2: Max concurrent positions ---
        if len(open_positions) >= self.max_concurrent:
            return self._reject(f"Max {self.max_concurrent} concurrent positions reached ({len(open_positions)} open)")

        # --- Gate 3: Already have position in this ticker ---
        existing = [p for p in open_positions if p.get("symbol") == ticker or p.get("ticker") == ticker]
        if existing:
            return self._reject(f"Already have open position in {ticker}")

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

        # Regime adjustment
        size_multiplier = 1.0
        if regime:
            size_multiplier = regime.get("size_multiplier", 1.0)
            regime_state = regime.get("state", "UNKNOWN")
            if regime_state == "CHOP" and confidence < 75:
                return self._reject(f"CHOP regime requires confidence >= 75 (got {confidence})")

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

            stop_distance = atr * 2
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
        risk_amount = wallet_balance * self.max_risk_pct * size_multiplier
        qty = risk_amount / stop_distance if stop_distance > 0 else 0

        # --- Gate 5: Max position cap ---
        position_value = qty * entry_price
        max_position_value = wallet_balance * self.max_position_pct
        if position_value > max_position_value:
            qty = max_position_value / entry_price
            position_value = max_position_value
            risk_amount = qty * stop_distance

        actual_risk_pct = (risk_amount / wallet_balance) * 100 if wallet_balance > 0 else 0

        # --- Gate 6: Minimum order size ---
        min_order_value = 5.0  # $5 minimum
        if position_value < min_order_value:
            return self._reject(f"Position value ${position_value:.2f} below minimum ${min_order_value}")

        # --- Take profit: 3:1 R/R ---
        rr_ratio = 3.0
        if direction == "LONG":
            take_profit = entry_price + (stop_distance * rr_ratio)
        else:
            take_profit = entry_price - (stop_distance * rr_ratio)

        # --- Leverage: conservative cap at 3x ---
        leverage = 1
        for candidate in [1, 2, 3]:
            required_margin = position_value / candidate
            if required_margin <= wallet_balance * 0.5:
                leverage = candidate
                break
            leverage = candidate  # last resort: max 3x

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
