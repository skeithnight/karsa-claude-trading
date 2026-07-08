"""Karsa Trading System — Funding Capture Sub-Strategy

Identifies tokens with persistently extreme funding rates and takes
the funding-favorable side. This is a carry trade — it earns funding
income, not price momentum.

Key properties:
- Entry: annualized funding > ±15% (configurable)
- Direction: high positive funding → SHORT (earn from longs), high negative → LONG
- Leverage: 2x max (independent of momentum book)
- Hold: minimum 3 funding epochs (24h), max 7 days
- Exit: funding rate mean-reverts below ±5% OR hold exceeds 7 days
- Capital: 10% base allocation, scales to 25% at extreme funding, hard cap 3 positions
- P&L: tracked separately via signal_source="funding_capture"

Flow:
  Scheduler calls scan() every 4 hours (aligned with funding epochs) →
  Returns funding signals → Orchestrator evaluates through risk gate →
  SOR executes with 2x leverage cap.
"""

from datetime import datetime, timezone
from decimal import Decimal

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("funding_capture")

# --- Configuration ---
FUNDING_ENTRY_THRESHOLD_ANNUALIZED = 15.0   # % annualized — entry trigger
FUNDING_EXIT_THRESHOLD_ANNUALIZED = 5.0     # % annualized — exit trigger (mean reversion)
MAX_HOLD_DAYS = 7                            # hard max hold period
MIN_HOLD_EPOCHS = 3                          # minimum 3 funding epochs (24h)
MAX_FUNDING_POSITIONS = 3                    # hard cap on concurrent funding positions
FUNDING_LEVERAGE_CAP = 2                     # max leverage for funding book
FUNDING_BASE_ALLOCATION_PCT = 10.0           # base % of risk budget
FUNDING_EXTENDED_ALLOCATION_PCT = 25.0       # scaled allocation at extreme funding

# Universe to scan for funding opportunities
FUNDING_UNIVERSE = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "SUIUSDT", "NEARUSDT", "PEPEUSDT", "MATICUSDT",
]


class FundingCaptureStrategy:
    """Funding rate carry trade strategy for perpetuals."""

    def __init__(self, funding_tracker, bybit_client):
        self.tracker = funding_tracker
        self.bybit = bybit_client

    async def scan(self, open_positions: list[dict] | None = None) -> list[dict]:
        """Scan universe for funding capture opportunities.

        Args:
            open_positions: Current open positions (to check concurrency cap)

        Returns:
            List of signal dicts ready for risk gate evaluation
        """
        signals = []

        # Count existing funding-book positions
        funding_count = 0
        if open_positions:
            funding_count = sum(
                1 for p in open_positions
                if p.get("signal_source") == "funding_capture"
            )

        if funding_count >= MAX_FUNDING_POSITIONS:
            logger.info("funding_cap_reached", current=funding_count, max=MAX_FUNDING_POSITIONS)
            return []

        # Get current funding rates
        rates = await self.tracker.get_current_rates(FUNDING_UNIVERSE)

        for rate_data in rates:
            try:
                signal = await self._evaluate_symbol(rate_data, open_positions, funding_count)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.warning("funding_eval_failed",
                               symbol=rate_data.get("symbol"), error=str(e))

        if signals:
            logger.info("funding_signals_generated", count=len(signals),
                        tickers=[s["ticker"] for s in signals])

        return signals

    async def _evaluate_symbol(self, rate_data: dict, open_positions: list[dict] | None,
                                funding_count: int) -> dict | None:
        """Evaluate a single symbol for funding capture opportunity."""
        symbol = rate_data.get("symbol", "")
        rate = rate_data.get("funding_rate", 0)
        annualized = rate_data.get("annualized_pct", 0)

        if not rate or not annualized:
            return None

        # Check entry threshold
        if abs(annualized) < FUNDING_ENTRY_THRESHOLD_ANNUALIZED:
            return None

        # Skip if already have position in this symbol
        if open_positions:
            existing = [p for p in open_positions
                        if p.get("symbol") == symbol or p.get("ticker") == symbol]
            if existing:
                return None

        # Direction: high positive funding → SHORT (earn from longs paying)
        #             high negative funding → LONG (earn from shorts paying)
        if rate > 0:
            direction = "SHORT"
        else:
            direction = "LONG"

        # Get current price for entry
        ticker_data = await self.bybit.get_ticker(symbol)
        if not ticker_data or not ticker_data.get("price"):
            return None
        entry_price = ticker_data["price"]

        # Compute confidence based on extremity of funding
        # More extreme = higher confidence
        extremity = abs(annualized) / FUNDING_ENTRY_THRESHOLD_ANNUALIZED
        confidence = min(90, 50 + int(extremity * 15))  # 50-90 range

        # Build signal for risk gate
        signal = {
            "ticker": symbol,
            "direction": direction,
            "confidence_score": confidence,
            "entry_price": entry_price,
            "strategy": "funding_capture",
            "signal_source": "funding_capture",
            "_funding_rate": rate,
            "_funding_annualized": annualized,
            "_funding_direction": "positive" if rate > 0 else "negative",
            # Force 2x leverage cap
            "_override_leverage": FUNDING_LEVERAGE_CAP,
            "reasoning": (
                f"Funding capture: {symbol} annualized {annualized:+.1f}% "
                f"(rate {rate*100:.4f}% per 8h). "
                f"Taking {direction} side to earn funding from crowded "
                f"{'longs' if rate > 0 else 'shorts'}."
            ),
        }

        logger.info("funding_signal",
                     symbol=symbol, direction=direction,
                     rate=f"{rate*100:.4f}%", annualized=f"{annualized:+.1f}%",
                     confidence=confidence)

        return signal

    def should_exit(self, position: dict, current_rate: float) -> dict:
        """Check if a funding position should be exited.

        Args:
            position: Open position dict with opened_at, signal_source, side
            current_rate: Current 8h funding rate for this symbol

        Returns:
            {"exit": bool, "reason": str}
        """
        # Only manage funding-book positions
        if position.get("signal_source") != "funding_capture":
            return {"exit": False, "reason": "not_funding_book"}

        # Check hold duration
        opened_at = position.get("opened_at")
        if opened_at:
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)
            hours_held = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600

            # Min hold: 24h (3 funding epochs)
            if hours_held < MIN_HOLD_EPOCHS * 8:
                return {"exit": False, "reason": f"min_hold_{int(hours_held)}h"}

            # Max hold: 7 days
            if hours_held >= MAX_HOLD_DAYS * 24:
                return {"exit": True, "reason": f"max_hold_{MAX_HOLD_DAYS}d"}

        # Check funding mean reversion
        annualized = current_rate * 3 * 365 * 100
        side = position.get("side", "")

        # If we're SHORT and funding has reverted to negative → exit
        if side == "Sell" and current_rate < 0:
            if abs(annualized) < FUNDING_EXIT_THRESHOLD_ANNUALIZED:
                return {"exit": True, "reason": f"funding_reverted_{annualized:+.1f}%"}

        # If we're LONG and funding has reverted to positive → exit
        if side == "Buy" and current_rate > 0:
            if abs(annualized) < FUNDING_EXIT_THRESHOLD_ANNUALIZED:
                return {"exit": True, "reason": f"funding_reverted_{annualized:+.1f}%"}

        return {"exit": False, "reason": "holding"}

    def get_allocation(self, annualized_funding_pct: float) -> float:
        """Get capital allocation % based on funding extremity.

        Base: 10%, scales to 25% when |annualized| > 15%.
        """
        if abs(annualized_funding_pct) >= FUNDING_ENTRY_THRESHOLD_ANNUALIZED:
            # Linear scale: 10% at threshold, 25% at 2x threshold
            scale = min(1.0, (abs(annualized_funding_pct) - FUNDING_ENTRY_THRESHOLD_ANNUALIZED)
                        / FUNDING_ENTRY_THRESHOLD_ANNUALIZED)
            return FUNDING_BASE_ALLOCATION_PCT + scale * (
                FUNDING_EXTENDED_ALLOCATION_PCT - FUNDING_BASE_ALLOCATION_PCT
            )
        return FUNDING_BASE_ALLOCATION_PCT
