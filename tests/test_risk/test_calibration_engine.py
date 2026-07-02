"""Tests for ConfidenceCalibrator — multiplier calculation, signal calibration."""

import sys
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.risk.calibration_engine import (
    ConfidenceCalibrator, MIN_SAMPLE_SIZE, MULTIPLIER_FLOOR, MULTIPLIER_CEIL,
)


def _make_trades(n, win_rate=0.5, avg_confidence=70):
    """Generate mock ClosedPaperTrade objects."""
    trades = []
    for i in range(n):
        t = MagicMock()
        t.realized_pnl = 100.0 if i < int(n * win_rate) else -50.0
        t.confidence_score = avg_confidence
        trades.append(t)
    return trades


@pytest.fixture
def calibrator():
    return ConfidenceCalibrator(window_size=100)


class TestCalculateMultiplier:
    @pytest.mark.asyncio
    async def test_insufficient_trades_returns_one(self, calibrator):
        trades = _make_trades(10)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = trades

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_db_module = MagicMock()
        mock_db_module.async_session = MagicMock(return_value=mock_session)
        with patch.dict("sys.modules", {"src.models.database": mock_db_module}):
            result = await calibrator.calculate_multiplier()
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_well_calibrated(self, calibrator):
        """Win rate ~ predicted confidence -> multiplier ~ 1.0."""
        trades = _make_trades(50, win_rate=0.7, avg_confidence=70)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = trades

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_db_module = MagicMock()
        mock_db_module.async_session = MagicMock(return_value=mock_session)
        with patch.dict("sys.modules", {"src.models.database": mock_db_module}):
            result = await calibrator.calculate_multiplier()
        assert 0.9 <= result <= 1.1

    @pytest.mark.asyncio
    async def test_overconfident_clamps_to_floor(self, calibrator):
        """High predicted confidence, low actual win -> multiplier hits floor."""
        trades = _make_trades(50, win_rate=0.1, avg_confidence=90)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = trades

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_db_module = MagicMock()
        mock_db_module.async_session = MagicMock(return_value=mock_session)
        with patch.dict("sys.modules", {"src.models.database": mock_db_module}):
            result = await calibrator.calculate_multiplier()
        assert result == MULTIPLIER_FLOOR

    @pytest.mark.asyncio
    async def test_underconfident_clamps_to_ceil(self, calibrator):
        """Low predicted, high actual -> multiplier hits ceil."""
        trades = _make_trades(50, win_rate=0.95, avg_confidence=20)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = trades

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_db_module = MagicMock()
        mock_db_module.async_session = MagicMock(return_value=mock_session)
        with patch.dict("sys.modules", {"src.models.database": mock_db_module}):
            result = await calibrator.calculate_multiplier()
        assert result == MULTIPLIER_CEIL

    @pytest.mark.asyncio
    async def test_no_confidence_scores_returns_one(self, calibrator):
        trades = _make_trades(50)
        for t in trades:
            t.confidence_score = None
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = trades

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_db_module = MagicMock()
        mock_db_module.async_session = MagicMock(return_value=mock_session)
        with patch.dict("sys.modules", {"src.models.database": mock_db_module}):
            result = await calibrator.calculate_multiplier()
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_exception_returns_one(self, calibrator):
        mock_session = AsyncMock()
        mock_session.execute.side_effect = Exception("DB down")
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_db_module = MagicMock()
        mock_db_module.async_session = MagicMock(return_value=mock_session)
        with patch.dict("sys.modules", {"src.models.database": mock_db_module}):
            result = await calibrator.calculate_multiplier()
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_zero_avg_confidence_returns_one(self, calibrator):
        trades = _make_trades(50)
        for t in trades:
            t.confidence_score = 0
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = trades

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_db_module = MagicMock()
        mock_db_module.async_session = MagicMock(return_value=mock_session)
        with patch.dict("sys.modules", {"src.models.database": mock_db_module}):
            result = await calibrator.calculate_multiplier()
        assert result == 1.0


class TestCalibrateSignal:
    @pytest.mark.asyncio
    async def test_adjusts_score(self, calibrator):
        calibrator._cached_multiplier = 0.8
        result = await calibrator.calibrate_signal(80.0)
        assert result == 64.0

    @pytest.mark.asyncio
    async def test_clamps_to_100(self, calibrator):
        calibrator._cached_multiplier = 1.5
        result = await calibrator.calibrate_signal(90.0)
        assert result == 100.0

    @pytest.mark.asyncio
    async def test_clamps_to_0(self, calibrator):
        calibrator._cached_multiplier = 0.5
        result = await calibrator.calibrate_signal(0.0)
        assert result == 0.0


class TestCache:
    @pytest.mark.asyncio
    async def test_caches_multiplier(self, calibrator):
        calibrator._cached_multiplier = 1.2
        result = await calibrator.get_multiplier()
        assert result == 1.2

    @pytest.mark.asyncio
    async def test_invalidate_cache(self, calibrator):
        calibrator._cached_multiplier = 1.2
        await calibrator.invalidate_cache()
        assert calibrator._cached_multiplier is None
