"""Karsa Trading System — Downside (Tail) Correlation Calculator

Computes pairwise correlation using only hours where BTC dropped > 1.5%.
Standard Pearson understates crash risk in crypto — assets show ~0.4
correlation on green days but ~0.95 on red days.

Used by the correlation gate (Gate 3b replacement) to block new positions
when adding them would over-concentate in correlated crash risk.

Parameters:
- Window: 30-day rolling of 1h returns
- Filter: only hours where BTC 1h return < -1.5%
- Threshold: correlation > 0.75 = "highly correlated"
- Rule: block if total notional across correlated assets > 35% of equity

Flow:
  Risk manager calls get_downside_correlation(ticker, open_positions) →
  Returns pairwise correlations and exposure check →
  Gate 3b uses this instead of static tier grouping.
"""

import time
from src.utils.logging import get_logger

logger = get_logger("correlation")

# Cache TTL (1 hour — computationally expensive)
CACHE_TTL_SEC = 3600

# Correlation parameters
BTC_DROP_THRESHOLD_PCT = 1.5  # only use hours where BTC dropped > this
CORRELATION_WINDOW_DAYS = 30
CORRELATION_THRESHOLD = 0.75  # above this = "highly correlated"
MAX_CORRELATED_EXPOSURE_PCT = 0.35  # 35% of equity cap


class DownsideCorrelationCalculator:
    """Computes pairwise downside correlation for crypto positions."""

    def __init__(self, bybit_client):
        self.bybit = bybit_client
        self._cache: dict[str, tuple[dict, float]] = {}  # key -> (data, timestamp)
        self._returns_cache: dict[str, tuple[list[float], float]] = {}  # ticker -> (returns, ts)

    async def get_downside_correlation(
        self,
        candidate_ticker: str,
        open_positions: list[dict],
        wallet_balance: float,
    ) -> dict:
        """Check if adding candidate would exceed correlated exposure cap.

        Args:
            candidate_ticker: Ticker being considered for entry
            open_positions: Currently open positions
            wallet_balance: Total USDT equity

        Returns:
            {
                "allowed": bool,
                "reason": str,
                "correlations": {ticker: corr_value},
                "correlated_exposure_pct": float,
                "highly_correlated": [ticker, ...],
            }
        """
        if not open_positions:
            return {"allowed": True, "reason": "no_open_positions",
                    "correlations": {}, "correlated_exposure_pct": 0,
                    "highly_correlated": []}

        # Get BTC 1h returns for crash filtering
        btc_returns = await self._get_hourly_returns("BTCUSDT")
        if not btc_returns or len(btc_returns) < 100:
            logger.warning("correlation_insufficient_btc_data", samples=len(btc_returns) if btc_returns else 0)
            return {"allowed": True, "reason": "insufficient_btc_data",
                    "correlations": {}, "correlated_exposure_pct": 0,
                    "highly_correlated": []}

        # Find crash hours (BTC dropped > threshold)
        crash_indices = set()
        for i, ret in enumerate(btc_returns):
            if ret < -BTC_DROP_THRESHOLD_PCT / 100:
                crash_indices.add(i)

        if len(crash_indices) < 10:
            logger.warning("correlation_insufficient_crash_hours", crash_hours=len(crash_indices))
            return {"allowed": True, "reason": "insufficient_crash_hours",
                    "correlations": {}, "correlated_exposure_pct": 0,
                    "highly_correlated": []}

        # Get candidate returns
        candidate_returns = await self._get_hourly_returns(candidate_ticker)
        if not candidate_returns:
            return {"allowed": True, "reason": "no_candidate_data",
                    "correlations": {}, "correlated_exposure_pct": 0,
                    "highly_correlated": []}

        # Compute pairwise downside correlation with each open position
        correlations = {}
        highly_correlated = []

        for pos in open_positions:
            pos_ticker = pos.get("symbol") or pos.get("ticker", "")
            if not pos_ticker or pos_ticker == candidate_ticker:
                continue

            pos_returns = await self._get_hourly_returns(pos_ticker)
            if not pos_returns:
                continue

            corr = self._compute_downside_correlation(
                candidate_returns, pos_returns, crash_indices,
            )
            correlations[pos_ticker] = round(corr, 3)

            if corr > CORRELATION_THRESHOLD:
                highly_correlated.append(pos_ticker)

        # Compute total exposure across highly correlated assets
        correlated_exposure = 0.0
        for pos in open_positions:
            pos_ticker = pos.get("symbol") or pos.get("ticker", "")
            if pos_ticker in highly_correlated:
                entry_price = pos.get("entry_price", 0)
                size = pos.get("size", 0)
                correlated_exposure += entry_price * size

        correlated_exposure_pct = correlated_exposure / wallet_balance if wallet_balance > 0 else 0

        # Decision
        if correlated_exposure_pct > MAX_CORRELATED_EXPOSURE_PCT:
            return {
                "allowed": False,
                "reason": (f"Correlated exposure {correlated_exposure_pct*100:.1f}% "
                           f"> {MAX_CORRELATED_EXPOSURE_PCT*100:.0f}% cap "
                           f"(highly correlated with: {', '.join(highly_correlated)})"),
                "correlations": correlations,
                "correlated_exposure_pct": round(correlated_exposure_pct, 4),
                "highly_correlated": highly_correlated,
            }

        return {
            "allowed": True,
            "reason": "ok",
            "correlations": correlations,
            "correlated_exposure_pct": round(correlated_exposure_pct, 4),
            "highly_correlated": highly_correlated,
        }

    def _compute_downside_correlation(
        self,
        returns_a: list[float],
        returns_b: list[float],
        crash_indices: set[int],
    ) -> float:
        """Compute Pearson correlation on crash-hour returns only."""
        # Align to same length
        min_len = min(len(returns_a), len(returns_b))

        # Filter to crash hours only
        a_filtered = []
        b_filtered = []
        for i in range(min_len):
            if i in crash_indices:
                a_filtered.append(returns_a[i])
                b_filtered.append(returns_b[i])

        if len(a_filtered) < 10:
            return 0.0

        return self._pearson(a_filtered, b_filtered)

    @staticmethod
    def _pearson(x: list[float], y: list[float]) -> float:
        """Compute Pearson correlation coefficient."""
        n = len(x)
        if n < 3:
            return 0.0

        mean_x = sum(x) / n
        mean_y = sum(y) / n

        cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
        var_x = sum((x[i] - mean_x) ** 2 for i in range(n))
        var_y = sum((y[i] - mean_y) ** 2 for i in range(n))

        denom = (var_x * var_y) ** 0.5
        if denom == 0:
            return 0.0

        return cov / denom

    async def _get_hourly_returns(self, ticker: str) -> list[float]:
        """Fetch 1h returns for the last 30 days with caching."""
        now = time.time()
        cached = self._returns_cache.get(ticker)
        if cached and now - cached[1] < CACHE_TTL_SEC:
            return cached[0]

        try:
            # 30 days * 24 hours = 720 candles
            ohlcv = await self.bybit.get_ohlcv(ticker, interval="1h", limit=720)
            if not ohlcv or len(ohlcv) < 50:
                return []

            # Compute returns
            returns = []
            for i in range(1, len(ohlcv)):
                prev_close = ohlcv[i - 1].get("close", 0) if isinstance(ohlcv[i - 1], dict) else 0
                curr_close = ohlcv[i].get("close", 0) if isinstance(ohlcv[i], dict) else 0
                if prev_close > 0:
                    returns.append((curr_close - prev_close) / prev_close)

            self._returns_cache[ticker] = (returns, now)
            return returns

        except Exception as e:
            logger.warning("correlation_returns_fetch_failed", ticker=ticker, error=str(e))
            return []
