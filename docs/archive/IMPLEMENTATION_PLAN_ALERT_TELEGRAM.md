# Implementation Plan: Observability & Notification Redesign

**Source:** `docs/ALERT_TELEGRAM.md`
**Date:** 2026-07-10
**Status:** Ready to implement

---

## Problem Statement

Infrastructure alerts (DB pool resets, WARP timeouts, watchdog events, event loop lag) are sent to the same Telegram chat as trade actions. This causes alert fatigue and makes it hard to distinguish "the bot opened a trade" from "the event loop was starved for 2 seconds."

## Goal

- **Telegram** → Business actions only (trades, regime shifts, manual commands)
- **Grafana** → All infrastructure/system telemetry + critical alerting via separate channel

---

## Phase 1: NotificationRouter (Telegram Filtering)

**File:** `src/notifications/router.py` (NEW)

### What changes

Create a centralized `NotificationRouter` that all subsystems use instead of calling `telegram_app.bot.send_message()` directly.

### Design

```python
class NotificationCategory(StrEnum):
    ASM_TRADE = "ASM_TRADE"           # Open/Close/Reduce positions
    ASM_REGIME = "ASM_REGIME"         # Regime shifts
    MANUAL_COMMAND = "MANUAL_COMMAND"  # Command acknowledgments
    INFRASTRUCTURE = "INFRASTRUCTURE"  # Watchdog, DB pool, event loop
    RISK_ALERT = "RISK_ALERT"         # Drawdown, liquidation proximity
    SYSTEM_ERROR = "SYSTEM_ERROR"     # Exceptions, connection failures

# Only these go to Telegram
TELEGRAM_ALLOWED = {ASM_TRADE, ASM_REGIME, MANUAL_COMMAND}

# These go to Grafana (via structured logging + Loki, or Prometheus alerts)
GRAFANA_ONLY = {INFRASTRUCTURE, RISK_ALERT, SYSTEM_ERROR}
```

### Callers to update (12 locations)

| File | Line | Current Behavior | New Category |
|------|------|------------------|--------------|
| `src/bot/crypto_main.py:222` | Connection health alert | Sends to Telegram | `INFRASTRUCTURE` → Grafana only |
| `src/bot/crypto_main.py:234` | Connection recovery | Sends to Telegram | `INFRASTRUCTURE` → Grafana only |
| `src/bot/crypto_main.py:274` | Startup reconciliation | Sends to Telegram | `MANUAL_COMMAND` → Telegram OK |
| `src/bot/crypto_main.py:390` | Alertmanager webhook | Sends to Telegram | `INFRASTRUCTURE` → Grafana only |
| `src/architecture/events/subscribers.py:128` | Trade events | Sends to Telegram | `ASM_TRADE` / `ASM_REGIME` → Telegram OK |
| `src/monitoring/watchdog.py:771` | Watchdog alerts | Sends to Telegram | `INFRASTRUCTURE` → Grafana only |
| `src/monitoring/watchdog.py:783` | Watchdog fallback (httpx) | Sends to Telegram | `INFRASTRUCTURE` → Grafana only |
| `src/main_crypto.py:1197` | Position mismatch | Sends to Telegram | `INFRASTRUCTURE` → Grafana only |
| `src/main_crypto.py:1370` | Anomaly detection | Sends to Telegram | `RISK_ALERT` → Grafana only |
| `src/risk/risk_monitor.py:156` | Emergency risk | Sends to Telegram | `RISK_ALERT` → Keep Telegram (emergency) |

### Special cases

1. **Risk monitor emergency alerts** — "Portfolio about to be liquidated" level. Should stay on Telegram even though they're `RISK_ALERT`. Add `force=True` override to `NotificationRouter.send()`.

2. **DLQ exhaustion** (`src/risk/dlq.py`) — Currently only logs, never alerts. Add `SYSTEM_ERROR` notification when DLQ exhausted queue grows.

3. **Alerts toggle** (`karsa:alerts_enabled` in Redis) — Currently not checked by event subscriber. The router should respect this toggle for `ASM_TRADE` category only (infrastructure alerts should never be muted).

### Integration point

```python
# In crypto_main.py startup:
from src.notifications.router import NotificationRouter
self.notifier = NotificationRouter(telegram_app.bot, logger)

# In subscribers.py:
async def telegram_subscriber(event):
    category = _EVENT_CATEGORY_MAP.get(event.type, NotificationCategory.SYSTEM_ERROR)
    await notifier.send(format_event(event), category)
```

---

## Phase 2: Grafana Dashboard — "Karsa Quant"

**File:** `monitoring/grafana/dashboards/karsa-quant.json` (NEW)

### Row structure (5 rows, 18 panels)

#### Row 1: Executive Summary (5 panels)

| Panel | Metric | Type | Thresholds |
|-------|--------|------|------------|
| System Health Score | `karsa_watchdog_health_score` | Gauge | 🟢 >70, 🟡 40-70, 🔴 <40 |
| Total Wallet Equity | `karsa_wallet_total_equity_usd` | Stat | Color by 24h change |
| Available Margin | `karsa_wallet_available_balance_usd` | Stat | 🔴 < 10% of total |
| Unrealized PnL | `karsa_portfolio_unrealized_pnl_usd` | Stat | 🟢 >0, 🔴 <0 |
| Open Positions | `karsa_positions_open_count` | Stat | 🟢 1-5, 🔴 >10 |

#### Row 2: ASM Trading (4 panels)

| Panel | Metric | Type |
|-------|--------|------|
| Active Positions PnL | `karsa_position_unrealized_pnl{ticker="*"}` | Time Series |
| Market Regimes | `karsa_regime_state{ticker="*"}` | State Timeline |
| ASM Checkpoint Events | `karsa_asm_checkpoint_total` | Time Series |
| AI Confidence Score | `karsa_llm_confidence_score{ticker="*"}` | Time Series |

#### Row 3: Infrastructure & Watchdog (5 panels)

| Panel | Metric | Type | Notes |
|-------|--------|------|-------|
| Event Loop Lag | `karsa_watchdog_event_loop_lag_seconds` | Time Series | **Critical** — flat < 1.0s |
| DB Pool Status | `karsa_db_pool_checked_out`, `karsa_db_pool_overflow` | Time Series | Overflow never negative |
| Process Memory (RSS) | `karsa_watchdog_memory_mb` | Time Series | Watch for slow leaks |
| WARP Proxy Latency | `karsa_bybit_api_latency_seconds` | Time Series | Spikes = WARP struggling |
| Watchdog Level | `karsa_watchdog_current_level` | State Timeline | Flat at 0 |

#### Row 4: Execution Microstructure (4 panels)

| Panel | Metric | Type |
|-------|--------|------|
| Cumulative Slippage | `karsa_sor_slippage_bps_cumulative` | Time Series |
| Maker vs Taker Ratio | `karsa_orders_maker_total` / `karsa_orders_taker_total` | Pie Chart |
| LLM Token Usage | `karsa_llm_tokens_used_total` | Stat |
| Dead Letter Queue | `karsa_dlq_depth` | Stat |

#### Row 5: Alerts & Incidents (3 panels)

| Panel | Metric | Type |
|-------|--------|------|
| API Error Rate | `rate(karsa_bybit_api_errors_total[5m])` | Time Series |
| Watchdog Self-Heals | `increase(karsa_watchdog_recoveries_total[1h])` | Bar Chart |
| Ghost Position Syncs | `increase(karsa_reconciliation_ghosts_total[1h])` | Bar Chart |

### Missing metrics (need to wire or create)

| Metric | Status | Action |
|--------|--------|--------|
| `karsa_wallet_total_equity_usd` | ❌ Not defined | Add to `crypto_metrics.py`, wire in wallet fetch |
| `karsa_wallet_available_balance_usd` | ❌ Not defined | Add to `crypto_metrics.py`, wire in wallet fetch |
| `karsa_portfolio_unrealized_pnl_usd` | ❌ Not defined | Sum from position tracker |
| `karsa_positions_open_count` | ❌ Not defined | Count from DB/Redis |
| `karsa_regime_state` | ❌ Not defined | Wire from CryptoRegimeFilter |
| `karsa_asm_checkpoint_total` | ❌ Not defined | Counter in PerformanceGate |
| `karsa_llm_confidence_score` | ❌ Not defined | Capture from agent responses |
| `karsa_sor_slippage_bps_cumulative` | ❌ Not defined | Accumulate in SOR |
| `karsa_orders_maker_total` / `taker` | ❌ Not defined | Count in SOR |
| `karsa_dlq_depth` | ❌ Not defined | Gauge in DLQ |
| `karsa_bybit_api_errors_total` | ❌ Not defined | Counter in bybit_client |
| `karsa_reconciliation_ghosts_total` | ❌ Not defined | Counter in position_sync |
| `karsa_watchdog_current_level` | ✅ Exists | Already wired |
| `karsa_watchdog_health_score` | ✅ Exists | Already wired |
| `karsa_watchdog_event_loop_lag_seconds` | ✅ Exists | Already wired |
| `karsa_watchdog_memory_mb` | ✅ Exists | Already wired |
| `karsa_watchdog_recoveries_total` | ✅ Exists | Already wired |
| `karsa_db_pool_checked_out` / `overflow` | ✅ Exists | Already wired |
| `karsa_bybit_api_latency_seconds` | ✅ Exists | Already wired |
| `karsa_llm_tokens_used_total` | ✅ Exists | Already wired |

**12 new metrics needed**, 8 already exist.

---

## Phase 3: Grafana Alert Rules

**File:** `monitoring/grafana/provisioning/alerting/alert-rules.yml` (NEW)

### Critical alerts (page-worthy)

| Alert | Condition | Duration | Action |
|-------|-----------|----------|--------|
| Event Loop Starvation | `karsa_watchdog_event_loop_lag_seconds > 5.0` | 2 min | Page — WARP or CPU issue |
| Watchdog Hard Restart | `karsa_watchdog_current_level == 3` | 1 min | Page — bot was killed |
| Drawdown Breach | `karsa_daily_loss_pct > 5.0` | Immediate | Page — emergency flatten |

### Warning alerts (investigate)

| Alert | Condition | Duration | Action |
|-------|-----------|----------|--------|
| DB Pool Leak | `karsa_db_pool_overflow < 0` | 5 min | Warn — connection leak |
| Memory Trend | `karsa_watchdog_memory_mb > 1200` | 10 min | Warn — possible leak |
| DLQ Depth | `karsa_dlq_depth > 0` | 5 min | Warn — operations failing |

---

## Phase 4: Docker Compose Cleanup

**File:** `docker-compose.yml` (MODIFY)

- Set `LOG_LEVEL=INFO` for production (currently `DEBUG` adds noise)
- Ensure Loki driver is configured if using Grafana Cloud/Stack for logs

---

## Execution Order

```
Phase 1 (NotificationRouter)
  ├─ src/notifications/__init__.py (NEW)
  ├─ src/notifications/router.py (NEW)
  ├─ Update src/bot/crypto_main.py (6 call sites)
  ├─ Update src/monitoring/watchdog.py (2 call sites)
  ├─ Update src/main_crypto.py (2 call sites)
  ├─ Update src/risk/risk_monitor.py (1 call site)
  └─ Update src/architecture/events/subscribers.py (1 call site)

Phase 2 (Metrics)
  ├─ Add 12 missing metrics to src/metrics/crypto_metrics.py
  └─ Wire metrics in: wallet fetch, position tracker, regime filter, SOR, DLQ

Phase 3 (Grafana Dashboard)
  ├─ monitoring/grafana/dashboards/karsa-quant.json (NEW)
  └─ monitoring/grafana/provisioning/alerting/alert-rules.yml (NEW)

Phase 4 (Docker)
  └─ Update docker-compose.yml LOG_LEVEL
```

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Breaking trade notifications | HIGH — user misses trade open/close | Keep `ASM_TRADE` on Telegram, test with paper trades |
| Losing visibility into infra issues | MEDIUM — silent failures | Grafana alerts MUST be configured before removing Telegram infra alerts |
| Missing metrics for dashboard | LOW — empty panels | Phase 2 wires metrics before Phase 3 builds dashboard |
| Force-kill during refactor | LOW — partial state | NotificationRouter falls back to direct send if router unavailable |

---

## Verification

1. **Phase 1:** Send test notifications for each category; confirm only ASM/manual reach Telegram
2. **Phase 2:** `curl http://localhost:8001/metrics | grep karsa_wallet` — new metrics appear
3. **Phase 3:** Open Grafana → Karsa Quant dashboard → all 18 panels render
4. **Phase 4:** `docker logs karsa-crypto-orchestrator | grep DEBUG` — no DEBUG lines
