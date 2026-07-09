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
# DOMAIN 1b — Session Performance Metrics
# ============================================================

SESSION_RETURN_PCT = Gauge(
    "karsa_session_return_pct", "Total return % since session start")

DAILY_RETURN_PCT = Gauge(
    "karsa_daily_return_pct", "Today's return %")

MAX_DRAWDOWN_PCT = Gauge(
    "karsa_max_drawdown_pct", "Max drawdown % in current session")

PROFIT_FACTOR = Gauge(
    "karsa_profit_factor", "Gross profit / gross loss ratio")

TOTAL_TRADES_COUNT = Gauge(
    "karsa_total_trades_count", "Total trades executed in session")

WINNING_TRADES = Gauge(
    "karsa_winning_trades_count", "Winning trades in session")

LOSING_TRADES = Gauge(
    "karsa_losing_trades_count", "Losing trades in session")

POSITION_ALLOCATION = Gauge(
    "karsa_position_allocation_pct", "Position allocation as % of equity", ["ticker"])

BEST_PERFORMER_PCT = Gauge(
    "karsa_best_performer_pct", "Best performing position return %")

WORST_PERFORMER_PCT = Gauge(
    "karsa_worst_performer_pct", "Worst performing position return %")

AVG_HOLDING_HOURS = Gauge(
    "karsa_avg_holding_time_hours", "Average holding time in hours")

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

POSITION_ENTRY_PRICE = Gauge(
    "karsa_position_entry_price_usd",
    "Entry price per open position",
    ["ticker", "side"])

POSITION_MARK_PRICE = Gauge(
    "karsa_position_mark_price_usd",
    "Current mark price per open position",
    ["ticker", "side"])

POSITION_SIZE = Gauge(
    "karsa_position_size_qty",
    "Position size in base currency",
    ["ticker", "side"])

POSITION_LEVERAGE = Gauge(
    "karsa_position_leverage",
    "Position leverage",
    ["ticker", "side"])

POSITION_DATA = Gauge(
    "karsa_position_data",
    "Combined position data for Grafana table",
    ["ticker", "side", "field"])

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
# DOMAIN 5c — Database Pool Health
# ============================================================

DB_POOL_CHECKED_OUT = Gauge(
    "karsa_db_pool_checked_out",
    "Number of DB connections currently checked out from the pool")

DB_POOL_OVERFLOW = Gauge(
    "karsa_db_pool_overflow",
    "DB pool overflow counter (negative value indicates leaked connections)")

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

SCAN_DURATION = Histogram("karsa_scan_duration_seconds", "Full crypto scan cycle time", ["market"])
COIN_SCAN_DURATION = Histogram("karsa_coin_scan_duration_seconds", "Per-coin analyst call time", ["ticker"])
REGIME_CLASSIFY_DURATION = Histogram("karsa_regime_classify_seconds", "Regime classification time")

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


# ============================================================
# AUTONOMOUS SESSION METRICS
# ============================================================

AUTO_SESSION_ACTIVE = Gauge(
    "karsa_auto_session_active",
    "1 if autonomous session is running, 0 if stopped",
)

AUTO_SESSION_CASH_USD = Gauge(
    "karsa_auto_session_available_cash_usd",
    "Available cash (excluding floating PnL)",
)

AUTO_SESSION_REALIZED_PNL = Gauge(
    "karsa_auto_session_realized_pnl_usd",
    "Realized PnL during autonomous session",
)

AUTO_SESSION_UNREALIZED_PNL = Gauge(
    "karsa_auto_session_unrealized_pnl_usd",
    "Unrealized MTM PnL during autonomous session",
)

AUTO_SESSION_TRADES_TOTAL = Counter(
    "karsa_auto_session_trades_total",
    "Total trades taken during autonomous session",
    ["result"],
)

AUTO_SESSION_REGIME_PAUSES = Counter(
    "karsa_auto_session_regime_pauses_total",
    "Times loop paused due to bad regime",
)

# ============================================================
# 7. Architecture Components
# ============================================================

EVENT_BUS_ACTIVE = Gauge(
    "karsa_event_bus_active",
    "1 if event bus is active and publishing",
)

EXIT_ENGINE_BLOCKS = Counter(
    "karsa_exit_engine_blocks_total",
    "Times Exit Engine blocked a trailing/SL action",
    ["decision"],
)

EVENTS_TOTAL = Counter(
    "karsa_events_total",
    "Business events published",
    ["event_type"],
)

POSITION_MANAGER_WRITES = Counter(
    "karsa_position_manager_writes_total",
    "Position Manager DB writes by operation",
    ["operation"],
)

# ============================================================
# DOMAIN 7 — Operations Dashboard (ASM ops panels)
# ============================================================

ASM_STATE = Gauge(
    "karsa_asm_state",
    "ASM tri-state: 0=disabled, 1=idle (no positions), 2=trading (has positions)",
)

BYBIT_WS_CONNECTED = Gauge(
    "karsa_bybit_ws_connected",
    "1 if Bybit WebSocket is connected, 0 otherwise",
)

SIGNALS_RECEIVED = Counter(
    "karsa_signals_received_total",
    "Signals received from analysts before validation",
    ["market"],
)

SIGNALS_VALIDATED = Counter(
    "karsa_signals_validated_total",
    "Signals that passed validation (before risk gate)",
    ["market"],
)

RISK_STATUS = Gauge(
    "karsa_risk_status",
    "Aggregate risk status: 0=normal, 1=warning (DD>2%), 2=critical (kill/breaker)",
)


def record_asm_state(state: int):
    """Update ASM state. Call from ASM start/stop and position changes."""
    ASM_STATE.set(state)


def record_ws_connected(connected: bool):
    """Update Bybit WS connection state."""
    BYBIT_WS_CONNECTED.set(1 if connected else 0)


def record_signal_received(market: str = "crypto"):
    """Increment received signal counter. Call when analyst returns a signal."""
    SIGNALS_RECEIVED.labels(market=market).inc()


def record_signal_validated(market: str = "crypto"):
    """Increment validated signal counter. Call after _validate_signal passes."""
    SIGNALS_VALIDATED.labels(market=market).inc()


def update_risk_status(kill_active: bool = False, cb_active: bool = False, dd_pct: float = 0):
    """Set aggregate risk status. Callers pass known state."""
    if kill_active or cb_active:
        RISK_STATUS.set(2)
    elif dd_pct > 2:
        RISK_STATUS.set(1)
    else:
        RISK_STATUS.set(0)


def record_exit_engine_block(decision: str):
    """Call when ExitEngine blocks a trailing stop action."""
    EXIT_ENGINE_BLOCKS.labels(decision=decision).inc()


def record_event(event_type: str):
    """Increment event counter. Call from publish_event()."""
    EVENTS_TOTAL.labels(event_type=event_type).inc()


def record_pm_write(operation: str):
    """Increment Position Manager write counter."""
    POSITION_MANAGER_WRITES.labels(operation=operation).inc()


# ============================================================
# DOMAIN 7 — Wallet & ASM Dashboard (Grafana Trading Ledger)
# ============================================================

WALLET_TOTAL_EQUITY = Gauge(
    "karsa_wallet_total_equity_usd",
    "Total account equity in USDT")

WALLET_AVAILABLE = Gauge(
    "karsa_wallet_available_usd",
    "Available balance in USDT")

WALLET_USED_MARGIN = Gauge(
    "karsa_wallet_used_margin_usd",
    "Margin used in USDT")

ASM_UPTIME_SECONDS = Gauge(
    "karsa_asm_uptime_seconds",
    "Current ASM session uptime in seconds")

ASM_NEXT_SCAN_SECONDS = Gauge(
    "karsa_asm_next_scan_seconds",
    "Seconds until next ASM scan")

TRADE_CLOSED_TOTAL = Counter(
    "karsa_trade_closed_total",
    "Total closed trades",
    ["result"])

TRADE_CLOSED_PNL = Histogram(
    "karsa_trade_closed_pnl_usd",
    "Realized PnL per closed trade in USD",
    buckets=[-500, -200, -100, -50, -10, 0, 10, 50, 100, 200, 500])

REALIZED_PNL_TOTAL = Counter(
    "karsa_realized_pnl_total",
    "Cumulative realized PnL in USD across all closed trades",
    ["symbol"],
)

TRADE_DETAIL = Gauge(
    "karsa_trade_detail",
    "Detailed trade record for table display",
    ["ticker", "exit_price", "result", "closed_time"])

# --- Fee Tracking Metrics (Phase 4) ---

TRADING_FEES_USD = Gauge(
    "karsa_trading_fees_usd",
    "Cumulative trading fees in USD per ticker",
    ["ticker"])

TRADE_FEE_PER_TRADE = Histogram(
    "karsa_trade_fee_usd",
    "Fee per trade in USD",
    ["ticker", "side"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 50.0])

NET_ROI_PCT = Gauge(
    "karsa_net_roi_pct",
    "Net ROI after fees as percentage",
    ["ticker"])


def update_wallet_metrics(total_equity: float, available: float, used_margin: float):
    """Update wallet gauges. Call from ASM loop or bot dashboard."""
    WALLET_TOTAL_EQUITY.set(total_equity)
    WALLET_AVAILABLE.set(available)
    WALLET_USED_MARGIN.set(used_margin)


def update_asm_uptime(start_time: float):
    """Update ASM uptime gauge from session start timestamp."""
    import time
    uptime = max(0, time.time() - start_time)
    ASM_UPTIME_SECONDS.set(uptime)


def update_asm_next_scan(seconds: float):
    """Update next scan countdown gauge."""
    ASM_NEXT_SCAN_SECONDS.set(max(0, seconds))


def record_trade_close(pnl: float, result: str, ticker: str = "", exit_price: float = 0.0, closed_time: str = ""):
    """Record a closed trade. result: 'win' or 'loss'."""
    TRADE_CLOSED_TOTAL.labels(result=result).inc()
    TRADE_CLOSED_PNL.observe(pnl)
    if ticker:
        REALIZED_PNL_TOTAL.labels(symbol=ticker).inc(pnl)
        TRADE_DETAIL.labels(
            ticker=ticker,
            exit_price=str(round(exit_price, 4)),
            result=result,
            closed_time=closed_time,
        ).set(1)


def record_trading_fee(ticker: str, side: str, fee_usd: float):
    """Record a trading fee for a single trade."""
    TRADE_FEE_PER_TRADE.labels(ticker=ticker, side=side).observe(fee_usd)
    TRADING_FEES_USD.labels(ticker=ticker).set(fee_usd)


def update_net_roi(ticker: str, net_roi_pct: float):
    """Update net ROI after fees for a ticker."""
    NET_ROI_PCT.labels(ticker=ticker).set(net_roi_pct)


# ============================================================
# DOMAIN 8 — Performance Gate v2 Metrics
# ============================================================

PERF_GATE_DYNAMIC_STOP_ACTIVE = Gauge(
    "karsa_perf_gate_dynamic_stop_active",
    "1 if position has an active dynamic stop, 0 otherwise",
    ["ticker"],
)

PERF_GATE_DRAWDOWN_TRIGGER_TOTAL = Counter(
    "karsa_perf_gate_drawdown_trigger_total",
    "Times drawdown-from-peak triggered AI judge",
    ["ticker"],
)

PERF_GATE_PRICE_STALE_SKIP_TOTAL = Counter(
    "karsa_perf_gate_price_stale_skip_total",
    "Times hard fail was skipped due to stale price data",
    ["ticker"],
)

PERF_GATE_CONSECUTIVE_HOLDS = Gauge(
    "karsa_perf_gate_consecutive_holds",
    "Current consecutive AI hold count per position",
    ["ticker"],
)

PERF_GATE_ZONE_TOTAL = Counter(
    "karsa_perf_gate_zone_total",
    "Performance gate zone classifications",
    ["zone", "bucket"],
)

PERF_GATE_EXIT_TOTAL = Counter(
    "karsa_perf_gate_exit_total",
    "Performance gate exits by reason type",
    ["reason_type"],
)


def update_dynamic_stop_active(ticker: str, active: bool):
    """Set dynamic stop gauge for a position."""
    PERF_GATE_DYNAMIC_STOP_ACTIVE.labels(ticker=ticker).set(1 if active else 0)


def record_drawdown_trigger(ticker: str):
    """Record a drawdown-from-peak trigger."""
    PERF_GATE_DRAWDOWN_TRIGGER_TOTAL.labels(ticker=ticker).inc()


def record_price_stale_skip(ticker: str):
    """Record a stale price skip."""
    PERF_GATE_PRICE_STALE_SKIP_TOTAL.labels(ticker=ticker).inc()


def update_consecutive_holds(ticker: str, count: int):
    """Update consecutive hold count for a position."""
    PERF_GATE_CONSECUTIVE_HOLDS.labels(ticker=ticker).set(count)


def record_perf_gate_zone(zone: str, bucket: str):
    """Record a zone classification."""
    PERF_GATE_ZONE_TOTAL.labels(zone=zone, bucket=bucket).inc()


def record_perf_gate_exit(reason_type: str):
    """Record a performance gate exit by reason type."""
    PERF_GATE_EXIT_TOTAL.labels(reason_type=reason_type).inc()


# ============================================================
# DOMAIN 9 — LLM & Token Usage
# ============================================================

LLM_TOKENS_INPUT = Counter(
    "karsa_llm_tokens_input_total",
    "Total input tokens consumed by LLM calls",
    ["agent"]
)

LLM_TOKENS_OUTPUT = Counter(
    "karsa_llm_tokens_output_total",
    "Total output tokens consumed by LLM calls",
    ["agent"]
)


def record_llm_tokens(agent: str, input_tokens: int, output_tokens: int):
    """Record LLM token usage. Call from base agent after LLM call."""
    LLM_TOKENS_INPUT.labels(agent=agent).inc(input_tokens)
    LLM_TOKENS_OUTPUT.labels(agent=agent).inc(output_tokens)


# ============================================================
# DOMAIN 9b — AI Judge Metrics
# ============================================================

AI_JUDGE_DECISIONS_TOTAL = Counter(
    "karsa_ai_judge_decisions_total",
    "AI judge decisions by action type",
    ["action"],  # HOLD, EXIT, TIGHTEN_STOP
)

AI_JUDGE_TIER_USED = Counter(
    "karsa_ai_judge_tier_used_total",
    "AI judge tier usage",
    ["tier"],  # cheap, escalated
)

AI_JUDGE_ESCALATION_TOTAL = Counter(
    "karsa_ai_judge_escalation_total",
    "Times AI judge escalated from Tier 1 to Tier 2",
)

AI_JUDGE_CONFIDENCE_SCORE = Histogram(
    "karsa_ai_judge_confidence_score",
    "AI judge confidence score distribution",
    buckets=[10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
)

AI_JUDGE_LATENCY_SECONDS = Histogram(
    "karsa_ai_judge_latency_seconds",
    "AI judge LLM API latency",
    ["tier"],  # cheap, escalated
    buckets=[0.5, 1, 2, 5, 10, 15, 20, 30],
)


def record_ai_decision(action: str):
    """Record an AI judge decision. Call from position_judge.py."""
    AI_JUDGE_DECISIONS_TOTAL.labels(action=action).inc()


def record_tier_used(tier: str):
    """Record which tier was used. Call from position_judge.py."""
    AI_JUDGE_TIER_USED.labels(tier=tier).inc()


def record_escalation():
    """Record an escalation from Tier 1 to Tier 2. Call from position_judge.py."""
    AI_JUDGE_ESCALATION_TOTAL.inc()


def record_confidence_score(score: int):
    """Record AI judge confidence score. Call from position_judge.py."""
    AI_JUDGE_CONFIDENCE_SCORE.observe(score)


def record_judge_latency(tier: str, seconds: float):
    """Record AI judge LLM latency. Call from position_judge.py."""
    AI_JUDGE_LATENCY_SECONDS.labels(tier=tier).observe(seconds)


# ============================================================
# DOMAIN 10 — Signal Outcomes
# ============================================================

SIGNAL_OUTCOME_TOTAL = Counter(
    "karsa_signal_outcome_total",
    "Signal outcomes by type",
    ["outcome"]  # win, loss, breakeven
)


def record_signal_outcome(outcome: str):
    """Record a signal outcome. Call when trade closes."""
    SIGNAL_OUTCOME_TOTAL.labels(outcome=outcome).inc()


# ============================================================
# DOMAIN 11 — Daily Trade Count
# ============================================================

DAILY_TRADE_COUNT = Gauge(
    "karsa_daily_trade_count",
    "Number of trades executed today"
)


def update_daily_trade_count(count: int):
    """Update daily trade count gauge."""
    DAILY_TRADE_COUNT.set(count)
