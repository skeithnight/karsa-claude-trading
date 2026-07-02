"""Tests for PerformanceTracker — equity curve, drawdown, trade stats."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock

from src.advisory.performance_tracker import PerformanceTracker


# ── Helpers ────────────────────────────────────────────────────────────────────

class FakeSnapshot:
    """Mimics a CryptoPnLSnapshot ORM row."""
    def __init__(self, date, equity=10000, realized=0, unrealized=0, funding=0, total=0, positions=0):
        self.snapshot_date = date
        self.equity = equity
        self.realized_pnl = realized
        self.unrealized_pnl = unrealized
        self.funding_costs = funding
        self.total_pnl = total
        self.open_positions = positions


class FakeTrade:
    """Mimics a ClosedPaperTrade ORM row."""
    def __init__(self, entry_date, exit_date=None, realized_pnl=0, realized_pnl_pct=0, market="CRYPTO"):
        self.id = 1
        self.entry_date = entry_date
        self.exit_date = exit_date or entry_date
        self.realized_pnl = realized_pnl
        self.realized_pnl_pct = realized_pnl_pct
        self.market = market


class FakeRegimeHistory:
    """Mimics a CryptoRegimeHistory ORM row."""
    def __init__(self, timestamp, regime="TREND_BULL"):
        self.timestamp = timestamp
        self.regime = regime


def _mock_session(query_results):
    """Build a mock async_session context manager that returns query_results in order.

    Args:
        query_results: list of result objects to return from successive session.execute() calls.
            Each element is a dict with optional keys:
            - "scalars": value for .scalars().all()
            - "scalar": value for .scalar()
            - "scalar_one_or_none": value for .scalar_one_or_none()
            - "one": value for .one()
            If a plain value (not dict), it's used for all accessors.
    """
    session = AsyncMock()
    call_count = {"n": 0}

    async def execute_side_effect(*args, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        mock_result = MagicMock()
        if idx < len(query_results):
            entry = query_results[idx]
            if isinstance(entry, dict):
                mock_result.scalars.return_value.all.return_value = entry.get("scalars", [])
                mock_result.scalar_one_or_none.return_value = entry.get("scalar_one_or_none", None)
                mock_result.scalar.return_value = entry.get("scalar", 0)
                mock_result.one.return_value = entry.get("one", None)
            else:
                mock_result.scalars.return_value.all.return_value = entry
                mock_result.scalar_one_or_none.return_value = entry
                mock_result.scalar.return_value = entry
                mock_result.one.return_value = entry
        else:
            mock_result.scalars.return_value.all.return_value = []
            mock_result.scalar_one_or_none.return_value = None
            mock_result.scalar.return_value = 0
        return mock_result

    session.execute = AsyncMock(side_effect=execute_side_effect)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ── get_equity_curve ───────────────────────────────────────────────────────────

class TestGetEquityCurve:
    @pytest.mark.asyncio
    async def test_returns_formatted_list(self):
        snap = FakeSnapshot(
            date=datetime(2026, 7, 1, tzinfo=None),
            equity=12000, realized=500, unrealized=200, funding=-10, total=690, positions=3
        )
        ctx = _mock_session([{"scalars": [snap]}])

        tracker = PerformanceTracker()
        with patch("src.advisory.performance_tracker.async_session", return_value=ctx):
            result = await tracker.get_equity_curve(days=30)

        assert len(result) == 1
        row = result[0]
        assert row["date"] == "2026-07-01"
        assert row["equity"] == 12000.0
        assert row["realized_pnl"] == 500.0
        assert row["unrealized_pnl"] == 200.0
        assert row["funding_costs"] == -10.0
        assert row["total_pnl"] == 690.0
        assert row["open_positions"] == 3

    @pytest.mark.asyncio
    async def test_empty_db_returns_empty_list(self):
        ctx = _mock_session([{"scalars": []}])
        tracker = PerformanceTracker()
        with patch("src.advisory.performance_tracker.async_session", return_value=ctx):
            result = await tracker.get_equity_curve(days=30)
        assert result == []

    @pytest.mark.asyncio
    async def test_none_fields_default_to_zero(self):
        snap = MagicMock()
        snap.snapshot_date = datetime(2026, 7, 1)
        snap.equity = None
        snap.realized_pnl = None
        snap.unrealized_pnl = None
        snap.funding_costs = None
        snap.total_pnl = None
        snap.open_positions = None

        ctx = _mock_session([{"scalars": [snap]}])
        tracker = PerformanceTracker()
        with patch("src.advisory.performance_tracker.async_session", return_value=ctx):
            result = await tracker.get_equity_curve(days=30)

        row = result[0]
        assert row["equity"] == 0
        assert row["realized_pnl"] == 0
        assert row["open_positions"] == 0

    @pytest.mark.asyncio
    async def test_multiple_snapshots_ordered(self):
        s1 = FakeSnapshot(date=datetime(2026, 6, 1), equity=9000)
        s2 = FakeSnapshot(date=datetime(2026, 6, 15), equity=10500)
        s3 = FakeSnapshot(date=datetime(2026, 7, 1), equity=12000)
        ctx = _mock_session([{"scalars": [s1, s2, s3]}])

        tracker = PerformanceTracker()
        with patch("src.advisory.performance_tracker.async_session", return_value=ctx):
            result = await tracker.get_equity_curve(days=60)

        assert len(result) == 3
        assert result[0]["date"] == "2026-06-01"
        assert result[2]["date"] == "2026-07-01"


# ── get_max_drawdown ───────────────────────────────────────────────────────────

class TestGetMaxDrawdown:
    @pytest.mark.asyncio
    async def test_empty_curve_returns_zeroes(self):
        tracker = PerformanceTracker()
        with patch.object(tracker, "get_equity_curve", new_callable=AsyncMock, return_value=[]):
            result = await tracker.get_max_drawdown(days=30)

        assert result["max_drawdown_pct"] == 0
        assert result["peak_equity"] == 0
        assert result["trough_equity"] == 0

    @pytest.mark.asyncio
    async def test_monotonically_increasing_no_drawdown(self):
        curve = [
            {"equity": 10000},
            {"equity": 11000},
            {"equity": 12000},
        ]
        tracker = PerformanceTracker()
        with patch.object(tracker, "get_equity_curve", new_callable=AsyncMock, return_value=curve):
            result = await tracker.get_max_drawdown(days=30)

        # No drawdown occurred, so peak_eq/trough_eq remain at 0
        assert result["max_drawdown_pct"] == 0.0

    @pytest.mark.asyncio
    async def test_drawdown_calculation(self):
        curve = [
            {"equity": 10000},
            {"equity": 15000},  # peak
            {"equity": 12000},  # 20% drawdown
            {"equity": 14000},
        ]
        tracker = PerformanceTracker()
        with patch.object(tracker, "get_equity_curve", new_callable=AsyncMock, return_value=curve):
            result = await tracker.get_max_drawdown(days=30)

        # DD = (15000 - 12000) / 15000 * 100 = 20%
        assert result["max_drawdown_pct"] == 20.0
        assert result["peak_equity"] == 15000.0
        assert result["trough_equity"] == 12000.0
        assert result["data_points"] == 4

    @pytest.mark.asyncio
    async def test_zero_peak_no_division_error(self):
        curve = [{"equity": 0}, {"equity": 0}]
        tracker = PerformanceTracker()
        with patch.object(tracker, "get_equity_curve", new_callable=AsyncMock, return_value=curve):
            result = await tracker.get_max_drawdown(days=30)

        assert result["max_drawdown_pct"] == 0


# ── get_cumulative_stats ───────────────────────────────────────────────────────

class TestGetCumulativeStats:
    @pytest.mark.asyncio
    async def test_with_data(self):
        stats_row = MagicMock()
        stats_row.total_pnl = 1500.50
        stats_row.total_realized_pnl_pct = 15.5
        stats_row.total_pnl_pct = 15.5
        stats_row.trade_count = 12

        latest_snap = MagicMock()
        latest_snap.equity = 15000.0
        latest_snap.snapshot_date = datetime(2026, 7, 1, 12, 0)

        ctx = _mock_session([
            {"one": stats_row},       # .one() for cumulative stats
            {"scalar": 3},            # .scalar() for open positions count
            {"scalar_one_or_none": latest_snap},  # .scalar_one_or_none() for latest snapshot
        ])
        tracker = PerformanceTracker()
        with patch("src.advisory.performance_tracker.async_session", return_value=ctx):
            result = await tracker.get_cumulative_stats(days=30)

        assert result["total_realized_pnl"] == 1500.50
        assert result["total_realized_pnl_pct"] == 15.5
        assert result["trade_count"] == 12
        assert result["open_positions"] == 3
        assert result["current_equity"] == 15000.0
        assert result["last_snapshot"] == "2026-07-01 12:00"
        assert result["period_days"] == 30

    @pytest.mark.asyncio
    async def test_no_trades(self):
        stats_row = MagicMock()
        stats_row.total_pnl = None
        stats_row.total_pnl_pct = None
        stats_row.trade_count = 0

        ctx = _mock_session([
            {"one": stats_row},
            {"scalar": 0},
            {"scalar_one_or_none": None},
        ])
        tracker = PerformanceTracker()
        with patch("src.advisory.performance_tracker.async_session", return_value=ctx):
            result = await tracker.get_cumulative_stats(days=30)

        assert result["total_realized_pnl"] == 0
        assert result["trade_count"] == 0
        assert result["open_positions"] == 0
        assert result["current_equity"] == 0
        assert result["last_snapshot"] == "Never"

    @pytest.mark.asyncio
    async def test_period_days_propagated(self):
        stats_row = MagicMock()
        stats_row.total_pnl = None
        stats_row.total_pnl_pct = None
        stats_row.trade_count = 0

        ctx = _mock_session([
            {"one": stats_row},
            {"scalar": 0},
            {"scalar_one_or_none": None},
        ])
        tracker = PerformanceTracker()
        with patch("src.advisory.performance_tracker.async_session", return_value=ctx):
            result = await tracker.get_cumulative_stats(days=90)

        assert result["period_days"] == 90


# ── get_regime_performance ─────────────────────────────────────────────────────

class TestGetRegimePerformance:
    @pytest.mark.asyncio
    async def test_grouped_by_regime(self):
        entry_dt = datetime(2026, 6, 15)
        trades = [
            FakeTrade(entry_date=entry_dt, realized_pnl_pct=5.0),
            FakeTrade(entry_date=entry_dt, realized_pnl_pct=-2.0),
            FakeTrade(entry_date=entry_dt, realized_pnl_pct=3.0),
        ]
        regimes = [
            FakeRegimeHistory(timestamp=datetime(2026, 6, 10), regime="TREND_BULL"),
            FakeRegimeHistory(timestamp=datetime(2026, 6, 1), regime="CHOP"),
        ]

        ctx = _mock_session([{"scalars": trades}, {"scalars": regimes}])
        tracker = PerformanceTracker()
        with patch("src.advisory.performance_tracker.async_session", return_value=ctx):
            result = await tracker.get_regime_performance(days=30)

        # All trades have entry_date (2026-06-15), closest regime with ts <= entry is TREND_BULL (June 10)
        assert "TREND_BULL" in result
        assert result["TREND_BULL"]["count"] == 3
        assert result["TREND_BULL"]["wins"] == 2
        assert result["TREND_BULL"]["losses"] == 1
        assert result["TREND_BULL"]["win_rate"] == pytest.approx(66.7, abs=0.1)
        assert result["TREND_BULL"]["pnl"] == pytest.approx(6.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_no_trades_returns_empty(self):
        ctx = _mock_session([{"scalars": []}, {"scalars": []}])
        tracker = PerformanceTracker()
        with patch("src.advisory.performance_tracker.async_session", return_value=ctx):
            result = await tracker.get_regime_performance(days=30)

        assert result == {}

    @pytest.mark.asyncio
    async def test_no_regimes_assigns_unknown(self):
        trades = [
            FakeTrade(entry_date=datetime(2026, 6, 15), realized_pnl_pct=2.0),
        ]
        ctx = _mock_session([{"scalars": trades}, {"scalars": []}])
        tracker = PerformanceTracker()
        with patch("src.advisory.performance_tracker.async_session", return_value=ctx):
            result = await tracker.get_regime_performance(days=30)

        assert "UNKNOWN" in result
        assert result["UNKNOWN"]["count"] == 1

    @pytest.mark.asyncio
    async def test_trade_without_entry_date_skipped(self):
        trades = [
            FakeTrade(entry_date=None, realized_pnl_pct=5.0),
            FakeTrade(entry_date=datetime(2026, 6, 15), realized_pnl_pct=3.0),
        ]
        regimes = [FakeRegimeHistory(timestamp=datetime(2026, 6, 10), regime="CHOP")]
        ctx = _mock_session([{"scalars": trades}, {"scalars": regimes}])
        tracker = PerformanceTracker()
        with patch("src.advisory.performance_tracker.async_session", return_value=ctx):
            result = await tracker.get_regime_performance(days=30)

        # Only the second trade should be counted
        assert result["CHOP"]["count"] == 1

    @pytest.mark.asyncio
    async def test_all_losses(self):
        entry_dt = datetime(2026, 6, 15)
        trades = [
            FakeTrade(entry_date=entry_dt, realized_pnl_pct=-3.0),
            FakeTrade(entry_date=entry_dt, realized_pnl_pct=-1.0),
        ]
        regimes = [FakeRegimeHistory(timestamp=datetime(2026, 6, 1), regime="TREND_BEAR")]
        ctx = _mock_session([{"scalars": trades}, {"scalars": regimes}])
        tracker = PerformanceTracker()
        with patch("src.advisory.performance_tracker.async_session", return_value=ctx):
            result = await tracker.get_regime_performance(days=30)

        assert result["TREND_BEAR"]["wins"] == 0
        assert result["TREND_BEAR"]["losses"] == 2
        assert result["TREND_BEAR"]["win_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_pnl_none_defaults_to_zero(self):
        entry_dt = datetime(2026, 6, 15)
        trade = FakeTrade(entry_date=entry_dt, realized_pnl_pct=None)
        trade.realized_pnl_pct = None
        regimes = [FakeRegimeHistory(timestamp=datetime(2026, 6, 1), regime="MEAN_REVERSION")]
        ctx = _mock_session([{"scalars": [trade]}, {"scalars": regimes}])
        tracker = PerformanceTracker()
        with patch("src.advisory.performance_tracker.async_session", return_value=ctx):
            result = await tracker.get_regime_performance(days=30)

        # None pnl → 0 → counted as loss
        assert result["MEAN_REVERSION"]["count"] == 1
        assert result["MEAN_REVERSION"]["losses"] == 1
        assert result["MEAN_REVERSION"]["pnl"] == 0.0
