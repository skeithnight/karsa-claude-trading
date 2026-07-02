"""Karsa Trading System - Prometheus Metrics

Exposes counters, gauges, and histograms for Grafana dashboards.
Import and call update_* helpers from orchestrator/bot code.

Domains:
  1. Trading Performance (P&L)
  2. Risk Safety (Kill Switch & Circuit Breakers)
  3. Position & Liquidation Health
  4. Order Execution Quality (SOR)
  5. Infrastructure & Scheduler Health
  5b. WebSocket Health
  6. Regime & Intelligence (stubs)
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ============================================================
# EXISTING — Risk Profile & Universe Metrics
# ============================================================

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

UNIVERSE_SIZE = Gauge(
    "karsa_universe_size",
    "Number of coins in current dynamic universe",
)

UNIVERSE_REFRESH_COUNT = Counter(
    "karsa_universe_refresh_total",
    "Universe refresh attempts",
    ["status"],
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

# ============================================================
# DOMAIN 1 — Trading Performance (P&L)
# ============================================================

DAILY_PNL_USD = Gauge(
    "karsa_pnl_daily_usd", "Today realized PnL USD")

UNREALIZED_PNL_USD = Gauge(
    "karsa_pnl_unrealized_usd", "Unrealized PnL USD across all open positions")

PORTFOLIO_EQUITY_USD = Gauge(
    "karsa_portfolio_equity_usd", "Total portfolio equity in USDT")

CURRENT_DRAWDOWN_PCT = Gauge(
    "karsa_drawdown_pct", "Current drawdown % from peak equity")

WIN_RATE_7D = Gauge(
    "karsa_win_rate_7d", "Rolling 7-day win rate (0-1)")

TRADE_PNL_USD = Histogram(
    "karsa_trade_pnl_usd",
    "Per-trade realized PnL in USD",
    ["ticker", "direction"],
    buckets=[-500, -200, -100, -50, -20, 0, 20, 50, 100, 200, 500],
)

AVG_RR_RATIO_7D = Gauge(
    "karsa_avg_rr_ratio_7d", "7-day average realized R:R ratio")

# ============================================================
# DOMAIN 2 — Risk Safety (Kill Switch & Circuit Breakers)
# ============================================================

KILL_SWITCH_ACTIVE = Gauge(
    "karsa_kill_switch_active",
    "1 if emergency kill switch is active, 0 otherwise")

CIRCUIT_BREAKER_ACTIVE = Gauge(
    "karsa_circuit_breaker_active",
    "1 if circuit breaker is active",
    ["breaker_type"])

VOLATILITY_SPIKE_PCT = Gauge(
    "karsa_volatility_spike_pct",
    "Max price move % in last 15 min",
    ["ticker"])

DAILY_LOSS_PCT = Gauge(
    "karsa_daily_loss_pct",
    "Current day unrealized+realized loss as % of equity")

CORRELATION_LOSS_RATIO = Gauge(
    "karsa_correlation_loss_ratio",
    "Fraction of positions losing within correlation tier",
    ["tier"])

# ============================================================
# DOMAIN 3 — Position & Liquidation Health
# ============================================================

OPEN_POSITIONS = Gauge(
    "karsa_open_positions_count", "Number of currently open positions")

POSITION_PNL = Gauge(
    "karsa_position_unrealized_pnl_usd",
    "Unrealized PnL per open position",
    ["ticker", "side"])

# ponytail: no "level" label — alert threshold handles danger/warning
LIQ_DISTANCE_PCT = Gauge(
    "karsa_liquidation_distance_pct",
    "Distance to liquidation as % of entry price",
    ["ticker", "side"])

POSITION_AGE_HOURS = Gauge(
    "karsa_position_age_hours",
    "Age of open position in hours",
    ["ticker"])

FUNDING_COST = Gauge(
    "karsa_funding_cost_8h_usd",
    "Funding cost per 8h interval per position",
    ["ticker"])

FUNDING_RATE = Gauge(
    "karsa_funding_rate_pct",
    "Current funding rate %",
    ["ticker"])

# ============================================================
# DOMAIN 4 — Order Execution Quality (SOR)
# ============================================================

ORDER_FILL = Counter(
    "karsa_order_fill_total",
    "Total orders filled",
    ["ticker", "order_type", "direction"])

ORDER_SLIPPAGE_BPS = Histogram(
    "karsa_order_slippage_bps",
    "Order slippage in basis points",
    ["ticker", "direction"],
    buckets=[0, 1, 2, 5, 10, 20, 50, 100])

FILL_LATENCY = Histogram(
    "karsa_order_fill_latency_seconds",
    "Order fill latency (signal to fill)",
    buckets=[1, 5, 15, 30, 60, 120, 300])

LIMIT_FALLBACK = Counter(
    "karsa_limit_order_fallback_total",
    "Limit order fell back to market order",
    ["ticker", "reason"])

ORDER_REJECTED_EXCHANGE = Counter(
    "karsa_order_rejected_exchange_total",
    "Orders rejected by Bybit exchange",
    ["ticker", "error_code"])

# ============================================================
# DOMAIN 5 — Infrastructure & Scheduler Health
# ============================================================

JOB_LAST_RUN = Gauge(
    "karsa_job_last_run_timestamp_seconds",
    "Unix timestamp of last successful job run",
    ["job_id"])

JOB_DURATION = Histogram(
    "karsa_job_duration_seconds",
    "Scheduled job execution duration",
    ["job_id"],
    buckets=[0.1, 0.5, 1, 5, 10, 30, 60, 120, 300])

JOB_ERRORS = Counter(
    "karsa_job_errors_total",
    "Scheduled job failures",
    ["job_id"])

BYBIT_LATENCY = Histogram(
    "karsa_bybit_api_latency_seconds",
    "Bybit REST API call latency",
    ["endpoint"],
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10])

BYBIT_ERRORS = Counter(
    "karsa_bybit_api_errors_total",
    "Bybit API call errors",
    ["endpoint", "error_type"])

REDIS_CONNECTED = Gauge(
    "karsa_redis_connected", "1 if Redis is reachable, 0 otherwise")

WARP_CONNECTED = Gauge(
    "karsa_warp_connected", "1 if Cloudflare WARP proxy is reachable, 0 otherwise")

# ============================================================
# DOMAIN 5b — WebSocket Health
# ============================================================

WS_LAST_MESSAGE_TIMESTAMP = Gauge(
    "karsa_ws_last_message_timestamp_seconds",
    "Last message received from Bybit WS")
# ponytail: init to now so `time() - metric` shows uptime, not 56 years
import time as _time
WS_LAST_MESSAGE_TIMESTAMP.set(_time.time())

WS_RECONNECT_TOTAL = Counter(
    "karsa_ws_reconnect_total", "WS reconnection count")

SL_BREACH_TOTAL = Counter(
    "karsa_sl_breach_total",
    "Stop-loss breaches detected",
    ["ticker"])

SL_EXECUTION_TOTAL = Counter(
    "karsa_sl_execution_total",
    "Stop-loss execution outcomes",
    ["ticker", "status"])

# ============================================================
# DOMAIN 6 — Regime & Intelligence (stubs for P3)
# ============================================================

CRYPTO_REGIME = Gauge(
    "karsa_crypto_regime",
    "Current crypto market regime (0=CHOP 1=MR 2=TREND_BEAR 3=TREND_BULL)")

REGIME_SIZE_MULT = Gauge(
    "karsa_regime_size_multiplier",
    "Position size multiplier from current regime (0.5-1.2)")

DOMINANCE = Gauge(
    "karsa_btc_dominance_pct",
    "BTC market dominance %")

LLM_LATENCY = Histogram(
    "karsa_signal_llm_latency_seconds",
    "Claude API call duration for signal generation",
    ["agent"],
    buckets=[1, 2, 5, 10, 20, 30, 60])

# ============================================================
# HELPER FUNCTIONS — Existing
# ============================================================

_PROFILE_ENCODING = {"conservative": 0, "semi_aggressive": 1, "aggressive": 2}


def update_active_profile(profile_name: str):
    ACTIVE_PROFILE.set(_PROFILE_ENCODING.get(profile_name, 0))


def record_profile_change(from_profile: str, to_profile: str):
    PROFILE_CHANGE_COUNT.labels(from_profile=from_profile, to_profile=to_profile).inc()
    update_active_profile(to_profile)


def record_signal_rejection(profile: str, reason: str):
    SIGNAL_REJECTION_COUNT.labels(profile=profile, reason=reason).inc()


def record_signal_executed(profile: str):
    SIGNAL_EXECUTED_COUNT.labels(profile=profile).inc()


def record_position_size(profile: str, pct: float):
    POSITION_SIZE_PCT.labels(profile=profile).observe(pct)


def record_signal_confidence(profile: str, confidence: int, executed: bool):
    outcome = "executed" if executed else "rejected"
    SIGNAL_CONFIDENCE.labels(profile=profile, outcome=outcome).observe(confidence)


def update_universe_size(count: int):
    UNIVERSE_SIZE.set(count)


def record_universe_refresh(status: str, duration_seconds: float):
    UNIVERSE_REFRESH_COUNT.labels(status=status).inc()
    UNIVERSE_REFRESH_DURATION.observe(duration_seconds)


def update_coin_score(ticker: str, score: float):
    UNIVERSE_COIN_SCORE.labels(ticker=ticker).set(score)


# ============================================================
# HELPER FUNCTIONS — New domains
# ============================================================


def update_kill_switch(active: bool):
    """Set kill switch gauge. Call from emergency.py."""
    KILL_SWITCH_ACTIVE.set(1 if active else 0)


def update_circuit_breaker(breaker_type: str, active: bool):
    """Set per-breaker state gauge. Call from circuit_breaker.py."""
    CIRCUIT_BREAKER_ACTIVE.labels(breaker_type=breaker_type).set(1 if active else 0)


def record_order_fill(ticker: str, order_type: str, direction: str):
    """Record an order fill event. Call from sor.py."""
    ORDER_FILL.labels(ticker=ticker, order_type=order_type, direction=direction).inc()


def record_slippage(ticker: str, direction: str, bps: float):
    """Record order slippage in basis points. Call from sor.py."""
    ORDER_SLIPPAGE_BPS.labels(ticker=ticker, direction=direction).observe(bps)


def record_fill_latency(seconds: float):
    """Record signal-to-fill latency. Call from sor.py."""
    FILL_LATENCY.observe(seconds)


def record_limit_fallback(ticker: str, reason: str):
    """Record limit->market fallback. Call from sor.py."""
    LIMIT_FALLBACK.labels(ticker=ticker, reason=reason).inc()


def record_order_rejected(ticker: str, error_code: str):
    """Record exchange-level order rejection. Call from sor.py."""
    ORDER_REJECTED_EXCHANGE.labels(ticker=ticker, error_code=error_code).inc()


def record_bybit_call(endpoint: str, duration: float, error: str | None = None):
    """Record Bybit API call latency and errors. Call from bybit_client.py."""
    BYBIT_LATENCY.labels(endpoint=endpoint).observe(duration)
    if error:
        BYBIT_ERRORS.labels(endpoint=endpoint, error_type=error).inc()


def update_ws_health_tick():
    """Call on each WS tick message. Call from websocket_manager.py."""
    import time
    WS_LAST_MESSAGE_TIMESTAMP.set(time.time())


def update_ws_reconnect():
    """Call on WS reconnection. Call from websocket_manager.py."""
    WS_RECONNECT_TOTAL.inc()


def record_sl_breach(ticker: str):
    """Record a stop-loss breach. Call from sl_engine.py."""
    SL_BREACH_TOTAL.labels(ticker=ticker).inc()


def record_sl_execution(ticker: str, success: bool):
    """Record stop-loss execution outcome. Call from sl_engine.py."""
    status = "success" if success else "failed"
    SL_EXECUTION_TOTAL.labels(ticker=ticker, status=status).inc()
