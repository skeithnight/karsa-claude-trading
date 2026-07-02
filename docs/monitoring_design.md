# Karsa Crypto Bot — Monitoring Design
### CIO / DevOps Perspective · Prometheus + Grafana

---

## Executive Summary

This is a **fully autonomous crypto trading system** executing real money on Bybit 24/7.  
Monitoring must answer three questions at all times:

1. **Is the bot alive and healthy?** (Infrastructure)
2. **Is the bot behaving safely?** (Risk & Circuit Breakers)
3. **Is the bot making money?** (Trading Performance)

Any gap between those three = blind spot = potential catastrophic loss.

---

## Current State Assessment

### ✅ What's Already Instrumented
| Metric | Location |
|--------|----------|
| `karsa_active_risk_profile` | `crypto_metrics.py` |
| `karsa_signal_rejections_total` | `crypto_metrics.py` |
| `karsa_signals_executed_total` | `crypto_metrics.py` |
| `karsa_signal_confidence` histogram | `crypto_metrics.py` |
| `karsa_universe_size` | `crypto_metrics.py` |
| `karsa_universe_coin_score` | `crypto_metrics.py` |

### ❌ Critical Gaps (Not Yet Instrumented)
- No PnL / drawdown metrics → **cannot monitor financial health**
- No kill switch / emergency stop state gauge → **silent failures**
- No SOR execution metrics → **blind on order fills**
- No position lifecycle metrics → **no visibility on open risk**
- No job scheduler health metrics → **scheduler failures silent**
- No Bybit API health / latency metrics → **connectivity blindness**
- No liquidation proximity metrics → **biggest risk unmonitored**
- No funding rate metrics → **carry cost invisible**
- No circuit breaker state metrics → **breakers fire silently**

---

## Domain 1 — Trading Performance (P&L Layer)
> _"Are we making money? Is drawdown under control?"_

### Metrics to Add

```python
# karsa_pnl_daily_usd — Gauge
# Today's realized PnL in USD (updated per closed trade)
DAILY_PNL_USD = Gauge("karsa_pnl_daily_usd", "Today realized PnL USD")

# karsa_pnl_unrealized_usd — Gauge  
# Current open position unrealized PnL
UNREALIZED_PNL_USD = Gauge("karsa_pnl_unrealized_usd", "Unrealized PnL USD across all open positions")

# karsa_portfolio_equity_usd — Gauge
# Total wallet equity (tracks account growth)
PORTFOLIO_EQUITY_USD = Gauge("karsa_portfolio_equity_usd", "Total portfolio equity in USDT")

# karsa_drawdown_pct — Gauge
# Current drawdown from peak equity (0-100)
CURRENT_DRAWDOWN_PCT = Gauge("karsa_drawdown_pct", "Current drawdown % from peak equity")

# karsa_win_rate — Gauge
# Rolling 7-day win rate (closed trades)
WIN_RATE_7D = Gauge("karsa_win_rate_7d", "Rolling 7-day win rate (0-1)")

# karsa_trade_pnl_usd — Histogram
# Per-trade realized PnL distribution
TRADE_PNL_USD = Histogram(
    "karsa_trade_pnl_usd",
    "Per-trade realized PnL in USD",
    ["ticker", "direction"],
    buckets=[-500, -200, -100, -50, -20, 0, 20, 50, 100, 200, 500]
)

# karsa_avg_rr_ratio — Gauge
# Rolling average realized R:R ratio (reward/risk)
AVG_RR_RATIO_7D = Gauge("karsa_avg_rr_ratio_7d", "7-day average realized R:R ratio")
```

### PromQL Queries for Dashboard

```promql
# Daily PnL vs Limit (burn rate bar)
karsa_pnl_daily_usd

# Drawdown gauge (RED if > 2%, CRITICAL if >= 3%)
karsa_drawdown_pct

# Equity curve (time-series, most important chart)
karsa_portfolio_equity_usd

# Win rate trend (should stay > 50%)
karsa_win_rate_7d * 100
```

---

## Domain 2 — Risk Safety (Kill Switch & Circuit Breakers)
> _"Is the safety net actually working? Are we one bad trade from blow-up?"_

### Metrics to Add

```python
# karsa_kill_switch_active — Gauge (0/1)
# CRITICAL: Must alert immediately if this goes 1
KILL_SWITCH_ACTIVE = Gauge("karsa_kill_switch_active", 
    "1 if emergency kill switch is active, 0 otherwise")

# karsa_circuit_breaker_active — Gauge
# Per-breaker state: DAILY_DD, VOLATILITY:BTC, CORRELATION
CIRCUIT_BREAKER_ACTIVE = Gauge("karsa_circuit_breaker_active",
    "1 if circuit breaker is active",
    ["breaker_type"])

# karsa_volatility_spike_pct — Gauge
# Current max 15-min move % per ticker (feed circuit breaker check)
VOLATILITY_SPIKE_PCT = Gauge("karsa_volatility_spike_pct",
    "Max price move % in last 15 min",
    ["ticker"])

# karsa_daily_loss_pct — Gauge
# Current daily loss as % of portfolio (0-100, triggers kill at 3%)
DAILY_LOSS_PCT = Gauge("karsa_daily_loss_pct",
    "Current day unrealized+realized loss as % of equity")

# karsa_correlation_loss_ratio — Gauge
# Losing positions ratio within each correlation tier
CORRELATION_LOSS_RATIO = Gauge("karsa_correlation_loss_ratio",
    "Fraction of positions losing within correlation tier",
    ["tier"])
```

### Alerting Rules (Prometheus `rules.yml`)

```yaml
groups:
  - name: karsa_critical
    rules:
      - alert: KillSwitchActive
        expr: karsa_kill_switch_active == 1
        for: 0m
        severity: critical
        annotations:
          summary: "TRADING HALTED — Kill switch active"

      - alert: DrawdownApproachingLimit
        expr: karsa_drawdown_pct > 2.0
        for: 1m
        severity: warning
        annotations:
          summary: "Drawdown {{ $value }}% approaching 3% kill limit"

      - alert: DrawdownCritical
        expr: karsa_drawdown_pct >= 2.8
        for: 0m
        severity: critical
        annotations:
          summary: "CRITICAL: Drawdown {{ $value }}% — kill switch imminent"

      - alert: CircuitBreakerFired
        expr: karsa_circuit_breaker_active == 1
        for: 0m
        severity: warning
        annotations:
          summary: "Circuit breaker {{ $labels.breaker_type }} triggered"

      - alert: VolatilitySpike
        expr: karsa_volatility_spike_pct > 4.0
        for: 0m
        severity: warning
        annotations:
          summary: "{{ $labels.ticker }} volatility spike {{ $value }}%"
```

---

## Domain 3 — Position & Liquidation Health
> _"Are open positions safe? How close are we to liquidation?"_

### Metrics to Add

```python
# karsa_open_positions_count — Gauge
OPEN_POSITIONS = Gauge("karsa_open_positions_count",
    "Number of currently open positions")

# karsa_position_unrealized_pnl_usd — Gauge (per ticker)
POSITION_PNL = Gauge("karsa_position_unrealized_pnl_usd",
    "Unrealized PnL per open position",
    ["ticker", "side"])

# karsa_liquidation_distance_pct — Gauge (per ticker)
# CRITICAL METRIC: distance from current price to liquidation price
LIQ_DISTANCE_PCT = Gauge("karsa_liquidation_distance_pct",
    "Distance to liquidation as % of entry price",
    ["ticker", "side", "level"])  # level: safe/warning/danger/force_close

# karsa_position_age_hours — Gauge (per ticker)
# Alert on positions held > 72h (time-exit trigger)
POSITION_AGE_HOURS = Gauge("karsa_position_age_hours",
    "Age of open position in hours",
    ["ticker"])

# karsa_funding_cost_usd — Gauge (per ticker, per 8h interval)
FUNDING_COST = Gauge("karsa_funding_cost_8h_usd",
    "Funding cost per 8h interval per position",
    ["ticker"])

# karsa_funding_rate_pct — Gauge (universe-wide)
FUNDING_RATE = Gauge("karsa_funding_rate_pct",
    "Current funding rate %",
    ["ticker"])
```

### Alerting Rules

```yaml
      - alert: LiquidationDanger
        expr: karsa_liquidation_distance_pct{level="danger"} < 10
        for: 0m
        severity: critical
        annotations:
          summary: "{{ $labels.ticker }} {{ $labels.side }} — {{ $value }}% from liquidation!"

      - alert: PositionAgedOut
        expr: karsa_position_age_hours > 72
        for: 0m
        severity: warning
        annotations:
          summary: "{{ $labels.ticker }} position held {{ $value }}h (time-exit trigger)"

      - alert: ExtremeFundingRate
        expr: abs(karsa_funding_rate_pct) > 0.1
        for: 0m
        severity: warning
        annotations:
          summary: "{{ $labels.ticker }} funding rate {{ $value }}% (crowded trade risk)"
```

---

## Domain 4 — Order Execution Quality (SOR)
> _"Are orders filling correctly? Are we getting good prices?"_

### Metrics to Add

```python
# karsa_order_fill_total — Counter
ORDER_FILL = Counter("karsa_order_fill_total",
    "Total orders filled",
    ["ticker", "order_type", "direction"])  # order_type: limit/market

# karsa_order_slippage_bps — Histogram
# Basis points of slippage vs mid price
ORDER_SLIPPAGE_BPS = Histogram(
    "karsa_order_slippage_bps",
    "Order slippage in basis points",
    ["ticker", "direction"],
    buckets=[0, 1, 2, 5, 10, 20, 50, 100]
)

# karsa_order_fill_latency_seconds — Histogram
# Time from signal generation to order fill
FILL_LATENCY = Histogram(
    "karsa_order_fill_latency_seconds",
    "Order fill latency (signal → fill)",
    buckets=[1, 5, 15, 30, 60, 120, 300]
)

# karsa_limit_order_fallback_total — Counter
# How often limit → market fallback triggers (bad = market orders dominating)
LIMIT_FALLBACK = Counter("karsa_limit_order_fallback_total",
    "Limit order fell back to market order",
    ["ticker", "reason"])  # reason: timeout/repriced/failed

# karsa_order_rejected_exchange_total — Counter
# Exchange-level rejections (insufficient margin, invalid price, etc.)
ORDER_REJECTED_EXCHANGE = Counter("karsa_order_rejected_exchange_total",
    "Orders rejected by Bybit exchange",
    ["ticker", "error_code"])
```

### PromQL Key Queries

```promql
# Market order ratio (should stay < 20% — maker rebates matter)
rate(karsa_limit_order_fallback_total[1h]) /
  rate(karsa_order_fill_total[1h]) * 100

# P95 fill latency (should stay < 60s)
histogram_quantile(0.95, rate(karsa_order_fill_latency_seconds_bucket[1h]))

# Median slippage in bps
histogram_quantile(0.50, rate(karsa_order_slippage_bps_bucket[1h]))
```

---

## Domain 5 — Infrastructure & Scheduler Health
> _"Is the bot actually running? Are jobs firing on schedule?"_

### Metrics to Add

```python
# karsa_job_last_run_timestamp — Gauge (per job)
# Stale value = job isn't firing
JOB_LAST_RUN = Gauge("karsa_job_last_run_timestamp_seconds",
    "Unix timestamp of last successful job run",
    ["job_id"])

# karsa_job_duration_seconds — Histogram (per job)
JOB_DURATION = Histogram(
    "karsa_job_duration_seconds",
    "Scheduled job execution duration",
    ["job_id"],
    buckets=[0.1, 0.5, 1, 5, 10, 30, 60, 120, 300]
)

# karsa_job_error_total — Counter (per job)
JOB_ERRORS = Counter("karsa_job_errors_total",
    "Scheduled job failures",
    ["job_id"])

# karsa_bybit_api_latency_seconds — Histogram
BYBIT_LATENCY = Histogram(
    "karsa_bybit_api_latency_seconds",
    "Bybit REST API call latency",
    ["endpoint"],
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10]
)

# karsa_bybit_api_errors_total — Counter
BYBIT_ERRORS = Counter("karsa_bybit_api_errors_total",
    "Bybit API call errors",
    ["endpoint", "error_type"])  # error_type: timeout/rate_limit/auth/other

# karsa_redis_connected — Gauge (0/1)
REDIS_CONNECTED = Gauge("karsa_redis_connected",
    "1 if Redis is reachable, 0 otherwise")

# karsa_warp_connected — Gauge (0/1)  
# WARP proxy health — if this fails, ALL Bybit traffic dies
WARP_CONNECTED = Gauge("karsa_warp_connected",
    "1 if Cloudflare WARP proxy is reachable, 0 otherwise")
```

### Critical Alerting Rules

```yaml
      - alert: CryptoScanNotRunning
        expr: time() - karsa_job_last_run_timestamp_seconds{job_id="scan_crypto"} > 7200
        for: 5m
        severity: critical
        annotations:
          summary: "Crypto scan has not run for 2+ hours — bot may be dead"

      - alert: PositionSyncStale
        expr: time() - karsa_job_last_run_timestamp_seconds{job_id="crypto_position_sync"} > 600
        for: 2m
        severity: warning
        annotations:
          summary: "Position sync stale — position data unreliable"

      - alert: WARPProxyDown
        expr: karsa_warp_connected == 0
        for: 1m
        severity: critical
        annotations:
          summary: "WARP proxy down — ALL Bybit trading is BLOCKED"

      - alert: BybitAPIHighLatency
        expr: histogram_quantile(0.95, rate(karsa_bybit_api_latency_seconds_bucket[5m])) > 5
        for: 5m
        severity: warning
        annotations:
          summary: "Bybit API P95 latency {{ $value }}s — execution risk"

      - alert: BybitAPIErrors
        expr: rate(karsa_bybit_api_errors_total[5m]) > 0.1
        for: 2m
        severity: warning
        annotations:
          summary: "Bybit API error rate {{ $value }}/s"
```

---

## Domain 6 — Regime & Intelligence Layer
> _"Is the bot adapting to market conditions?"_

### Metrics to Add

```python
# karsa_crypto_regime — Gauge (encoded)
# 0=CHOP, 1=MEAN_REVERSION, 2=TREND_BEAR, 3=TREND_BULL
CRYPTO_REGIME = Gauge("karsa_crypto_regime",
    "Current crypto market regime (0=CHOP 1=MR 2=TREND_BEAR 3=TREND_BULL)")

# karsa_regime_size_multiplier — Gauge
# Current size multiplier from regime (0.5-1.2x)
REGIME_SIZE_MULT = Gauge("karsa_regime_size_multiplier",
    "Position size multiplier from current regime (0.5-1.2)")

# karsa_btc_dominance_pct — Gauge
DOMINANCE = Gauge("karsa_btc_dominance_pct",
    "BTC market dominance % (>55=BTC season, <45=alt season)")

# karsa_signal_llm_latency_seconds — Histogram
# LLM call latency (Claude API) — budget/rate risk
LLM_LATENCY = Histogram(
    "karsa_signal_llm_latency_seconds",
    "Claude API call duration for signal generation",
    ["agent"],
    buckets=[1, 2, 5, 10, 20, 30, 60]
)
```

---

## Grafana Dashboard Layout

![Grafana Dashboard Mockup](/Users/dwiki.nugraha/.gemini/antigravity-ide/brain/9af902b8-0120-4f9f-9bfb-a0f7623d68f6/karsa_grafana_dashboard_1782986631169.png)

### Row 1 — Status Bar (Always Visible)
| Panel | Type | Metric | Threshold |
|-------|------|--------|-----------|
| Kill Switch | Stat | `karsa_kill_switch_active` | 0=GREEN, 1=RED |
| Active Breakers | Stat | `sum(karsa_circuit_breaker_active)` | 0=GREEN, >0=RED |
| WARP Proxy | Stat | `karsa_warp_connected` | 1=GREEN, 0=RED |
| Open Positions | Stat | `karsa_open_positions_count` | 0-5 range |
| Market Regime | Stat | `karsa_crypto_regime` | mapped labels |
| Risk Profile | Stat | `karsa_active_risk_profile` | 0-2 mapped |

### Row 2 — Financial Health
| Panel | Type | Metric |
|-------|------|--------|
| Equity Curve (7d) | Time-series | `karsa_portfolio_equity_usd` |
| Daily PnL | Bar gauge | `karsa_pnl_daily_usd` |
| Drawdown % | Gauge | `karsa_drawdown_pct` |
| Win Rate 7d | Stat | `karsa_win_rate_7d * 100` |

### Row 3 — Risk & Safety
| Panel | Type | Metric |
|-------|------|--------|
| Daily Loss % vs Limit | Gauge | `karsa_daily_loss_pct` — threshold at 3% |
| Liquidation Distance | Table | `karsa_liquidation_distance_pct` by ticker |
| Funding Rates | Heatmap | `karsa_funding_rate_pct` by ticker |
| Correlation Exposure | Bar | `karsa_correlation_loss_ratio` by tier |

### Row 4 — Execution Quality
| Panel | Type | Metric |
|-------|------|--------|
| Signal Flow | Time-series | Executed vs Rejected rate |
| Market Order % | Stat | limit fallback rate |
| Fill Latency P95 | Stat | `histogram_quantile(0.95, ...)` |
| Bybit API Latency | Time-series | P50/P95 by endpoint |

### Row 5 — Scheduler Health
| Panel | Type | Metric |
|-------|------|--------|
| Job Freshness | Table | Last run timestamp per job, staleness calculated |
| Job Error Rate | Time-series | `rate(karsa_job_errors_total[5m])` by job |
| LLM Latency | Time-series | P50/P95 by agent |
| Universe Refresh | Stat | Last refresh + duration |

> [!TIP]
> **Dashboard JSON Ready**: The full Grafana Dashboard JSON for this layout has been exported to [monitoring/grafana-dashboard.json](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/monitoring/grafana-dashboard.json). Since this file is mounted directly into the Grafana container via `docker-compose.yml`, restarting the Grafana container will load this new V2 dashboard automatically.

---

## Implementation Priority

### 🔴 Priority 1 — Do This Week (Safety Critical)
1. `karsa_kill_switch_active` — instrument in `emergency.py`
2. `karsa_drawdown_pct` + `karsa_portfolio_equity_usd` — instrument in `_job_kill_switch`
3. `karsa_liquidation_distance_pct` — instrument in `check_all_positions_health()`
4. `karsa_job_last_run_timestamp_seconds` — wrap all `_job_*` methods
5. `karsa_warp_connected` — probe from health check

### 🟡 Priority 2 — Do This Sprint (Visibility)
6. `karsa_order_fill_total` + slippage + latency in SOR
7. `karsa_open_positions_count` + per-position PnL
8. `karsa_circuit_breaker_active` in `circuit_breaker.py`
9. `karsa_bybit_api_latency_seconds` in `bybit_client.py`
10. `karsa_funding_rate_pct` in `funding_tracker.py`

### 🟢 Priority 3 — Next Sprint (Intelligence)
11. `karsa_crypto_regime` from `crypto_regime.py`
12. `karsa_win_rate_7d` from `crypto_audit.py`
13. `karsa_signal_llm_latency_seconds` in `base.py`
14. `karsa_avg_rr_ratio_7d` from closed trades

---

## Implementation Pattern

Add this wrapper to every job in `main_crypto.py`:

```python
from src.metrics.crypto_metrics import JOB_LAST_RUN, JOB_DURATION, JOB_ERRORS
import time

async def _job_scan_crypto(self):
    start = time.time()
    try:
        signals = await self.orchestrator.scan_all_markets("CRYPTO")
        JOB_LAST_RUN.labels(job_id="scan_crypto").set(time.time())
        JOB_DURATION.labels(job_id="scan_crypto").observe(time.time() - start)
        logger.info("crypto_scan_done", signals=len(signals))
    except Exception as e:
        JOB_ERRORS.labels(job_id="scan_crypto").inc()
        logger.error("crypto_scan_failed", error=str(e))
```

Add kill switch gauge to `emergency.py`:

```python
from prometheus_client import Gauge

KILL_SWITCH_STATE = Gauge("karsa_kill_switch_active", 
    "1 if emergency kill switch is active")

async def activate(reason: str, operator: str = "system") -> None:
    KILL_SWITCH_STATE.set(1)
    # ... existing code
    
async def deactivate(operator: str = "system") -> None:
    KILL_SWITCH_STATE.set(0)
    # ... existing code
```

---

## Prometheus Config Update

Add to `monitoring/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "rules/trading_alerts.yml"

alerting:
  alertmanagers:
    - static_configs:
        - targets: ['alertmanager:9093']

scrape_configs:
  - job_name: 'karsa-orchestrator'
    static_configs:
      - targets: ['karsa-orchestrator:8000']

  - job_name: 'karsa-crypto-orchestrator'
    static_configs:
      - targets: ['karsa-crypto-orchestrator:8001']

  # Infrastructure exporters
  - job_name: 'redis'
    static_configs:
      - targets: ['redis-exporter:9121']

  - job_name: 'postgres'
    static_configs:
      - targets: ['postgres-exporter:9187']

  - job_name: 'node'
    static_configs:
      - targets: ['node-exporter:9100']
```

---

> [!IMPORTANT]
> **Must-have alerting channel**: Wire Prometheus Alertmanager → Telegram bot (`karsa-crypto-bot`).
> The kill switch alert must reach the operator's phone within 30 seconds of activation.
> Configure `alertmanager.yml` with Telegram webhook pointing at the bot's `/alert` endpoint.

> [!CAUTION]
> **Grafana admin password** is currently `admin` in `docker-compose.yml`.
> Change `GF_SECURITY_ADMIN_PASSWORD` before production. The dashboard is on `127.0.0.1:3000` only — ensure no NAT traversal exposes it.
