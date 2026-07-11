# 📊 Karsa Trading Engine: Observability & Notification Architecture Redesign

**Date:** October 2023  
**Scope:** Transitioning from chat-based monitoring (Telegram) to dashboard-based observability (Grafana).  
**Objective:** Eliminate alert fatigue, separate business logic from system telemetry, and achieve institutional-grade monitoring.

---

## 📑 Executive Summary

As the Karsa bot has evolved into a complex, self-healing distributed system, monitoring it via a Telegram chat has become unsustainable. Infrastructure alerts (DB pool resets, WARP timeouts, event loop lag) clutter the chat, making it difficult to track actual trading performance.

This document outlines the architectural shift to **Dashboard-Based Observability**. Telegram will be strictly reserved for **Business Actions** (ASM trades), while **Grafana** will handle **System Telemetry, Risk Monitoring, and Infrastructure Alerting**.

---

## 📬 Part 1: The Notification Routing Strategy

We must enforce a strict separation of concerns between the bot's business logic and its infrastructure.

### 🟢 Telegram (Strictly Business / ASM Only)

Turn off all infrastructure, watchdog, and error alerts in Telegram. The bot should only message you when it takes a financial action or responds to a manual command.

**Allowed Categories:**

1. **Trade Opened:** `[ASM] 🟢 OPENED LONG 0.5 BTC @ $65,000 | Regime: TREND_BULL | SL: $63,500`
2. **Trade Closed:** `[ASM] 🔴 CLOSED LONG 0.5 BTC @ $66,200 | PnL: +$600 (+1.8%) | Reason: Trailing Stop`
3. **Regime Shift:** `[ASM] 🔄 REGIME CHANGE: BTCUSDT shifted from CHOP to TREND_BULL`
4. **Manual Commands:** Acknowledgments when you send `/start`, `/status`, or `/flatten`.

### 🔴 Grafana Alerting (Infrastructure & Risk)

Configure Grafana Alertmanager to send critical system alerts to a **separate, high-priority channel** (e.g., a dedicated "Karsa Critical" Telegram group, Discord, or PagerDuty).

**Alert Categories:**

1. **Watchdog Level 2/3:** `🚨 WATCHDOG: Hard restart triggered. Score dropped to 23.`
2. **Event Loop Starvation:** `⚠️ EVENT LOOP: Lag > 5s for 2 minutes. WARP proxy may be failing.`
3. **DB Pool Leak:** `🩸 DB LEAK: Pool overflow is negative. Self-heal failed.`
4. **Drawdown Limit:** `🛑 RISK: Portfolio drawdown exceeded 5%. Emergency flatten triggered.`

---

## 📊 Part 2: The "Karsa Quant" Grafana Dashboard Design

Create a new dashboard in Grafana named **"Karsa Trading Engine"**. Organize it into 5 logical rows to provide a complete view of the system.

### 📈 Row 1: Executive Summary

*This row gives you a 1-second glance at the bot's financial and operational state.*

| Panel Name | Visualization | Prometheus Query / Metric | Thresholds / Colors |
| :--- | :--- | :--- | :--- |
| **System Health Score** | Gauge | `karsa_watchdog_health_score` | 🟢 >70, 🟡 40-70, 🔴 <40 |
| **Total Wallet Equity** | Stat | `karsa_wallet_total_equity_usd` | Color by % change over 24h |
| **Available Margin** | Stat | `karsa_wallet_available_balance_usd` | 🟢 Normal, 🔴 < 10% of Total |
| **Unrealized PnL** | Stat | `karsa_portfolio_unrealized_pnl_usd` | 🟢 >0, 🔴 <0 |
| **Open Positions** | Stat | `karsa_positions_open_count` | 🟢 1-5, 🔴 >10 (Overexposed) |

### 🤖 Row 2: Autonomous Session Manager (ASM)

*This row tracks the actual trading logic, AI decisions, and market regimes.*

| Panel Name | Visualization | Prometheus Query / Metric | Notes |
| :--- | :--- | :--- | :--- |
| **Active Positions PnL** | Time Series | `karsa_position_unrealized_pnl{ticker="*"}` | One line per open coin. |
| **Market Regimes** | State Timeline | `karsa_regime_state{ticker="*"}` | Shows when coins flip from CHOP to TREND. |
| **ASM Checkpoint Events** | Logs / Time Series | `karsa_asm_checkpoint_total` | Tracks when the AI Judge evaluates a position. |
| **AI Confidence Score** | Time Series | `karsa_llm_confidence_score{ticker="*"}` | Helps debug if the AI is hesitant or hallucinating. |

### ⚙️ Row 3: Infrastructure & Watchdog

*This is where you monitor the self-healing systems. If the bot crashes, you look here first.*

| Panel Name | Visualization | Prometheus Query / Metric | Notes |
| :--- | :--- | :--- | :--- |
| **Event Loop Lag** | Time Series | `karsa_watchdog_event_loop_lag_seconds` | **CRITICAL:** Should be flat < 1.0s. |
| **DB Pool Status** | Time Series | `karsa_db_pool_checked_out`, `karsa_db_pool_overflow` | Overflow should NEVER go negative. |
| **Process Memory (RSS)** | Time Series | `karsa_watchdog_memory_mb` | Watch for slow upward trends (memory leaks). |
| **WARP Proxy Latency** | Time Series | `karsa_bybit_api_latency_seconds` | Spikes indicate WARP is struggling. |
| **Watchdog Recovery Level** | State Timeline | `karsa_watchdog_current_level` | Should be flat at 0. Spikes mean self-healing. |

### 📉 Row 4: Execution Microstructure

*This row tracks the hidden costs of trading (fees, slippage, AI costs).*

| Panel Name | Visualization | Prometheus Query / Metric | Notes |
| :--- | :--- | :--- | :--- |
| **Cumulative Slippage** | Time Series | `karsa_sor_slippage_bps_cumulative` | Tracks execution quality over time. |
| **Maker vs Taker Ratio** | Pie Chart | `karsa_orders_maker_total` vs `karsa_orders_taker_total` | Goal: >80% Maker (Limit orders). |
| **LLM Token Usage** | Stat / Bar | `karsa_llm_tokens_used_total` | Tracks daily AI costs to prevent billing shocks. |
| **Dead Letter Queue** | Stat | `karsa_dlq_depth` | Should be 0. If >0, business logic is failing. |

### 🚨 Row 5: Alerts & Incidents

*A historical view of system hiccups and reconciliation events.*

| Panel Name | Visualization | Prometheus Query / Metric | Notes |
| :--- | :--- | :--- | :--- |
| **API Error Rate** | Time Series | `rate(karsa_bybit_api_errors_total[5m])` | Spikes mean Bybit is down or rate-limited. |
| **Watchdog Self-Heals** | Bar Chart | `increase(karsa_watchdog_recoveries_total[1h])` | Shows how often Level 1 triggers. |
| **Ghost Position Syncs** | Bar Chart | `increase(karsa_reconciliation_ghosts_total[1h])` | Shows how often DB and Bybit desync. |

---

## 🛠️ Part 3: Implementation Steps

### Step 1: Update Bot Code (Filter Telegram Notifications)

Refactor your notification manager to enforce the routing strategy. Create a `NotificationRouter` that filters messages by category.

```python
# src/notifications/router.py
import logging

logger = logging.getLogger(__name__)

class NotificationCategory:
    ASM_TRADE = "ASM_TRADE"
    ASM_REGIME = "ASM_REGIME"
    MANUAL_COMMAND = "MANUAL_COMMAND"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    RISK_ALERT = "RISK_ALERT"
    SYSTEM_ERROR = "SYSTEM_ERROR"

class NotificationRouter:
    def __init__(self, telegram_client, logger):
        self.telegram = telegram_client
        self.logger = logger

    async def send(self, message: str, category: str):
        # ONLY allow ASM and Manual categories to go to Telegram
        ALLOWED_TELEGRAM_CATEGORIES = {
            NotificationCategory.ASM_TRADE,
            NotificationCategory.ASM_REGIME,
            NotificationCategory.MANUAL_COMMAND
        }
        
        if category in ALLOWED_TELEGRAM_CATEGORIES:
            await self.telegram.send_message(message)
        else:
            # Log to standard logger, which Prometheus/Grafana will pick up via Loki or standard logs
            self.logger.info(f"[{category}] {message}")
```

### Step 2: Configure Grafana Alertmanager

Set up alerts for the critical infrastructure metrics so you don't lose visibility when things break.

**Alert 1: Event Loop Starvation (The WARP Killer)**

```yaml
# Grafana Alert Rule
expr: karsa_watchdog_event_loop_lag_seconds > 5.0
for: 2m
labels:
  severity: critical
annotations:
  summary: "Event loop starved > 5s for 2 minutes"
  description: "The asyncio event loop is blocked. Check WARP proxy latency or heavy CPU tasks."
```

**Alert 2: Watchdog Hard Restart**

```yaml
# Grafana Alert Rule
expr: karsa_watchdog_current_level == 3
for: 1m
labels:
  severity: critical
annotations:
  summary: "Watchdog triggered Level 3 Hard Restart"
  description: "The bot was unresponsive and had to be hard-killed by the Watchdog."
```

**Alert 3: DB Pool Leak**

```yaml
# Grafana Alert Rule
expr: karsa_db_pool_overflow < 0
for: 5m
labels:
  severity: warning
annotations:
  summary: "Database connection pool leak detected"
  description: "SQLAlchemy lost track of connections. Check for missing async context managers."
```

### Step 3: Clean up Docker Compose (Log Noise Reduction)

To keep your container logs clean for Grafana Loki (if used) or standard `docker logs`, increase the log level of the bot to `INFO` in production.

```yaml
# docker-compose.yml
services:
  karsa-crypto-bot:
    environment:
      - LOG_LEVEL=INFO  # Change from DEBUG to INFO
```

---

## 🎯 The Result

By implementing this architectural split:

1. **Zero Alert Fatigue:** Your phone stops buzzing with "DB pool reset" or "WARP timeout" messages. You only get notified when the bot actually opens or closes a trade.
2. **Instant Root-Cause Analysis:** Instead of scrolling through a chaotic Telegram chat to find out why the bot stopped trading, you open Grafana, look at the **Event Loop Lag** panel, and instantly see, *"Ah, WARP latency spiked at 14:00, starving the loop."*
3. **Institutional Operations:** You are now operating like a hedge fund. Professional quants do not manage infrastructure via chat apps; they manage it via time-series dashboards.

**Next Steps:**

1. Deploy the `NotificationRouter` code changes.
2. Build the 5 rows of the Grafana dashboard using the provided PromQL queries.
3. Configure the Grafana Alertmanager rules.
4. Enjoy a clean Telegram chat and a beautiful, real-time trading dashboard.
