"""Karsa Trading System - Risk Profile Manager

Three predefined risk profiles with runtime switching via Redis.
All deterministic — no LLM involvement.
"""

import json
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timezone

from src.utils.logging import get_logger

logger = get_logger("risk_profile")

REDIS_KEY = "karsa:state:risk_profile"
REDIS_AUDIT_KEY = "karsa:audit:risk_profile_changes"
REDIS_COOLDOWN_PREFIX = "karsa:cooldown:profile_change"
PROFILE_CHANGE_COOLDOWN_SECONDS = 300  # 5 minutes

# Hard limits — cannot be overridden by any profile
HARD_MAX_POSITION_SIZE_PCT = 0.10  # 10% absolute maximum
HARD_MAX_DAILY_LOSS_PCT = 0.05    # 5% daily loss limit


class RiskProfile(Enum):
    CONSERVATIVE = "conservative"
    SEMI_AGGRESSIVE = "semi_aggressive"
    AGGRESSIVE = "aggressive"


@dataclass
class RiskProfileConfig:
    name: str
    emoji: str
    min_confidence: int          # minimum LLM confidence to accept signal
    max_position_size_pct: float # % of equity per position
    stop_loss_atr_mult: float    # ATR multiplier for stop loss
    take_profit_atr_mult: float  # ATR multiplier for take profit
    max_open_positions: int      # max concurrent positions
    max_daily_trades: int        # max trades per day
    max_correlation: float       # max correlation between open positions
    min_volume_24h_usd: float    # minimum 24h volume in USD
    regime_veto_strictness: str  # "strict", "moderate", "loose"
    size_multiplier: float       # applied on top of base position sizing


PROFILES: dict[RiskProfile, RiskProfileConfig] = {
    RiskProfile.CONSERVATIVE: RiskProfileConfig(
        name="conservative",
        emoji="\U0001f6e1️",  # shield
        min_confidence=70,
        max_position_size_pct=0.01,
        stop_loss_atr_mult=1.0,
        take_profit_atr_mult=2.0,
        max_open_positions=2,
        max_daily_trades=3,
        max_correlation=0.7,
        min_volume_24h_usd=100_000_000,
        regime_veto_strictness="strict",
        size_multiplier=0.8,
    ),
    RiskProfile.SEMI_AGGRESSIVE: RiskProfileConfig(
        name="semi_aggressive",
        emoji="⚖️",  # balance scale
        min_confidence=50,
        max_position_size_pct=0.025,
        stop_loss_atr_mult=1.5,
        take_profit_atr_mult=3.0,
        max_open_positions=4,
        max_daily_trades=8,
        max_correlation=0.85,
        min_volume_24h_usd=50_000_000,
        regime_veto_strictness="moderate",
        size_multiplier=1.0,
    ),
    RiskProfile.AGGRESSIVE: RiskProfileConfig(
        name="aggressive",
        emoji="\U0001f525",  # fire
        min_confidence=35,
        max_position_size_pct=0.05,
        stop_loss_atr_mult=2.5,
        take_profit_atr_mult=4.0,
        max_open_positions=6,
        max_daily_trades=15,
        max_correlation=0.95,
        min_volume_24h_usd=20_000_000,
        regime_veto_strictness="loose",
        size_multiplier=1.3,
    ),
}


class RiskProfileManager:
    """Manages risk profile state and validation. Redis-backed, thread-safe."""

    def __init__(self, redis_client):
        self._redis = redis_client
        # ponytail: defer to first async call — avoids coroutine-never-awaited warning

    async def ensure_default(self):
        """Set Conservative as default if no profile exists. Call once after init."""
        try:
            if not await self._redis.exists(REDIS_KEY):
                await self._redis.set(REDIS_KEY, RiskProfile.CONSERVATIVE.value)
        except Exception as e:
            logger.warning("profile_init_failed", error=str(e))

    async def get_active_profile(self) -> RiskProfileConfig:
        """Read active profile from Redis. Falls back to Conservative."""
        try:
            raw = await self._redis.get(REDIS_KEY)
            if raw:
                profile = RiskProfile(raw if isinstance(raw, str) else raw.decode())
                return PROFILES[profile]
        except Exception as e:
            logger.warning("profile_read_failed", error=str(e))
        return PROFILES[RiskProfile.CONSERVATIVE]

    async def get_active_profile_name(self) -> str:
        """Return the active profile's enum value string."""
        return (await self.get_active_profile()).name

    async def set_profile(
        self, profile: RiskProfile, changed_by: str, reason: str = ""
    ) -> bool:
        """Atomically update profile. Logs to audit trail. Returns False on cooldown."""
        # Cooldown check
        cooldown_key = f"{REDIS_COOLDOWN_PREFIX}:{changed_by}"
        if await self._redis.exists(cooldown_key):
            logger.info("profile_change_cooldown", user=changed_by)
            return False

        old_config = await self.get_active_profile()
        old_name = old_config.name
        new_name = profile.value

        if old_name == new_name:
            return True  # no-op

        # Update Redis
        await self._redis.set(REDIS_KEY, new_name)
        await self._redis.setex(cooldown_key, PROFILE_CHANGE_COOLDOWN_SECONDS, "1")

        # Audit log (Redis list, keep last 100)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "previous_profile": old_name,
            "new_profile": new_name,
            "changed_by": changed_by,
            "reason": reason,
        }
        await self._redis.rpush(REDIS_AUDIT_KEY, json.dumps(entry))
        await self._redis.ltrim(REDIS_AUDIT_KEY, -100, -1)

        logger.info(
            "profile_changed",
            old=old_name,
            new=new_name,
            changed_by=changed_by,
        )

        # Record Prometheus metric
        try:
            from src.metrics.crypto_metrics import record_profile_change
            record_profile_change(old_name, new_name)
        except Exception:
            pass

        # Notify universe engine to refresh immediately
        try:
            await self._redis.publish("karsa:events:profile_changed", json.dumps({
                "old": old_name,
                "new": new_name,
                "changed_by": changed_by,
            }))
        except Exception:
            pass

        return True

    async def get_audit_log(self, limit: int = 50) -> list[dict]:
        """Return recent profile change history."""
        try:
            entries = await self._redis.lrange(REDIS_AUDIT_KEY, -limit, -1)
            return [json.loads(e) for e in entries]
        except Exception:
            return []

    async def validate_signal(
        self,
        confidence: int,
        volume_24h_usd: float = 0,
        open_position_count: int = 0,
        daily_trade_count: int = 0,
    ) -> tuple[bool, str]:
        """Validate signal parameters against active profile.

        Returns (allowed, reason).
        """
        p = await self.get_active_profile()

        if confidence < p.min_confidence:
            return False, f"Confidence {confidence} < {p.min_confidence} ({p.name})"

        if volume_24h_usd > 0 and volume_24h_usd < p.min_volume_24h_usd:
            return False, f"Volume ${volume_24h_usd:,.0f} < ${p.min_volume_24h_usd:,.0f}"

        if open_position_count >= p.max_open_positions:
            return False, f"Max {p.max_open_positions} positions reached ({p.name})"

        if daily_trade_count >= p.max_daily_trades:
            return False, f"Max {p.max_daily_trades} daily trades reached ({p.name})"

        return True, "OK"

    async def calculate_position_size(
        self,
        equity: float,
        atr: float,
        entry_price: float,
        direction: str = "LONG",
    ) -> dict:
        """Profile-aware position sizing with ATR-based stops.

        Returns dict with quantity, stop_loss, take_profit, risk_amount, rr_ratio.
        Enforces hard limits regardless of profile.
        """
        p = await self.get_active_profile()

        # Clamp position size to hard limit
        effective_pct = min(p.max_position_size_pct, HARD_MAX_POSITION_SIZE_PCT)
        risk_amount = equity * effective_pct

        stop_distance = atr * p.stop_loss_atr_mult
        tp_distance = atr * p.take_profit_atr_mult

        if direction == "LONG":
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + tp_distance
        else:
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - tp_distance

        quantity = risk_amount / stop_distance if stop_distance > 0 else 0
        rr_ratio = tp_distance / stop_distance if stop_distance > 0 else 0

        return {
            "quantity": round(quantity, 8),
            "notional_value": round(quantity * entry_price, 2),
            "stop_loss": round(stop_loss, 4),
            "take_profit": round(take_profit, 4),
            "risk_amount": round(risk_amount, 2),
            "rr_ratio": round(rr_ratio, 2),
            "profile": p.name,
            "size_multiplier": p.size_multiplier,
        }
