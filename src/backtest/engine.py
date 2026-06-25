"""Karsa Trading System - Backtesting Engine

Uses pure-Python RSI/Bollinger calculation for backtesting.
Data comes from the ohlcv_cache table in Postgres.

Minimum thresholds before a strategy can go live:
  - Sharpe Ratio > 1.2
  - Max Drawdown < 15%
  - Win Rate > 50%
"""

import statistics
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.tables import OHLCVCache
from src.utils.logging import get_logger

logger = get_logger("backtest")

THRESHOLDS = {
    "sharpe_ratio": 1.2,
    "max_drawdown_pct": 15.0,
    "win_rate_pct": 50.0,
}


class BacktestResult:
    def __init__(self, strategy: str, ticker: str, market: str):
        self.strategy = strategy
        self.ticker = ticker
        self.market = market
        self.total_trades: int = 0
        self.winning_trades: int = 0
        self.losing_trades: int = 0
        self.win_rate: float = 0.0
        self.total_return_pct: float = 0.0
        self.sharpe_ratio: float = 0.0
        self.max_drawdown_pct: float = 0.0
        self.profit_factor: float = 0.0
        self.passed: bool = False
        self.failures: list[str] = []

    def evaluate(self):
        self.failures = []
        if self.win_rate < THRESHOLDS["win_rate_pct"]:
            self.failures.append(f"Win rate {self.win_rate:.1f}% < {THRESHOLDS['win_rate_pct']}%")
        if self.sharpe_ratio < THRESHOLDS["sharpe_ratio"]:
            self.failures.append(f"Sharpe {self.sharpe_ratio:.2f} < {THRESHOLDS['sharpe_ratio']}")
        if self.max_drawdown_pct > THRESHOLDS["max_drawdown_pct"]:
            self.failures.append(f"Max DD {self.max_drawdown_pct:.1f}% > {THRESHOLDS['max_drawdown_pct']}%")
        self.passed = len(self.failures) == 0

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy, "ticker": self.ticker, "market": self.market,
            "total_trades": self.total_trades, "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades, "win_rate": round(self.win_rate, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "profit_factor": round(self.profit_factor, 2),
            "passed": self.passed, "failures": self.failures,
        }


async def load_ohlcv(session: AsyncSession, ticker: str, market: str, timeframe: str = "1D") -> list[dict]:
    result = await session.execute(
        select(OHLCVCache).where(
            OHLCVCache.ticker == ticker, OHLCVCache.market == market,
            OHLCVCache.timeframe == timeframe
        ).order_by(OHLCVCache.timestamp.asc())
    )
    return [{"timestamp": r.timestamp, "open": float(r.open), "high": float(r.high),
             "low": float(r.low), "close": float(r.close), "volume": r.volume}
            for r in result.scalars().all()]


def backtest_rsi_mean_reversion(
    candles: list[dict], ticker: str, market: str,
    rsi_period: int = 14, bb_period: int = 20,
    buy_rsi: float = 30.0, sell_rsi: float = 70.0,
) -> BacktestResult:
    """RSI + Bollinger mean reversion. Entry: RSI<30 AND close<lower BB. Exit: RSI>70."""
    result = BacktestResult("RSI Mean Reversion", ticker, market)
    if len(candles) < max(rsi_period, bb_period) + 5:
        result.failures = ["Insufficient data"]
        return result

    closes = [c["close"] for c in candles]
    rsi_values = _calc_rsi(closes, rsi_period)
    _, _, bb_lower = _calc_bollinger(closes, bb_period, 2.0)

    in_position = False
    entry_price = 0.0
    wins = losses = 0
    gross_profit = gross_loss = 0.0
    returns = []
    peak = equity = 100.0
    max_dd = 0.0

    for i in range(max(rsi_period, bb_period), len(closes)):
        if not in_position:
            if rsi_values[i] < buy_rsi and closes[i] < bb_lower[i]:
                in_position = True
                entry_price = closes[i]
        else:
            if rsi_values[i] > sell_rsi:
                in_position = False
                pnl_pct = (closes[i] - entry_price) / entry_price * 100
                returns.append(pnl_pct)
                if pnl_pct > 0:
                    wins += 1; gross_profit += pnl_pct
                else:
                    losses += 1; gross_loss += abs(pnl_pct)
                equity *= (1 + pnl_pct / 100)
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak * 100)

    total = wins + losses
    result.total_trades = total
    result.winning_trades = wins
    result.losing_trades = losses
    result.win_rate = (wins / total * 100) if total > 0 else 0
    result.total_return_pct = equity - 100
    result.max_drawdown_pct = max_dd
    result.profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    if returns and len(returns) > 1:
        avg = statistics.mean(returns)
        sd = statistics.stdev(returns)
        result.sharpe_ratio = (avg / sd) * (252 ** 0.5) if sd > 0 else 0
    result.evaluate()
    return result


def _calc_rsi(closes: list[float], period: int) -> list[float]:
    rsi = [50.0] * len(closes)
    if len(closes) < period + 1:
        return rsi
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        rsi[i + 1] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return rsi


def _calc_bollinger(closes: list[float], period: int, std_dev: float):
    upper = [0.0] * len(closes)
    middle = [0.0] * len(closes)
    lower = [0.0] * len(closes)
    for i in range(period - 1, len(closes)):
        w = closes[i - period + 1 : i + 1]
        sma = sum(w) / period
        sd = statistics.stdev(w) if len(w) > 1 else 0.0
        middle[i] = sma; upper[i] = sma + std_dev * sd; lower[i] = sma - std_dev * sd
    return upper, middle, lower
