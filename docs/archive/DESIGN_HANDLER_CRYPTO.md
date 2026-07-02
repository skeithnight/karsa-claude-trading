# Karsa Crypto Trading System - Simplified Telegram Bot Design

**Project:** Karsa Trading System (Autonomous Crypto Desk)  
**Goal:** Redesign the Telegram handlers to be **much simpler**, focused on **monitoring** rather than complex interaction.  
**Rationale:** The system is autonomous (runs 24/7 with its own decision engine). The Telegram bot should primarily serve as a **dashboard + alert viewer**, not a full control panel.

---

## 1. Design Principles

- **Minimalism:** Reduce from ~30+ commands to **8-10 core commands**.
- **Monitoring First:** Prioritize real-time status, activity, and health.
- **One-Click Navigation:** Heavy use of inline keyboards with consistent layout.
- **Progressive Disclosure:** Start with high-level summaries → drill down only when needed.
- **Autonomy Respect:** Most actions are "read-only". Dangerous commands (`/kill`, `/sellall`) are hidden or confirmation-protected.
- **Consistency:** Uniform formatting (emoji + bold + pre blocks), timestamps, and keyboard layouts.
- **Error Resilience:** Graceful fallbacks when services are down.

---

## 2. Core Command Set (Simplified)

| Command          | Alias/Callback     | Purpose                          | Frequency | Complexity |
|------------------|--------------------|----------------------------------|---------|----------|
| `/start`         | `cmd_start`        | Welcome + main navigation        | High    | Low      |
| `/status`        | `cmd_status`       | System health + capital + regime | High    | Low      |
| `/portfolio`     | `cmd_portfolio`    | Live positions + uPnL            | High    | Medium   |
| `/activity`      | `cmd_activity`     | Recent signals + closed trades   | High    | Medium   |
| `/briefing`      | `cmd_briefing`     | Market overview + regime summary | Daily   | Low      |
| `/risk`          | `cmd_risk`         | Risk limits + margin usage       | Medium  | Low      |
| `/pnl`           | `cmd_pnl`          | Performance summary              | Medium  | Low      |
| `/scan`          | `cmd_scan`         | Manual trigger full/single scan  | Low     | Medium   |
| `/kill`          | `cmd_kill`         | Emergency stop (protected)       | Rare    | High     |
| `/help` / `/guide` | `cmd_guide`     | Quick reference                  | Low     | Low      |

**Removed / Consolidated:**
- `research`, `whytrade`, `compare`, `backtest`, `replay`, `stats`, `equity`, `calibration`, `regimestats`, `funding`, `market`, `position`, `trailing`, `circuitbreakers`, `halt`, `reconcile`, `drift`, `liquidity`, `audit_agent`, etc. → Accessible via deeper navigation from `/activity` or `/status` if truly needed.

---

## 3. Navigation Structure (Inline Keyboards)

### Main Menu (`/start`)
```text
🖥️ KARSA CRYPTO DESK — ONLINE

[ 🌐 Briefing ] [ 📊 Status ]
[ 💼 Portfolio ] [ 📋 Activity ]
[ 🛡️ Risk ] [ 📈 P&L ]
[ 🔍 Scan ] [ ⚠️ Emergency ]
```

### Consistent Bottom Navigation
Every screen should include 2-3 rows:
- Row 1: Core monitoring (Status, Portfolio, Activity)
- Row 2: Context-aware (e.g. Briefing, Risk, Scan)
- Optional: Back / Refresh

---

## 4. Key Screen Designs

### `/status`
- System vitals (DB, Redis, Bybit, API Key)
- Regime banner (Hurst, ADX, Recommendation)
- Wallet summary (Balance, Available, Margin, uPnL)
- Halt / Cooldown status
- Last heartbeat timestamp

### `/portfolio`
- Table: Symbol | Side | Size | Entry | Mark | uPnL
- Total Unrealized P&L
- Quick buttons: Refresh, Risk, Activity

### `/activity`
- **Signals** (last 10): Ticker, Direction, Confidence, Status, Short Reasoning
- **Closed Trades** (last 10): Ticker, Side, PnL%, Reason, Time
- If pending signals → prominent **"Execute All Pending"** button
- Pagination or "Show More" if needed

### `/briefing`
- Current Regime
- Top movers (gainers/losers)
- Funding rate alerts
- Market sentiment summary

### `/risk`
- Config limits (Max Risk/Trade, Max Positions, Daily Loss Cap)
- Current usage (Margin bar)
- Open positions count vs limit

---

## 5. Technical Redesign Recommendations

### File Structure Suggestion
```
src/telegram/
├── crypto_handlers.py          # ← Main simplified file
├── handlers/
│   ├── monitoring.py           # status, portfolio, activity, briefing
│   ├── control.py              # scan, kill, resume
│   └── utils.py                # shared helpers
├── keyboards.py                # Centralized keyboard builder
└── formatters.py               # Consistent card/table formatters
```

### Simplified Helper Functions
- `_reply()` stays (with timestamp)
- `_get_bybit()`, `_get_redis()`, `_is_authorized()` stay
- New: `build_main_keyboard()`, `format_status_card()`, `format_activity_log()`

### Button Callback Handler (Simplified)
```python
async def button_callback(update, context):
    data = update.callback_query.data
    if data == "cmd_status":
        await status_cmd(...)
    elif data in ["cmd_portfolio", "cmd_activity", "cmd_briefing", ...]:
        ...
    # Only 8-10 mappings instead of 30+
```

### Autonomous Behavior
- Bot should **proactively notify** on important events (new execution, big PnL swing, regime change, halt triggered) via `context.bot.send_message`.
- Reduce manual triggers — `/scan` is mainly for debugging.

---

## 6. Implementation Priority

**Phase 1 (Core Monitoring)**
1. `/start` + main keyboard
2. `/status`
3. `/portfolio`
4. `/activity`
5. `/briefing`

**Phase 2 (Safety & Control)**
- `/risk`, `/pnl`, `/scan`
- Protected `/kill` + `/resume`

**Phase 3 (Polish)**
- Consistent formatting
- Error handling + loading states
- Proactive notifications

---

## 7. Example Simplified `status_cmd`

```python
async def status_cmd(update, context):
    # ... fetch data ...
    text = fmt(
        bold("🖥️ KARSA STATUS"), "\n━━━━━━━━━━━━━━\n\n",
        bold("System:"), pre(sys_status), "\n",
        regime_banner(...), "\n",
        bold("Capital:"), pre(wallet_block)
    )
    keyboard = build_main_keyboard()
    await _reply(update, text, reply_markup=keyboard)
```

---

## 8. Next Steps

1. Extract core monitoring logic from current handlers.
2. Create new `crypto_handlers_simplified.py`.
3. Update `crypto_main.py` to register only simplified handlers.
4. Add proactive notification service.
5. Test with real orchestrator connection.

---

**This design makes the bot a clean monitoring dashboard while keeping the autonomous engine untouched.**

Would you like me to:
- Generate the full simplified `crypto_handlers.py` code?
- Create the `keyboards.py` and `formatters.py` modules?
- Or refine any section above?


I have redesigned the interface to shift the paradigm from a **Manual Trading Terminal** to an **Autonomous System Monitor**. 

Since the bot trades autonomously 24/7, the user interface should focus on **observability, trust, and emergency control** rather than manual execution. I have condensed the 30+ commands into **5 core commands** and introduced a "Live Feed" concept so you can watch the AI work in real-time.

The design has been saved to `karsa_redesign.md`. Here is the breakdown:

***

# Karsa Autonomous Trading Bot - UX Redesign

## 1. Design Philosophy
Shift from a **Manual Trading Terminal** to an **Autonomous System Monitor**.
Since the bot trades autonomously 24/7, the user interface should focus on **observability, trust, and emergency control** rather than manual execution.

### Core Principles:
- **Less is More**: Reduce 30+ commands to 5 core commands.
- **Drill-Down UI**: Use Telegram Inline Keyboards to navigate from high-level summaries to detailed data.
- **Chronological Feed**: Replace static tables with a live "Activity Feed" so the user can see what the AI is doing in real-time.
- **Fail-Safe Controls**: Make emergency stops highly visible and accessible.

---

## 2. Simplified Command Structure

| Command | Purpose | Replaces |
| :--- | :--- | :--- |
| `/dashboard` | High-level system health, regime, and capital summary. | `/status`, `/briefing`, `/regime` |
| `/activity` | Live chronological feed of AI scans, trades, and adjustments. | `/activity`, `/trades`, `/whytrade`, `/drift` |
| `/portfolio` | Open positions, unrealized PnL, and recent closed trades. | `/portfolio`, `/pnl`, `/position`, `/trailing` |
| `/performance` | Historical stats, equity curve, and AI self-audit. | `/stats`, `/equity`, `/calibration`, `/audit_agent` |
| `/control` | Emergency halt, pause/resume, and manual overrides. | `/kill`, `/sellall`, `/resume`, `/halt`, `/scan` |

---

## 3. Interface Breakdown

### 📊 `/dashboard` (The Command Center)
The main landing page. Gives a 5-second snapshot of the bot's state.

**Display:**
- **System Vitals**: 🟢 Bybit Connected | 🟢 DB Online | 🟢 Redis Online
- **Market Regime**: 🟢 TREND_BULL (Hurst: 0.62, ADX: 28)
- **Capital**: $10,000 Balance | $8,500 Available | 15% Margin Used
- **Active Risk**: 2 Positions | $150 Unrealized PnL (🟢)

**Inline Keyboard:**
[ 📋 Activity ] [ 💼 Portfolio ]
[ 📈 Performance ] [ 🎛️ Control ]

---

### 📋 `/activity` (The Live Feed)
Instead of static tables, show a chronological log of the bot's "thoughts" and actions. This builds trust in the autonomous system.

**Display:**
**Recent Activity (Last 24h)**
> `14:32` ✅ **EXECUTED** ETHUSDT LONG
> Entry: $3,500 | Size: 0.5 | Conf: 82%
> *Thesis: Breakout above resistance with high volume.*
>
> `14:00` 🔍 **SCANNED** BTCUSDT
> Direction: SHORT | Conf: 45%
> *Action: Rejected (Confidence < 50)*
>
> `13:15` 🔄 **MANAGED** SOLUSDT LONG
> *Trailing stop moved: $145.00 → $148.50*

**Inline Keyboard:**
[ 🔍 Run Full Scan ] [ 🧠 View AI Reasoning ]
[ 🏠 Dashboard ]

---

### 💼 `/portfolio` (Positions & PnL)
A clean view of current exposure and recent performance.

**Display:**
**Active Positions**
| Ticker | Side | Size | Entry | Mark | uPnL |
| :--- | :--- | :--- | :--- | :--- | :--- |
| ETHUSDT | 🟢 LONG | 0.5 | $3,500 | $3,550 | 🟢 +$25.00 |
| SOLUSDT | 🟢 LONG | 10.0 | $145.0 | $148.5 | 🟢 +$35.00 |
**Total uPnL: 🟢 +$60.00**

**Recent Closes (Last 5)**
> `12:00` BTCUSDT LONG | 🟢 +1.2% ($120) | *Take Profit Hit*
> `09:00` AVAXUSDT SHORT | 🔴 -0.5% ($-25) | *Stop Loss Hit*

**Inline Keyboard:**
[ 📊 Detailed PnL ] [ 🔄 Trailing Stops ]
[ 🏠 Dashboard ]

---

### 📈 `/performance` (Analytics & Audit)
High-level metrics to evaluate the AI's strategy over time.

**Display:**
**30-Day Performance**
- **Total PnL**: 🟢 +$1,250.00 (+12.5%)
- **Win Rate**: 65% (26 Wins / 14 Losses)
- **Profit Factor**: 1.85
- **Max Drawdown**: -4.2%

**AI Agent Audit**
> **Grade: B+**
> *Recommendation: The agent performs well in TREND_BULL regimes but struggles in CHOP. Consider increasing the confidence threshold to 70% when ADX < 20.*

**Inline Keyboard:**
[ 📉 Equity Curve ] [ 🌡️ Regime Stats ]
[ 🎯 Calibration ] [ 🏠 Dashboard ]

---

### 🎛️ `/control` (Safety & Overrides)
The "Break Glass" panel. Designed for quick, decisive action.

**Display:**
**System State**: 🟢 AUTO-TRADING ACTIVE
**Global Halt**: 🟢 INACTIVE

**Inline Keyboard:**
[ 🚨 EMERGENCY KILL (Close All) ]
[ ⏸️ Pause Trading ] [ ▶️ Resume Trading ]
[ 🔄 Manual Scan ] [ 🏠 Dashboard ]

*(Note: The "EMERGENCY KILL" button should require a confirmation callback to prevent accidental clicks).*

---

## 4. Implementation Notes for `crypto_handlers.py`

### 4.1 Consolidate Handlers
Map the new 5 commands to the existing logic:
- `dashboard_cmd`: Combines logic from `status_cmd` and `briefing_cmd`.
- `activity_cmd`: Formats the DB queries from `activity_cmd` and `trades_cmd` into a unified timeline.
- `portfolio_cmd`: Combines `portfolio_cmd` and `pnl_cmd`.
- `performance_cmd`: Combines `stats_cmd`, `equity_cmd`, and `audit_agent_cmd`.
- `control_cmd`: Handles the UI for `kill_cmd`, `sellall_cmd`, and `resume_cmd`.

### 4.2 Unified Navigation Keyboard
Instead of building custom keyboards for every command, create a standard `main_nav_keyboard` that is attached to the bottom of every message:
```python
MAIN_NAV = [
    [("📊 Dashboard", "cmd_dashboard"), ("📋 Activity", "cmd_activity")],
    [("💼 Portfolio", "cmd_portfolio"), ("📈 Performance", "cmd_performance")],
    [("🎛️ Control", "cmd_control")]
]
```

### 4.3 Trust-Building Features
- **Show the "Why"**: In the `/activity` feed, always include a 1-line summary of the AI's reasoning.
- **Regime Context**: Always show the current market regime in the `/dashboard` so the user understands *why* the bot might be idle (e.g., "Regime: CHOP - Bot is waiting for a trend").

