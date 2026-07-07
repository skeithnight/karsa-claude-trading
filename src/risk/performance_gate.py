"""Karsa Trading System — Performance Gate Engine

Two-layer position exit system:
  Layer 1: Mechanical checkpoints (no LLM, zero cost)
    - Hard fail zone: instant exit (e.g., -10% in 15min)
    - Clear win zone: hold (e.g., +5% at checkpoint)
    - Ambiguous zone: trigger AI judge

  Layer 2: AI Judge (LLM-based)
    - Cheap pass: compact prompt, pre-collected market data
    - If judge says HOLD but still underperforms next checkpoint:
      escalate to expensive pass with full context + reasoning trace

Checkpoint schedules per bucket:
  Meme:     15min, 30min, 1h, 2h, 4h, 8h, 24h
  Standard: 1h, 4h, 12h, 24h, 72h
  Core:     4h, 24h, 72h, 168h (7d)

Flow:
  Scheduler calls PerformanceGate.evaluate_all() every 5 min →
  For each open position: determine bucket → find current checkpoint →
  Calculate gain % → classify zone →
    HARD_FAIL → immediate close via SOR
    CLEAR_WIN → hold, advance checkpoint
    AMBIGUOUS → fire PositionJudge agent → act on judgment
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from src.utils.logging import get_logger

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
        Checkpoint(after_minutes=60,   min_gain_pct=0.0,  reason="meme_1h_not_breakeven"),
        Checkpoint(after_minutes=120,  min_gain_pct=1.0,  reason="meme_2h_weak"),
        Checkpoint(after_minutes=240,  min_gain_pct=2.0,  reason="meme_4h_dead"),
        Checkpoint(after_minutes=480,  min_gain_pct=3.0,  reason="meme_8h_stale"),
        Checkpoint(after_minutes=1440, min_gain_pct=5.0,  reason="meme_24h_underperform"),
    ],
    Bucket.STANDARD: [
        Checkpoint(after_minutes=60,   min_gain_pct=-5.0, reason="std_1h_crash"),
        Checkpoint(after_minutes=240,  min_gain_pct=-2.0, reason="std_4h_bleeding"),
        Checkpoint(after_minutes=720,  min_gain_pct=0.0,  reason="std_12h_flat"),
        Checkpoint(after_minutes=1440, min_gain_pct=1.0,  reason="std_24h_weak"),
        Checkpoint(after_minutes=4320, min_gain_pct=2.0,  reason="std_72h_stale"),
    ],
    Bucket.CORE: [
        Checkpoint(after_minutes=240,   min_gain_pct=-8.0, reason="core_4h_crash"),
        Checkpoint(after_minutes=1440,  min_gain_pct=-3.0, reason="core_24h_check"),
        Checkpoint(after_minutes=4320,  min_gain_pct=0.0,  reason="core_72h_flat"),
        Checkpoint(after_minutes=10080, min_gain_pct=2.0,  reason="core_7d_underperform"),
    ],
}

# Zone boundaries
HARD_FAIL_THRESHOLD = -8.0   # gain < -8% at any checkpoint = hard fail
CLEAR_WIN_THRESHOLD = 3.0    # gain > +3% at checkpoint = clear win


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

    if any(kw in source for kw in ("core", "high_conviction", "regime_aligned")):
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
    Returns actions: EXIT (hard fail), HOLD (clear win), JUDGE (ambiguous),
    or SKIP (not at checkpoint yet).
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
        checkpoints = CHECKPOINTS[bucket]
        gain_pct = get_gain_pct(entry, current, side)
        hours_held = get_hours_held(opened_at)
        minutes_held = hours_held * 60

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

        # Classify zone
        if gain_pct <= HARD_FAIL_THRESHOLD:
            zone = Zone.HARD_FAIL
            action = GateAction.EXIT
            reason = f"{active_checkpoint.reason}: gain {gain_pct:+.1f}% <= {HARD_FAIL_THRESHOLD}% hard fail"
        elif gain_pct >= CLEAR_WIN_THRESHOLD:
            zone = Zone.CLEAR_WIN
            action = GateAction.HOLD
            reason = f"clear win: gain {gain_pct:+.1f}% >= {CLEAR_WIN_THRESHOLD}%"
            await self._mark_checkpoint_passed(pos.id, active_checkpoint.after_minutes)
        elif gain_pct >= active_checkpoint.min_gain_pct:
            # Above minimum but not clear win — ambiguous, judge it
            zone = Zone.AMBIGUOUS
            action = GateAction.JUDGE
            reason = (
                f"ambiguous: gain {gain_pct:+.1f}% >= min {active_checkpoint.min_gain_pct}% "
                f"but < {CLEAR_WIN_THRESHOLD}% clear win"
            )
        else:
            # Below minimum — hard fail or ambiguous depending on severity
            zone = Zone.AMBIGUOUS
            action = GateAction.JUDGE
            reason = (
                f"below checkpoint: gain {gain_pct:+.1f}% < min {active_checkpoint.min_gain_pct}% "
                f"for {active_checkpoint.reason}"
            )

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
        """Record AI judge decision for escalation tracking."""
        if not self._redis:
            return
        try:
            key = f"karsa:gate:judgment:{position_id}"
            await self._redis.setex(key, 86400, json.dumps(judgment))  # 24h TTL
        except Exception:
            pass

    async def mark_position_closed(self, position_id: int) -> None:
        """Clean up tracking data when position closes."""
        if not self._redis:
            return
        try:
            cp_key = f"karsa:gate:checkpoints:{position_id}"
            j_key = f"karsa:gate:judgment:{position_id}"
            await self._redis.delete(cp_key, j_key)
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
