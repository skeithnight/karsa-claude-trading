"""Karsa Trading System - Prometheus Metrics for Risk Profile & Universe

Exposes counters, gauges, and histograms for Grafana dashboards.
Import and call update_* helpers from orchestrator/bot code.
"""

from prometheus_client import Counter, Gauge, Histogram

# --- Risk Profile Metrics ---

PROFILE_CHANGE_COUNT = Counter(
    "karsa_risk_profile_changes_total",
    "Total risk profile changes",
    ["from_profile", "to_profile"],
)

ACTIVE_PROFILE = Gauge(
    "karsa_active_risk_profile",
    "Currently active risk profile (0=conservative, 1=semi_aggressive, 2=aggressive)",
)

SIGNAL_REJECTION_COUNT = Counter(
    "karsa_signal_rejections_total",
    "Signals rejected by risk profile",
    ["profile", "reason"],
)

SIGNAL_EXECUTED_COUNT = Counter(
    "karsa_signals_executed_total",
    "Signals that passed risk gates",
    ["profile"],
)

POSITION_SIZE_PCT = Histogram(
    "karsa_position_size_pct",
    "Position size as % of equity",
    ["profile"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.075, 0.1],
)

SIGNAL_CONFIDENCE = Histogram(
    "karsa_signal_confidence",
    "Signal confidence distribution",
    ["profile", "outcome"],
    buckets=[30, 40, 50, 60, 70, 80, 90, 100],
)

# --- Universe Metrics ---

UNIVERSE_SIZE = Gauge(
    "karsa_universe_size",
    "Number of coins in current dynamic universe",
)

UNIVERSE_REFRESH_COUNT = Counter(
    "karsa_universe_refresh_total",
    "Universe refresh attempts",
    ["status"],  # success / failure
)

UNIVERSE_REFRESH_DURATION = Histogram(
    "karsa_universe_refresh_duration_seconds",
    "Universe refresh duration",
    buckets=[1, 2, 5, 10, 30, 60],
)

UNIVERSE_COIN_SCORE = Gauge(
    "karsa_universe_coin_score",
    "Individual coin score in universe",
    ["ticker"],
)

# --- Profile encoding ---
_PROFILE_ENCODING = {"conservative": 0, "semi_aggressive": 1, "aggressive": 2}


def update_active_profile(profile_name: str):
    """Update the active profile gauge."""
    ACTIVE_PROFILE.set(_PROFILE_ENCODING.get(profile_name, 0))


def record_profile_change(from_profile: str, to_profile: str):
    """Record a profile change event."""
    PROFILE_CHANGE_COUNT.labels(from_profile=from_profile, to_profile=to_profile).inc()
    update_active_profile(to_profile)


def record_signal_rejection(profile: str, reason: str):
    """Record a signal rejection."""
    SIGNAL_REJECTION_COUNT.labels(profile=profile, reason=reason).inc()


def record_signal_executed(profile: str):
    """Record a signal that passed risk gates."""
    SIGNAL_EXECUTED_COUNT.labels(profile=profile).inc()


def record_position_size(profile: str, pct: float):
    """Record position size as % of equity."""
    POSITION_SIZE_PCT.labels(profile=profile).observe(pct)


def record_signal_confidence(profile: str, confidence: int, executed: bool):
    """Record signal confidence score."""
    outcome = "executed" if executed else "rejected"
    SIGNAL_CONFIDENCE.labels(profile=profile, outcome=outcome).observe(confidence)


def update_universe_size(count: int):
    """Update universe size gauge."""
    UNIVERSE_SIZE.set(count)


def record_universe_refresh(status: str, duration_seconds: float):
    """Record a universe refresh event."""
    UNIVERSE_REFRESH_COUNT.labels(status=status).inc()
    UNIVERSE_REFRESH_DURATION.observe(duration_seconds)


def update_coin_score(ticker: str, score: float):
    """Update individual coin score gauge."""
    UNIVERSE_COIN_SCORE.labels(ticker=ticker).set(score)
