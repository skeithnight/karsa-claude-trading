"""Karsa Trading System - Backtesting Engine

Uses pure-Python RSI/Bollinger calculation for backtesting.
Data comes from the ohlcv_cache table in Postgres.

Minimum thresholds before a strategy can go live:
  - Sharpe Ratio > 1.2
  - Max Drawdown < 15%
  - Win Rate > 50%

Phase 3: Added crypto support (4h timeframe), SignalReplayEngine.
"""

import statistics
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.tables import OHLCVCache, Signal, ReasoningTrace
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


def _calc_ema(closes: list[float], period: int) -> list[float]:
    """Calculate Exponential Moving Average."""
    ema = [0.0] * len(closes)
    if len(closes) < period:
        return ema
    k = 2.0 / (period + 1)
    ema[period - 1] = sum(closes[:period]) / period
    for i in range(period, len(closes)):
        ema[i] = closes[i] * k + ema[i - 1] * (1 - k)
    return ema


class SignalReplayEngine:
    """Replay historical AI signals against actual OHLCV data.

    Shows what would have happened if each signal was followed.
    """

    async def replay(
        self, session: AsyncSession, ticker: str, market: str = "CRYPTO",
        days: int = 30, timeframe: str = "4h",
    ) -> dict:
        """Replay signals for a ticker over N days.

        Returns dict with signal list and aggregate stats.
        """
        from datetime import timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # Load signals
        sig_result = await session.execute(
            select(Signal).where(
                Signal.ticker == ticker,
                Signal.market == market,
                Signal.created_at >= cutoff,
            ).order_by(Signal.created_at.asc())
        )
        signals = sig_result.scalars().all()

        if not signals:
            return {"error": f"No signals found for {ticker} in last {days} days", "replays": []}

        # Load OHLCV
        candles = await load_ohlcv(session, ticker, market, timeframe)
        if not candles:
            # Try 1D fallback
            candles = await load_ohlcv(session, ticker, market, "1D")
            if not candles:
                return {"error": f"No OHLCV data for {ticker}", "replays": []}

        # Build price lookup (timestamp → candle)
        price_map = {}
        for c in candles:
            price_map[c["timestamp"]] = c

        replays = []
        wins = losses = 0
        total_pnl = 0.0

        for sig in signals:
            if not sig.entry_price or not sig.stop_loss_price:
                continue

            entry = float(sig.entry_price)
            sl = float(sig.stop_loss_price)
            tp = float(sig.target_price) if sig.target_price else None
            direction = sig.direction

            # Find candles after signal time
            sig_time = sig.created_at
            future_candles = [c for c in candles if c["timestamp"] >= sig_time]

            if not future_candles:
                continue

            exit_price = None
            exit_reason = "STILL_OPEN"
            exit_time = None
            bars_held = 0

            for j, fc in enumerate(future_candles[:50]):  # max 50 bars
                if direction == "LONG":
                    if fc["low"] <= sl:
                        exit_price = sl
                        exit_reason = "STOP_LOSS"
                        exit_time = fc["timestamp"]
                        bars_held = j + 1
                        break
                    if tp and fc["high"] >= tp:
                        exit_price = tp
                        exit_reason = "TAKE_PROFIT"
                        exit_time = fc["timestamp"]
                        bars_held = j + 1
                        break
                elif direction == "SHORT":
                    if fc["high"] >= sl:
                        exit_price = sl
                        exit_reason = "STOP_LOSS"
                        exit_time = fc["timestamp"]
                        bars_held = j + 1
                        break
                    if tp and fc["low"] <= tp:
                        exit_price = tp
                        exit_reason = "TAKE_PROFIT"
                        exit_time = fc["timestamp"]
                        bars_held = j + 1
                        break

            if exit_price is None and future_candles:
                # Mark-to-market at last available candle
                exit_price = future_candles[-1]["close"]
                exit_reason = "MARK_TO_MARKET"
                exit_time = future_candles[-1]["timestamp"]
                bars_held = len(future_candles[:50])

            if exit_price:
                if direction == "LONG":
                    pnl_pct = (exit_price - entry) / entry * 100
                else:
                    pnl_pct = (entry - exit_price) / entry * 100

                total_pnl += pnl_pct
                if pnl_pct > 0: wins += 1
                else: losses += 1

                replays.append({
                    "signal_id": str(sig.id),
                    "direction": direction,
                    "confidence": sig.confidence_score or 0,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "pnl_pct": round(pnl_pct, 2),
                    "bars_held": bars_held,
                    "signal_time": sig_time.isoformat() if sig_time else None,
                    "exit_time": exit_time.isoformat() if exit_time else None,
                })

        total = wins + losses
        return {
            "ticker": ticker,
            "market": market,
            "period_days": days,
            "total_signals": len(signals),
            "replayed": len(replays),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "total_pnl_pct": round(total_pnl, 2),
            "avg_pnl_pct": round(total_pnl / total, 2) if total > 0 else 0,
            "replays": replays,
        }


class RealisticCryptoBacktester:
    """Historical walk-forward backtester for Crypto signals.

    Applies Bybit realistic transaction costs:
    - Slippage: 0.05%
    - Taker fee: 0.055%
    - Maker fee: 0.02% (if limit fill)
    - Accrued funding: ~0.01% per 8h
    Calculates reality-adjusted Sharpe Ratio and Max Drawdown.
    """

    def __init__(self, slippage_pct: float = 0.05, taker_fee_pct: float = 0.055):
        self.slippage = slippage_pct / 100.0
        self.taker_fee = taker_fee_pct / 100.0
        # Average funding cost assuming 8h collection
        self.funding_cost_per_bar_4h = 0.01 / 100.0 / 2.0

    async def run(
        self, session: AsyncSession, ticker: str, market: str = "CRYPTO",
        days: int = 30, timeframe: str = "4h",
    ) -> BacktestResult:
        from datetime import timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        result = BacktestResult("Realistic Replay", ticker, market)

        sig_result = await session.execute(
            select(Signal).where(
                Signal.ticker == ticker,
                Signal.market == market,
                Signal.created_at >= cutoff,
            ).order_by(Signal.created_at.asc())
        )
        signals = sig_result.scalars().all()

        if not signals:
            result.failures.append(f"No signals found for {ticker} in last {days} days")
            return result

        candles = await load_ohlcv(session, ticker, market, timeframe)
        if not candles:
            candles = await load_ohlcv(session, ticker, market, "1D")
            if not candles:
                result.failures.append(f"No OHLCV data for {ticker}")
                return result

        gross_profit = 0.0
        gross_loss = 0.0
        equity_curve = [100.0]  # Start with 100% equity
        current_equity = 100.0
        peak_equity = 100.0
        max_drawdown = 0.0
        returns = []

        for sig in signals:
            if not sig.entry_price or not sig.stop_loss_price:
                continue

            direction = sig.direction
            # Simulate market entry with slippage + fees
            base_entry = float(sig.entry_price)
            real_entry = base_entry * (1 + self.slippage) if direction == "LONG" else base_entry * (1 - self.slippage)
            real_entry = real_entry * (1 + self.taker_fee) if direction == "LONG" else real_entry * (1 - self.taker_fee)

            sl = float(sig.stop_loss_price)
            tp = float(sig.target_price) if sig.target_price else None

            sig_time = sig.created_at
            future_candles = [c for c in candles if c["timestamp"] >= sig_time]
            if not future_candles:
                continue

            trade_pnl_pct = 0.0
            bars_held = 0

            for j, fc in enumerate(future_candles[:50]):
                bars_held = j + 1
                exit_price = None

                if direction == "LONG":
                    if fc["low"] <= sl:
                        exit_price = sl * (1 - self.slippage)
                    elif tp and fc["high"] >= tp:
                        exit_price = tp * (1 - self.slippage) # assumed stop-limit exit slip
                else:
                    if fc["high"] >= sl:
                        exit_price = sl * (1 + self.slippage)
                    elif tp and fc["low"] <= tp:
                        exit_price = tp * (1 + self.slippage)

                if exit_price is not None:
                    # Apply exit fees and funding
                    exit_price = exit_price * (1 - self.taker_fee) if direction == "LONG" else exit_price * (1 + self.taker_fee)
                    funding_paid = self.funding_cost_per_bar_4h * bars_held

                    if direction == "LONG":
                        trade_pnl_pct = ((exit_price - real_entry) / real_entry) - funding_paid
                    else:
                        trade_pnl_pct = ((real_entry - exit_price) / real_entry) - funding_paid

                    break

            if trade_pnl_pct != 0:
                result.total_trades += 1
                if trade_pnl_pct > 0:
                    result.winning_trades += 1
                    gross_profit += trade_pnl_pct
                else:
                    result.losing_trades += 1
                    gross_loss += abs(trade_pnl_pct)

                returns.append(trade_pnl_pct)
                current_equity *= (1 + trade_pnl_pct)
                equity_curve.append(current_equity)

                if current_equity > peak_equity:
                    peak_equity = current_equity

                dd = (peak_equity - current_equity) / peak_equity * 100
                if dd > max_drawdown:
                    max_drawdown = dd

        # Calculate KPIs
        result.total_return_pct = (current_equity - 100.0)
        result.max_drawdown_pct = max_drawdown
        result.win_rate = (result.winning_trades / result.total_trades * 100) if result.total_trades > 0 else 0
        result.profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')

        if len(returns) > 1:
            mean_return = statistics.mean(returns)
            stdev_return = statistics.stdev(returns)
            # Annualize assuming 6 trades per day roughly
            result.sharpe_ratio = (mean_return / stdev_return) * (252 * 6)**0.5 if stdev_return > 0 else 0

        result.evaluate()
        return result

class OHLCVCollector:
    """Background collector to fill ohlcv_cache from Bybit.

    Runs as an APScheduler job or manual trigger.
    """

    async def collect(
        self, bybit_client, tickers: list[str], timeframe: str = "4h", limit: int = 200,
    ) -> dict:
        """Fetch OHLCV data and persist to DB cache.

        Args:
            bybit_client: BybitClient instance
            tickers: list of symbols to collect
            timeframe: candle timeframe (default 4h for crypto)
            limit: number of candles per ticker

        Returns:
            {"collected": int, "errors": list[str]}
        """
        from src.models.database import async_session
        import asyncio

        collected = 0
        errors = []

        for ticker in tickers:
            try:
                ohlcv = await bybit_client.get_kline(ticker, timeframe, limit)
                if not ohlcv:
                    errors.append(f"{ticker}: no data")
                    continue

                async with async_session() as session:
                    for candle in ohlcv:
                        # Upsert — merge on primary key
                        existing = await session.get(OHLCVCache, (ticker, "CRYPTO", timeframe, candle["timestamp"]))
                        if existing:
                            existing.open = candle["open"]
                            existing.high = candle["high"]
                            existing.low = candle["low"]
                            existing.close = candle["close"]
                            existing.volume = candle["volume"]
                        else:
                            session.add(OHLCVCache(
                                ticker=ticker, market="CRYPTO", timeframe=timeframe,
                                timestamp=candle["timestamp"],
                                open=candle["open"], high=candle["high"],
                                low=candle["low"], close=candle["close"],
                                volume=candle["volume"],
                            ))
                    await session.commit()
                    collected += 1

                # Rate limit: 100ms between requests
                await asyncio.sleep(0.1)
            except Exception as e:
                errors.append(f"{ticker}: {str(e)}")
                logger.error("ohlcv_collect_failed", ticker=ticker, error=str(e))

        logger.info("ohlcv_collect_done", collected=collected, errors=len(errors))
        return {"collected": collected, "errors": errors}
