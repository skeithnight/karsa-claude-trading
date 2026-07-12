"""Karsa Trading System — Confidence Calibration Engine

Tracks LLM's predicted confidence against actual trade outcomes.
Auto-adjusts future confidence scores using a rolling multiplier.

If LLM predicts 80% confidence but actual win rate is 40%,
the multiplier is 0.5 — future 80% scores become 40%.

Flow:
  Orchestrator calls calibrate_signal(confidence) after LLM returns →
  Returns adjusted confidence before risk gate check.
"""

from src.utils.logging import get_logger

logger = get_logger("calibration")

MIN_SAMPLE_SIZE = 20
MULTIPLIER_FLOOR = 0.5
MULTIPLIER_CEIL = 1.5

class ConfidenceCalibrator:
    """Deterministic confidence adjustment based on historical accuracy."""

    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self._cached_multiplier: float | None = None

    async def calculate_multiplier(self) -> float:
        """Calculate calibration multiplier from recent closed trades.

        Returns value between 0.5 and 1.5.
        1.0 = well-calibrated. <1.0 = overconfident. >1.0 = underconfident.
        """
        try:
            from src.models.database import async_session
            from src.models.tables import ClosedPaperTrade
            from sqlalchemy import select, desc

            async with async_session() as session:
                result = await session.execute(
                    select(ClosedPaperTrade)
                    .order_by(desc(ClosedPaperTrade.exit_date))
                    .limit(self.window_size)
                )
                trades = result.scalars().all()

            if len(trades) < MIN_SAMPLE_SIZE:
                return 1.0

            wins = sum(1 for t in trades if (t.realized_pnl or 0) > 0)
            actual_win_rate = wins / len(trades)

            confidences = []
            for t in trades:
                conf = getattr(t, 'confidence_score', None)
                if conf is not None:
                    confidences.append(float(conf))

            if not confidences or len(confidences) < MIN_SAMPLE_SIZE:
                return 1.0

            avg_predicted_conf = sum(confidences) / len(confidences) / 100.0
            if avg_predicted_conf == 0:
                return 1.0

            multiplier = actual_win_rate / avg_predicted_conf
            clamped = max(MULTIPLIER_FLOOR, min(MULTIPLIER_CEIL, multiplier))

            logger.info("calibration_updated",
                       trades=len(trades),
                       win_rate=round(actual_win_rate, 3),
                       avg_confidence=round(avg_predicted_conf, 3),
                       multiplier=round(clamped, 3))

            self._cached_multiplier = clamped
            return clamped

        except Exception as e:
            logger.error("calibration_calc_failed", error=str(e))
            return 1.0

    async def calibrate_signal(self, llm_confidence: float) -> float:
        """Adjust an LLM confidence score using the calibration multiplier."""
        multiplier = await self.get_multiplier()
        adjusted = llm_confidence * multiplier
        return round(min(100, max(0, adjusted)), 1)

    async def get_multiplier(self) -> float:
        if self._cached_multiplier is None:
            self._cached_multiplier = await self.calculate_multiplier()
        return self._cached_multiplier
