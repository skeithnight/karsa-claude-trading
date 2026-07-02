from __future__ import annotations

"""Karsa Trading System — Perpetual Contract Backtester

Event-driven simulator for crypto perpetuals that accounts for:
1. Funding fee simulation (every 8h based on historical rates)
2. Maker/taker fee tiers (0.02% maker / 0.055% taker)
3. Dynamic slippage model (order size vs orderbook volume)
4. Liquidation check (margin ratio monitoring)

Usage:
  simulator = PerpSimulator(initial_capital=10000)
  result = simulator.run(ohlcv_data, signals, funding_rates)
"""

from dataclasses import dataclass, field
from src.utils.logging import get_logger

logger = get_logger("perp_backtest")

MAKER_FEE = 0.0002
TAKER_FEE = 0.00055
FUNDING_INTERVAL_HOURS = 8
MAINTENANCE_MARGIN_RATE = 0.005


@dataclass
class Trade:
    ticker: str
    side: str  # "LONG" or "SHORT"
    entry_price: float
    quantity: float
    leverage: int
    stop_loss: float
    take_profit: float
    entry_time: int
    exit_price: float = 0.0
    exit_time: int = 0
    pnl: float = 0.0
    funding_paid: float = 0.0
    fees_paid: float = 0.0
    slippage_cost: float = 0.0
    exit_reason: str = ""


@dataclass
class SimulationResult:
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    total_return_pct: float = 0.0
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    total_funding_paid: float = 0.0
    total_fees_paid: float = 0.0
    total_slippage_cost: float = 0.0
    liquidations: int = 0


class PerpSimulator:
    """Event-driven perpetual contract backtester."""

    def __init__(self, initial_capital: float = 10_000.0,
                 maker_fee: float = MAKER_FEE,
                 taker_fee: float = TAKER_FEE,
                 max_leverage: int = 10):
        self.initial_capital = initial_capital
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.max_leverage = max_leverage

    def run(self, ohlcv: list[dict], signals: list[dict],
            funding_rates: list[float] | None = None) -> SimulationResult:
        """Run backtest on OHLCV data with signals.

        Args:
            ohlcv: list of {open, high, low, close, volume, timestamp}
            signals: list of {entry_idx, direction, entry_price, stop_loss, take_profit, leverage}
            funding_rates: list of funding rates per candle (or None for default 0.01%)
        """
        result = SimulationResult()
        equity = self.initial_capital
        peak_equity = equity
        open_trade = None
        default_funding = 0.0001

        for i, candle in enumerate(ohlcv):
            price = candle["close"]
            high = candle["high"]
            low = candle["low"]

            # Open new trade
            for sig in signals:
                if sig.get("entry_idx") != i or open_trade is not None:
                    continue

                direction = sig.get("direction", "LONG")
                entry_price = sig.get("entry_price", price)
                leverage = min(sig.get("leverage", 3), self.max_leverage)
                sl = sig.get("stop_loss", 0)
                tp = sig.get("take_profit", 0)

                slippage = self._estimate_slippage(entry_price, candle.get("volume", 0))
                entry_price *= (1 + slippage) if direction == "LONG" else (1 - slippage)

                risk_amount = equity * 0.01
                stop_distance = abs(entry_price - sl) if sl else entry_price * 0.02
                quantity = (risk_amount / stop_distance) * leverage if stop_distance > 0 else 0
                notional = quantity * entry_price

                if notional / leverage > equity * 0.95:
                    continue

                entry_fee = notional * self.taker_fee
                equity -= entry_fee

                open_trade = Trade(
                    ticker=sig.get("ticker", "BTCUSDT"), side=direction,
                    entry_price=entry_price, quantity=quantity, leverage=leverage,
                    stop_loss=sl, take_profit=tp, entry_time=i,
                    fees_paid=entry_fee, slippage_cost=abs(slippage * notional),
                )
                break

            if not open_trade:
                result.equity_curve.append(equity)
                continue

            # Check stop loss
            if open_trade.side == "LONG" and low <= open_trade.stop_loss > 0:
                equity, t = self._close_trade(open_trade, open_trade.stop_loss, i, "stop_loss", equity)
                result.trades.append(t)
                open_trade = None
            elif open_trade.side == "SHORT" and high >= open_trade.stop_loss > 0:
                equity, t = self._close_trade(open_trade, open_trade.stop_loss, i, "stop_loss", equity)
                result.trades.append(t)
                open_trade = None

            # Check take profit
            if open_trade and open_trade.side == "LONG" and high >= open_trade.take_profit > 0:
                equity, t = self._close_trade(open_trade, open_trade.take_profit, i, "take_profit", equity)
                result.trades.append(t)
                open_trade = None
            elif open_trade and open_trade.side == "SHORT" and low <= open_trade.take_profit > 0:
                equity, t = self._close_trade(open_trade, open_trade.take_profit, i, "take_profit", equity)
                result.trades.append(t)
                open_trade = None

            # Funding every 8h
            if open_trade and i > 0 and i % FUNDING_INTERVAL_HOURS == 0:
                fr = funding_rates[i] if funding_rates and i < len(funding_rates) else default_funding
                notional = open_trade.quantity * price
                funding_cost = notional * fr
                if open_trade.side == "LONG":
                    equity -= funding_cost
                else:
                    equity += funding_cost
                open_trade.funding_paid += funding_cost

            # Liquidation check
            if open_trade:
                liq_price = self._liquidation_price(open_trade)
                if (open_trade.side == "LONG" and low <= liq_price) or \
                   (open_trade.side == "SHORT" and high >= liq_price):
                    equity, t = self._close_trade(open_trade, liq_price, i, "liquidation", equity)
                    result.trades.append(t)
                    result.liquidations += 1
                    open_trade = None

            result.equity_curve.append(equity)
            peak_equity = max(peak_equity, equity)

        # Close remaining
        if open_trade:
            equity, t = self._close_trade(open_trade, ohlcv[-1]["close"], len(ohlcv)-1, "end_of_data", equity)
            result.trades.append(t)

        # Stats
        result.total_return_pct = ((equity - self.initial_capital) / self.initial_capital) * 100
        if result.trades:
            result.win_rate = sum(1 for t in result.trades if t.pnl > 0) / len(result.trades) * 100
        result.total_funding_paid = sum(t.funding_paid for t in result.trades)
        result.total_fees_paid = sum(t.fees_paid for t in result.trades)
        result.total_slippage_cost = sum(t.slippage_cost for t in result.trades)

        # Max drawdown
        if result.equity_curve:
            peak = result.equity_curve[0]
            max_dd = 0
            for eq in result.equity_curve:
                peak = max(peak, eq)
                dd = (peak - eq) / peak * 100 if peak > 0 else 0
                max_dd = max(max_dd, dd)
            result.max_drawdown_pct = max_dd

        # Sharpe ratio
        if len(result.equity_curve) > 1:
            returns = []
            for j in range(1, len(result.equity_curve)):
                if result.equity_curve[j-1] > 0:
                    returns.append((result.equity_curve[j] - result.equity_curve[j-1]) / result.equity_curve[j-1])
            if returns:
                import statistics
                avg_ret = statistics.mean(returns)
                std_ret = statistics.stdev(returns) if len(returns) > 1 else 1
                result.sharpe_ratio = (avg_ret / std_ret) * (2190 ** 0.5) if std_ret > 0 else 0

        return result

    def _close_trade(self, trade: Trade, exit_price: float, idx: int,
                      reason: str, equity: float) -> tuple[float, Trade]:
        slippage = self._estimate_slippage(exit_price, 0)
        if trade.side == "LONG":
            exit_price *= (1 - slippage)
            pnl = (exit_price - trade.entry_price) * trade.quantity
        else:
            exit_price *= (1 + slippage)
            pnl = (trade.entry_price - exit_price) * trade.quantity

        exit_fee = trade.quantity * exit_price * self.taker_fee
        trade.fees_paid += exit_fee
        trade.exit_price = exit_price
        trade.exit_time = idx
        trade.pnl = pnl - trade.fees_paid - trade.funding_paid
        trade.exit_reason = reason

        equity += pnl - exit_fee - trade.funding_paid
        return equity, trade

    def _estimate_slippage(self, price: float, volume: float) -> float:
        """ponytail: simple model — 0.01% base + volume factor. Real model needs orderbook."""
        base = 0.0001
        if volume > 0:
            return base * (1 + max(0, 1 - volume / 1_000_000))
        return base

    def _liquidation_price(self, trade: Trade) -> float:
        if trade.side == "LONG":
            return trade.entry_price * (1 - 1/trade.leverage + MAINTENANCE_MARGIN_RATE)
        else:
            return trade.entry_price * (1 + 1/trade.leverage - MAINTENANCE_MARGIN_RATE)
