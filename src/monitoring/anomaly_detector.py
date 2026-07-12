"""Karsa Trading System — Anomaly Detector

Lightweight statistical anomaly detection using rolling z-scores:
- Daily PnL anomalies
- Drawdown velocity
- Win rate degradation (7d vs 30d)

Alerts when z-score exceeds ±2.5.
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

from src.utils.logging import get_logger

logger = get_logger("anomaly_detector")

ZSCORE_THRESHOLD = 2.5
MIN_SAMPLES = 10

@dataclass
class AnomalyMetric:
    """Rolling window for a single metric."""
    name: str
    window: deque = field(default_factory=lambda: deque(maxlen=100))
    last_alert_ts: float = 0
    alert_cooldown: float = 3600  # 1 hour between alerts

    def add(self, value: float) -> None:
        self.window.append((time.time(), value))

    def zscore(self, value: float) -> float | None:
        """Calculate z-score of value against rolling window."""
        if len(self.window) < MIN_SAMPLES:
            return None
        values = [v for _, v in self.window]
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        std = variance ** 0.5
        if std == 0:
            return 0.0
        return (value - mean) / std

    def should_alert(self, zscore: float) -> bool:
        """Check if we should alert (above threshold + cooldown)."""
        if abs(zscore) < ZSCORE_THRESHOLD:
            return False
        if time.time() - self.last_alert_ts < self.alert_cooldown:
            return False
        return True

class AnomalyDetector:
    """Detects anomalies in trading metrics using rolling z-scores."""

    def __init__(self):
        self.metrics: dict[str, AnomalyMetric] = {
            "daily_pnl": AnomalyMetric(name="daily_pnl"),
            "drawdown_velocity": AnomalyMetric(name="drawdown_velocity"),
            "win_rate": AnomalyMetric(name="win_rate"),
        }

    def record(self, metric_name: str, value: float) -> None:
        """Record a new value for a metric."""
        if metric_name in self.metrics:
            self.metrics[metric_name].add(value)

    def check(self, metric_name: str, current_value: float) -> dict | None:
        """Check if current value is anomalous.

        Returns:
            Dict with anomaly info if anomalous, None otherwise.
        """
        metric = self.metrics.get(metric_name)
        if not metric:
            return None

        z = metric.zscore(current_value)
        if z is None:
            return None

        if metric.should_alert(z):
            metric.last_alert_ts = time.time()
            return {
                "metric": metric_name,
                "value": current_value,
                "zscore": round(z, 2),
                "window_size": len(metric.window),
                "mean": round(sum(v for _, v in metric.window) / len(metric.window), 4),
            }
        return None


_detector: AnomalyDetector | None = None


def get_anomaly_detector() -> AnomalyDetector:
    global _detector
    if _detector is None:
        _detector = AnomalyDetector()
    return _detector
