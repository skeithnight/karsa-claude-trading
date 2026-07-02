"""Karsa Trading System — Cross-Market Capital Allocator

Prevents one market from draining the entire account.
Checks sub-account limits before any order is sent.

Hard limits (immutable):
  - Crypto: 30% of total equity
  - US Equities: 40%
  - ETF: 20%
  - IDX: 10%
  - Global drawdown: 5% across all markets → kill switch

Flow:
  Orchestrator calls allocator.can_trade(market, notional) before execution →
  Returns (allowed: bool, reason: str).
  Global drawdown checked every 5 min via APScheduler.
"""

from decimal import Decimal
from src.utils.logging import get_logger

logger = get_logger("portfolio_allocator")

# Sub-account allocation limits (% of total equity)
MARKET_LIMITS = {
    "CRYPTO": Decimal("0.30"),
    "US": Decimal("0.40"),
    "ETF": Decimal("0.20"),
    "IDX": Decimal("0.10"),
}

# Global drawdown limit
GLOBAL_DRAWDOWN_LIMIT = Decimal("0.05")  # 5%


class PortfolioAllocator:
    """Cross-market capital allocation guard."""

    def __init__(self, redis_client):
        self._redis = redis_client
        self._initial_equity: Decimal | None = None

    async def can_trade(self, market: str, notional_usd: Decimal,
                        equity: Decimal | None = None) -> tuple[bool, str]:
        """Check if a trade is allowed under allocation limits.

        Args:
            market: "CRYPTO", "US", "ETF", or "IDX"
            notional_usd: trade notional value in USD
            equity: total portfolio equity (fetched if None)

        Returns:
            (allowed, reason)
        """
        market = market.upper()
        limit_pct = MARKET_LIMITS.get(market)
        if not limit_pct:
            return True, "unknown_market_no_limit"

        if equity is None:
            equity = await self._get_total_equity()
        if equity <= 0:
            return False, "zero_equity"

        # Calculate current exposure for this market
        current_exposure = await self._get_market_exposure(market)
        max_allowed = equity * limit_pct
        remaining = max_allowed - current_exposure

        if notional_usd > remaining:
            return False, (
                f"{market} allocation limit: "
                f"${current_exposure:,.0f} / ${max_allowed:,.0f} used, "
                f"${remaining:,.0f} remaining, need ${notional_usd:,.0f}"
            )

        # Global drawdown check
        drawdown_ok, dd_reason = await self._check_global_drawdown(equity)
        if not drawdown_ok:
            return False, dd_reason

        return True, "ok"

    async def get_allocation_status(self) -> dict:
        """Get current allocation status for all markets."""
        equity = await self._get_total_equity()
        status = {"total_equity": float(equity), "markets": {}}

        for market, limit_pct in MARKET_LIMITS.items():
            exposure = await self._get_market_exposure(market)
            max_allowed = equity * limit_pct
            utilization = (exposure / max_allowed * 100) if max_allowed > 0 else 0
            status["markets"][market] = {
                "exposure_usd": float(exposure),
                "limit_usd": float(max_allowed),
                "utilization_pct": round(float(utilization), 1),
                "remaining_usd": float(max_allowed - exposure),
            }

        if self._initial_equity and self._initial_equity > 0:
            dd = (self._initial_equity - equity) / self._initial_equity
            status["global_drawdown_pct"] = round(float(dd * 100), 2)
            status["drawdown_limit_pct"] = float(GLOBAL_DRAWDOWN_LIMIT * 100)

        return status

    async def _get_total_equity(self) -> Decimal:
        """Get total portfolio equity across all markets."""
        try:
            from src.models.database import async_session
            from src.models.tables import CashBalance, PortfolioState
            from sqlalchemy import select, func

            async with async_session() as session:
                cash_result = await session.execute(select(func.sum(CashBalance.balance)))
                cash = cash_result.scalar() or Decimal("0")

                pos_result = await session.execute(
                    select(func.sum(
                        PortfolioState.current_price * PortfolioState.quantity
                    )).where(PortfolioState.current_price.isnot(None))
                )
                positions_value = pos_result.scalar() or Decimal("0")

                crypto_value = Decimal("0")
                try:
                    raw = await self._redis.get("karsa:state:wallet_balance")
                    if raw:
                        crypto_value = Decimal(str(raw))
                except Exception:
                    pass

                total = cash + positions_value + crypto_value

                # Track high-water mark for drawdown
                if self._initial_equity is None:
                    stored = await self._redis.get("karsa:state:initial_equity")
                    self._initial_equity = Decimal(str(stored)) if stored else total
                if total > self._initial_equity:
                    self._initial_equity = total
                    await self._redis.set("karsa:state:initial_equity", str(total))

                return total
        except Exception as e:
            logger.error("equity_fetch_failed", error=str(e))
            return Decimal("0")

    async def _get_market_exposure(self, market: str) -> Decimal:
        """Get current notional exposure for a market."""
        try:
            if market == "CRYPTO":
                raw = await self._redis.get("karsa:state:crypto_exposure")
                return Decimal(str(raw)) if raw else Decimal("0")

            from src.models.database import async_session
            from src.models.tables import PortfolioState
            from sqlalchemy import select, func

            async with async_session() as session:
                result = await session.execute(
                    select(func.sum(
                        PortfolioState.current_price * PortfolioState.quantity
                    )).where(
                        PortfolioState.market == market,
                        PortfolioState.current_price.isnot(None),
                    )
                )
                return result.scalar() or Decimal("0")
        except Exception:
            return Decimal("0")

    async def _check_global_drawdown(self, current_equity: Decimal) -> tuple[bool, str]:
        """Check if global drawdown exceeds limit."""
        if self._initial_equity is None or self._initial_equity <= 0:
            return True, "no_initial_equity"

        drawdown = (self._initial_equity - current_equity) / self._initial_equity
        if drawdown >= GLOBAL_DRAWDOWN_LIMIT:
            logger.warning("global_drawdown_breached",
                          drawdown_pct=float(drawdown * 100),
                          limit_pct=float(GLOBAL_DRAWDOWN_LIMIT * 100))
            return False, (
                f"Global drawdown {drawdown:.1%} exceeds limit {GLOBAL_DRAWDOWN_LIMIT:.1%}. "
                f"Equity: ${current_equity:,.0f} / Initial: ${self._initial_equity:,.0f}"
            )
        return True, "ok"
