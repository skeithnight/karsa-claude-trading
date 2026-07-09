"""Karsa Trading System — Performance Gate Engine (v2)

Two-layer position exit system:
  Layer 1: Mechanical checkpoints (no LLM, zero cost)
    - Hard fail zone: instant exit (e.g., -10% in 15min)
    - Clear win zone: hold + activate trailing stop
    - Ambiguous zone: trigger AI judge
    - Dynamic stop: AI-set or clear-win trailing stop overrides checkpoints

  Layer 2: AI Judge (LLM-based)
    - Cheap pass: compact prompt, pre-collected market data
    - If judge says HOLD but still underperforms next checkpoint:
      escalate to expensive pass with full context + reasoning trace
    - Consecutive holds >= 3 on negative positions → auto-exit

Enhancements over v1:
  - dynamic_stop_pct: AI or clear-win sets a floor; if gain drops below → EXIT
  - Drawdown-from-peak: if gain drops >3% from peak since last checkpoint → trigger AI
  - Price freshness: skip hard fail if price data >2min old (stale feed guard)
  - Consecutive hold tracking: force exit after 3 consecutive AI HOLDs on negative positions
  - Breakeven thresholds adjusted for fee coverage (+1.0% minimum)

Checkpoint schedules per bucket:
  Meme:     15min, 30min, 1h, 2h, 4h, 8h, 24h
  Standard: 1h, 4h, 12h, 24h, 72h
  Core:     4h, 24h, 72h, 168h (7d)

Flow:
  Scheduler calls PerformanceGate.evaluate_all() every 5 min →
  For each open position: determine bucket → check dynamic stop →
  find current checkpoint → Calculate gain % → classify zone →
    DYNAMIC_STOP_HIT → exit (AI or clear-win set stop, price dropped below)
    HARD_FAIL → immediate close via SOR (unless price stale)
    CLEAR_WIN → hold, set dynamic stop, advance checkpoint
    DRAWDOWN_FROM_PEAK → trigger AI judge
    AMBIGUOUS → fire PositionJudge agent → act on judgment
    CONSECUTIVE_HOLDS >= 3 → auto-exit negative positions
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from src.utils.logging import get_logger
from src.metrics.crypto_metrics import (
    update_dynamic_stop_active,
    record_drawdown_trigger,
    record_price_stale_skip,
    update_consecutive_holds,
    record_perf_gate_zone,
    record_perf_gate_exit,
)

logger = get_logger("performance_gate")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Bucket(str, Enum):
    """Position bucket — maps from portfolio_bucker categories."""
    MEME = "meme"
    STANDARD = "standard"
    CORE = "core"


class Zone(str, Enum):
    """Performance zone classification."""
    HARD_FAIL = "hard_fail"
    AMBIGUOUS = "ambiguous"
    CLEAR_WIN = "clear_win"
    NOT_YET = "not_yet"  # checkpoint not reached yet
    DYNAMIC_STOP = "dynamic_stop"  # AI/clear-win stop hit
    DRAWDOWN = "drawdown"  # drawdown from peak


class GateAction(str, Enum):
    """Action the gate recommends."""
    EXIT = "exit"
    HOLD = "hold"
    JUDGE = "judge"  # trigger AI judge
    SKIP = "skip"  # not at checkpoint yet


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Checkpoint:
    """A single performance checkpoint."""
    after_minutes: int
    min_gain_pct: float  # minimum gain to pass this checkpoint
    reason: str  # exit reason if failed


@dataclass
class GateResult:
    """Result of evaluating one position against its checkpoints."""
    position_id: int
    ticker: str
    bucket: str
    zone: str
    action: str
    gain_pct: float
    hours_held: float
    checkpoint: Checkpoint | None = None
    reason: str = ""
    escalation: bool = False  # True = re-evaluate after prior HOLD


# ---------------------------------------------------------------------------
# Checkpoint schedules per bucket
# ---------------------------------------------------------------------------

CHECKPOINTS: dict[str, list[Checkpoint]] = {
    Bucket.MEME: [
        Checkpoint(after_minutes=15,   min_gain_pct=-5.0, reason="meme_15m_crash"),
        Checkpoint(after_minutes=30,   min_gain_pct=-3.0, reason="meme_30m_bleeding"),
        Checkpoint(after_minutes=60,   min_gain_pct=-1.0, reason="meme_1h_consolidation"),
        Checkpoint(after_minutes=120,  min_gain_pct=1.0,  reason="meme_2h_weak"),
        Checkpoint(after_minutes=240,  min_gain_pct=2.0,  reason="meme_4h_dead"),
        Checkpoint(after_minutes=480,  min_gain_pct=3.0,  reason="meme_8h_stale"),
        Checkpoint(after_minutes=1440, min_gain_pct=5.0,  reason="meme_24h_underperform"),
    ],
    Bucket.STANDARD: [
        Checkpoint(after_minutes=60,   min_gain_pct=-5.0, reason="std_1h_crash"),
        Checkpoint(after_minutes=240,  min_gain_pct=-2.0, reason="std_4h_bleeding"),
        Checkpoint(after_minutes=720,  min_gain_pct=1.0,  reason="std_12h_not_profitable"),
        Checkpoint(after_minutes=1440, min_gain_pct=1.0,  reason="std_24h_weak"),
        Checkpoint(after_minutes=4320, min_gain_pct=2.0,  reason="std_72h_stale"),
    ],
    Bucket.CORE: [
        Checkpoint(after_minutes=240,   min_gain_pct=-8.0, reason="core_4h_crash"),
        Checkpoint(after_minutes=1440,  min_gain_pct=-3.0, reason="core_24h_check"),
        Checkpoint(after_minutes=4320,  min_gain_pct=1.0,  reason="core_72h_not_profitable"),
        Checkpoint(after_minutes=10080, min_gain_pct=2.0,  reason="core_7d_underperform"),
    ],
}


def get_adaptive_checkpoints(bucket: Bucket, volatility_regime: str | None = None) -> list[Checkpoint]:
    """Get checkpoint schedule adjusted for current volatility regime.

    High vol → shorter checkpoints (catch moves faster)
    Low vol → longer checkpoints (give positions more time)
    """
    base = CHECKPOINTS.get(bucket, CHECKPOINTS[Bucket.STANDARD])
    if not volatility_regime:
        return base

    # Volatility multiplier: compress/expand checkpoint timing
    if volatility_regime == "HIGH_VOL":
        multiplier = 0.7  # 30% shorter checkpoints
    elif volatility_regime == "LOW_VOL":
        multiplier = 1.3  # 30% longer checkpoints
    else:
        return base  # NORMAL_VOL — no change

    return [
        Checkpoint(
            after_minutes=max(5, int(cp.after_minutes * multiplier)),
            min_gain_pct=cp.min_gain_pct,
            reason=cp.reason,
        )
        for cp in base
    ]


# Zone boundaries
HARD_FAIL_THRESHOLD = -8.0   # gain < -8% at any checkpoint = hard fail
CLEAR_WIN_THRESHOLD = 3.0    # gain > +3% at checkpoint = clear win
DRAWDOWN_TRIGGER_PCT = 3.0   # drop > 3% from peak = trigger AI
CONSECUTIVE_HOLDS_LIMIT = 3  # force exit after 3 consecutive AI holds on negative
PRICE_STALE_SEC = 120        # price data older than 2 min → skip hard fail
CLEAR_WIN_STOP_FLOOR = 1.0   # minimum dynamic stop % on clear win (covers fees)
CLEAR_WIN_STOP_RATIO = 0.3   # lock in 30% of peak gain as dynamic stop


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def classify_bucket(signal_source: str | None) -> Bucket:
    """Map position metadata to a bucket.

    Uses signal_source to determine bucket. Default: STANDARD.
    """
    source = (signal_source or "").lower()

    if any(kw in source for kw in ("meme", "moonshot", "dex_discovery", "sniper", "alpha_wallet")):
        return Bucket.MEME

    if any(kw in source for kw in ("core", "high_conviction", "regime_aligned", "dip_buying", "accumulation")):
        return Bucket.CORE

    return Bucket.STANDARD


def get_gain_pct(entry_price: Decimal, current_price: Decimal, side: str) -> float:
    """Calculate unrealized gain percentage."""
    if entry_price == 0:
        return 0.0
    if side == "Buy":
        return float((current_price - entry_price) / entry_price * 100)
    else:
        return float((entry_price - current_price) / entry_price * 100)


def get_hours_held(opened_at: datetime) -> float:
    """Calculate hours since position was opened."""
    now = datetime.now(timezone.utc)
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    return (now - opened_at).total_seconds() / 3600


# ---------------------------------------------------------------------------
# PerformanceGate
# ---------------------------------------------------------------------------

class PerformanceGate:
    """Mechanical checkpoint evaluator.

    Evaluates positions against bucket-specific checkpoints.
    Returns actions: EXIT (hard fail/dynamic stop), HOLD (clear win),
    JUDGE (ambiguous/drawdown), or SKIP (not at checkpoint yet).
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client

    async def evaluate_all(self, positions: list) -> list[GateResult]:
        """Evaluate all open positions against their checkpoints.

        Args:
            positions: list of CryptoPosition objects (status='OPEN')

        Returns:
            list of GateResult with recommended actions
        """
        results = []
        for pos in positions:
            try:
                result = await self.evaluate(pos)
                if result and result.action != GateAction.SKIP:
                    results.append(result)
            except Exception as e:
                logger.error("gate_eval_failed", ticker=getattr(pos, 'ticker', '?'), error=str(e))
        return results

    async def evaluate(self, pos) -> GateResult | None:
        """Evaluate single position against its checkpoint schedule.

        Checks (in order):
          1. Dynamic stop — if gain < dynamic_stop_pct → EXIT
          2. Checkpoint schedule — hard fail / clear win / ambiguous
          3. Drawdown from peak — if dropped >3% from peak → trigger AI
          4. Consecutive holds — if >=3 holds and negative → auto-exit

        Args:
            pos: CryptoPosition with status='OPEN'

        Returns:
            GateResult or None if position should be skipped
        """
        ticker = pos.ticker
        entry = pos.entry_price
        current = pos.current_price
        side = pos.side
        opened_at = pos.opened_at
        signal_source = pos.signal_source

        if not entry or not current or not opened_at:
            return None

        bucket = classify_bucket(signal_source)
        # Use adaptive checkpoints based on current volatility regime
        volatility_regime = None
        try:
            if self._redis:
                vol_regime = await self._redis.get("karsa:volatility_regime")
                if vol_regime:
                    volatility_regime = vol_regime
        except Exception:
            pass
        checkpoints = get_adaptive_checkpoints(bucket, volatility_regime)
        gain_pct = get_gain_pct(entry, current, side)
        hours_held = get_hours_held(opened_at)
        minutes_held = hours_held * 60

        # --- Phase 1A: Dynamic stop check ---
        dynamic_stop = getattr(pos, 'dynamic_stop_pct', None)
        has_dynamic_stop = dynamic_stop is not None
        update_dynamic_stop_active(ticker, has_dynamic_stop)

        if has_dynamic_stop:
            dynamic_stop_float = float(dynamic_stop)
            if gain_pct <= dynamic_stop_float:
                logger.warning(
                    "dynamic_stop_hit",
                    ticker=ticker, gain_pct=round(gain_pct, 2),
                    dynamic_stop=dynamic_stop_float,
                )
                return GateResult(
                    position_id=pos.id, ticker=ticker, bucket=bucket.value,
                    zone=Zone.DYNAMIC_STOP, action=GateAction.EXIT,
                    gain_pct=gain_pct, hours_held=hours_held,
                    reason=f"dynamic stop hit: gain {gain_pct:+.1f}% <= {dynamic_stop_float}%",
                )

        # --- Phase 1D: Consecutive holds check ---
        hold_count = await self._get_consecutive_holds(pos.id)
        update_consecutive_holds(ticker, hold_count)

        if hold_count >= CONSECUTIVE_HOLDS_LIMIT and gain_pct < 0:
            logger.warning(
                "consecutive_holds_exit",
                ticker=ticker, hold_count=hold_count, gain_pct=round(gain_pct, 2),
            )
            return GateResult(
                position_id=pos.id, ticker=ticker, bucket=bucket.value,
                zone=Zone.AMBIGUOUS, action=GateAction.EXIT,
                gain_pct=gain_pct, hours_held=hours_held,
                reason=f"consecutive holds {hold_count} >= {CONSECUTIVE_HOLDS_LIMIT} on negative position",
            )

        # Find the highest checkpoint we've passed (time-wise)
        active_checkpoint = None
        for cp in checkpoints:
            if minutes_held >= cp.after_minutes:
                active_checkpoint = cp

        if active_checkpoint is None:
            return GateResult(
                position_id=pos.id, ticker=ticker, bucket=bucket.value,
                zone=Zone.NOT_YET, action=GateAction.SKIP,
                gain_pct=gain_pct, hours_held=hours_held,
            )

        # Check if already passed this checkpoint (via Redis tracking)
        if await self._checkpoint_already_passed(pos.id, active_checkpoint.after_minutes):
            return GateResult(
                position_id=pos.id, ticker=ticker, bucket=bucket.value,
                zone=Zone.NOT_YET, action=GateAction.SKIP,
                gain_pct=gain_pct, hours_held=hours_held,
            )

        # --- Phase 2A: Drawdown from peak check ---
        peak_gain = await self._get_peak_gain(pos.id)
        if peak_gain is not None and peak_gain > gain_pct:
            drawdown = peak_gain - gain_pct
            if drawdown >= DRAWDOWN_TRIGGER_PCT:
                record_drawdown_trigger(ticker)
                logger.warning(
                    "drawdown_from_peak",
                    ticker=ticker, peak=round(peak_gain, 2),
                    current=round(gain_pct, 2), drawdown=round(drawdown, 2),
                )
                return GateResult(
                    position_id=pos.id, ticker=ticker, bucket=bucket.value,
                    zone=Zone.DRAWDOWN, action=GateAction.JUDGE,
                    gain_pct=gain_pct, hours_held=hours_held,
                    checkpoint=active_checkpoint,
                    reason=f"drawdown from peak: {drawdown:+.1f}% (peak {peak_gain:+.1f}% → now {gain_pct:+.1f}%)",
                )

        # Update peak gain tracking
        await self._update_peak_gain(pos.id, gain_pct)

        # --- Phase 2B: Price freshness guard ---
        # (price_at is set by the caller in main_crypto.py; here we just check)
        price_at = getattr(pos, 'current_price_at', None)
        if price_at is not None:
            try:
                if isinstance(price_at, datetime):
                    age_sec = (datetime.now(timezone.utc) - price_at.replace(tzinfo=timezone.utc)).total_seconds()
                    if age_sec > PRICE_STALE_SEC:
                        record_price_stale_skip(ticker)
                        logger.warning(
                            "price_stale_skip_hard_fail",
                            ticker=ticker, age_sec=round(age_sec),
                        )
                        # Skip hard fail but still allow judge/other logic
                        if gain_pct <= HARD_FAIL_THRESHOLD:
                            return GateResult(
                                position_id=pos.id, ticker=ticker, bucket=bucket.value,
                                zone=Zone.AMBIGUOUS, action=GateAction.JUDGE,
                                gain_pct=gain_pct, hours_held=hours_held,
                                checkpoint=active_checkpoint,
                                reason=f"price stale ({age_sec:.0f}s), skipping hard fail, routing to judge",
                            )
            except Exception:
                pass

        # Classify zone
        if gain_pct <= HARD_FAIL_THRESHOLD:
            zone = Zone.HARD_FAIL
            action = GateAction.EXIT
            reason = f"{active_checkpoint.reason}: gain {gain_pct:+.1f}% <= {HARD_FAIL_THRESHOLD}% hard fail"
            record_perf_gate_zone(zone.value, bucket.value)
            record_perf_gate_exit("hard_fail")
        elif gain_pct >= CLEAR_WIN_THRESHOLD:
            zone = Zone.CLEAR_WIN
            action = GateAction.HOLD
            reason = f"clear win: gain {gain_pct:+.1f}% >= {CLEAR_WIN_THRESHOLD}%"
            record_perf_gate_zone(zone.value, bucket.value)
            await self._mark_checkpoint_passed(pos.id, active_checkpoint.after_minutes)
            # Phase 1B: Set dynamic stop to lock in profit
            stop_pct = max(CLEAR_WIN_STOP_FLOOR, gain_pct * CLEAR_WIN_STOP_RATIO)
            await self._set_dynamic_stop(pos.id, stop_pct)
            reason += f" [dynamic stop set to {stop_pct:.1f}%]"
        elif gain_pct >= active_checkpoint.min_gain_pct:
            # Above minimum but not clear win — ambiguous, judge it
            zone = Zone.AMBIGUOUS
            action = GateAction.JUDGE
            reason = (
                f"ambiguous: gain {gain_pct:+.1f}% >= min {active_checkpoint.min_gain_pct}% "
                f"but < {CLEAR_WIN_THRESHOLD}% clear win"
            )
            record_perf_gate_zone(zone.value, bucket.value)
        else:
            # Below minimum — ambiguous, judge it
            zone = Zone.AMBIGUOUS
            action = GateAction.JUDGE
            reason = (
                f"below checkpoint: gain {gain_pct:+.1f}% < min {active_checkpoint.min_gain_pct}% "
                f"for {active_checkpoint.reason}"
            )
            record_perf_gate_zone(zone.value, bucket.value)

        # Check for escalation (prior judge said HOLD, still bad)
        escalation = False
        if action == GateAction.JUDGE:
            prior_judgment = await self._get_prior_judgment(pos.id)
            if prior_judgment and prior_judgment.get("action") == "HOLD":
                escalation = True
                reason += " [ESCALATED: prior judge said HOLD, still underperforming]"

        logger.info(
            "gate_eval",
            ticker=ticker, bucket=bucket.value, zone=zone.value,
            action=action.value, gain_pct=round(gain_pct, 2),
            hours_held=round(hours_held, 1),
            checkpoint=active_checkpoint.after_minutes,
            escalation=escalation,
        )

        return GateResult(
            position_id=pos.id, ticker=ticker, bucket=bucket.value,
            zone=zone.value, action=action.value,
            gain_pct=gain_pct, hours_held=hours_held,
            checkpoint=active_checkpoint, reason=reason,
            escalation=escalation,
        )

    async def record_judgment(self, position_id: int, judgment: dict) -> None:
        """Record AI judge decision for escalation and consecutive hold tracking."""
        if not self._redis:
            return
        try:
            key = f"karsa:gate:judgment:{position_id}"
            await self._redis.setex(key, 86400, json.dumps(judgment))  # 24h TTL

            # Phase 1D: Track consecutive holds
            hold_key = f"karsa:gate:holds:{position_id}"
            if judgment.get("action") == "HOLD":
                count = await self._redis.incr(hold_key)
                await self._redis.expire(hold_key, 86400)
            else:
                # Non-HOLD action resets the counter
                await self._redis.delete(hold_key)
        except Exception:
            pass

    async def mark_position_closed(self, position_id: int) -> None:
        """Clean up tracking data when position closes."""
        if not self._redis:
            return
        try:
            cp_key = f"karsa:gate:checkpoints:{position_id}"
            j_key = f"karsa:gate:judgment:{position_id}"
            peak_key = f"karsa:gate:peak:{position_id}"
            hold_key = f"karsa:gate:holds:{position_id}"
            await self._redis.delete(cp_key, j_key, peak_key, hold_key)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal Redis helpers
    # ------------------------------------------------------------------

    async def _checkpoint_already_passed(self, position_id: int, checkpoint_minutes: int) -> bool:
        """Check if position already passed this checkpoint."""
        if not self._redis:
            return False
        try:
            key = f"karsa:gate:checkpoints:{position_id}"
            return bool(await self._redis.sismember(key, str(checkpoint_minutes)))
        except Exception:
            return False

    async def _mark_checkpoint_passed(self, position_id: int, checkpoint_minutes: int) -> None:
        """Mark checkpoint as passed so we don't re-evaluate."""
        if not self._redis:
            return
        try:
            key = f"karsa:gate:checkpoints:{position_id}"
            await self._redis.sadd(key, str(checkpoint_minutes))
            await self._redis.expire(key, 604800)  # 7 days
        except Exception:
            pass

    async def _get_prior_judgment(self, position_id: int) -> dict | None:
        """Get prior AI judge decision for this position."""
        if not self._redis:
            return None
        try:
            key = f"karsa:gate:judgment:{position_id}"
            data = await self._redis.get(key)
            return json.loads(data) if data else None
        except Exception:
            return None

    async def _set_dynamic_stop(self, position_id: int, stop_pct: float) -> None:
        """Set dynamic stop percentage in Redis (also written to DB by caller)."""
        if not self._redis:
            return
        try:
            key = f"karsa:gate:dynamic_stop:{position_id}"
            await self._redis.setex(key, 604800, str(stop_pct))  # 7 days
        except Exception:
            pass

    async def _get_dynamic_stop(self, position_id: int) -> float | None:
        """Get dynamic stop from Redis."""
        if not self._redis:
            return None
        try:
            key = f"karsa:gate:dynamic_stop:{position_id}"
            data = await self._redis.get(key)
            return float(data) if data else None
        except Exception:
            return None

    async def _get_peak_gain(self, position_id: int) -> float | None:
        """Get peak gain since last checkpoint."""
        if not self._redis:
            return None
        try:
            key = f"karsa:gate:peak:{position_id}"
            data = await self._redis.get(key)
            return float(data) if data else None
        except Exception:
            return None

    async def _update_peak_gain(self, position_id: int, current_gain: float) -> None:
        """Update peak gain if current is higher."""
        if not self._redis:
            return
        try:
            key = f"karsa:gate:peak:{position_id}"
            peak = await self._redis.get(key)
            if peak is None or current_gain > float(peak):
                await self._redis.setex(key, 604800, str(current_gain))  # 7 days
        except Exception:
            pass

    async def _reset_peak_gain(self, position_id: int) -> None:
        """Reset peak gain (called on checkpoint pass)."""
        if not self._redis:
            return
        try:
            key = f"karsa:gate:peak:{position_id}"
            await self._redis.delete(key)
        except Exception:
            pass

    async def _get_consecutive_holds(self, position_id: int) -> int:
        """Get consecutive AI HOLD count."""
        if not self._redis:
            return 0
        try:
            key = f"karsa:gate:holds:{position_id}"
            data = await self._redis.get(key)
            return int(data) if data else 0
        except Exception:
            return 0
