# 🚀 ASM Telegram Bot: Improvement & Optimization Specification (v2.0)

This document outlines the actionable improvements, architectural adjustments, and feature additions required to upgrade the **Autonomous Session Manager (ASM) Telegram Bot**. The improvements are categorized by priority, focusing first on critical financial safety, followed by system reliability, operational efficiency, and user experience. 

*Note: This specification has been updated to align with the streamlined UX v2.1 design, removing duration selections and high-risk friction screens, while enhancing trade history and position management.*

---

## 🔴 Priority 0: Critical Risk & Financial Safety (Immediate)
*These changes address vulnerabilities that could lead to catastrophic financial loss or runaway bot behavior. They must be implemented before the next live trading session.*

### 1. Persistent Emergency Halts
**Current Issue:** The bot auto-clears emergency/halt keys on restart, ignoring previous max drawdown triggers.
**Improvement:** 
- Remove the auto-clear logic in `AutonomousSessionManager.start`.
- If a session halts due to Max Drawdown or Circuit Breaker, the Redis state must persist as `HALTED`.
- **Action:** Add a new Telegram command `/clear_halt` (restricted to authorized admins) that explicitly resets the halt state after the user has reviewed the account.

### 2. High-Frequency Risk Monitor (Decoupled from Scan Loop)
**Current Issue:** Max drawdown and liquidation risks are only checked every 15 minutes during the scan loop.
**Improvement:**
- Create a separate, lightweight background task (`risk_monitor_loop`) that runs every **5 to 10 seconds**.
- **Action:** This task should use WebSockets (preferred) or fast REST polling to monitor:
  - Global equity drop (Max Drawdown).
  - Proximity to liquidation prices for open positions.
  - Immediate execution of emergency market-close orders if thresholds are breached.

---

## 🟡 Priority 1: System Reliability & State Management (Short-Term)
*These changes ensure the bot can recover gracefully from crashes, network blips, or exchange downtime without creating "zombie" states.*

### 1. Zombie Session Reconciliation
**Current Issue:** If the bot crashes, it loses in-memory context of specific order IDs but Redis still thinks a session is active.
**Improvement:**
- Implement a `reconcile_state()` method triggered on startup if an active session is found in Redis.
- **Action:** 
  1. Query Bybit for all currently open positions and active orders.
  2. Compare against the PostgreSQL/Redis state.
  3. Sync the internal state. If "orphan" positions exist on the exchange, alert the user via Telegram and either adopt them into the session or close them.

### 2. Iteration Lock for the Trading Loop
**Current Issue:** Slow API responses can cause the 15-minute scan to take longer than the interval, leading to overlapping loops and duplicate orders.
**Improvement:**
- Implement an execution lock to prevent concurrent scans.
- **Action:** Use an `asyncio.Lock()` at the start of the `_run_loop` iteration. If the lock is already acquired, the next iteration should simply sleep and retry, rather than spawning a parallel scan.

### 3. Graceful Degradation in Dashboard
**Current Issue:** A failed database or Redis ping crashes the `/start` or `/dashboard` command.
**Improvement:**
- Wrap all external health checks in `try/except` blocks.
- **Action:** If Bybit is down, show *"⚠️ Exchange API Unreachable"*. If Postgres is down, show *"⚠️ DB Offline"*. The dashboard should always render the UI, highlighting which specific components are failing.

---

## 🟢 Priority 2: Operational Efficiency & Logic Tuning (Medium-Term)
*Optimizations to improve trade quality, reduce API spam, and adapt to market conditions dynamically.*

### 1. TTL-Based Signal Deduplication
**Current Issue:** Clearing the signal cache every 15 minutes causes the bot to re-enter the exact same breakout signal.
**Improvement:**
- Replace the "clear all" cache logic with a Time-To-Live (TTL) approach.
- **Action:** When a signal is processed (either executed or rejected), store its hash in Redis with a TTL of **4 hours**. This prevents re-entry into the same setup while allowing the bot to catch new breakouts.

### 2. Dynamic Regime Re-evaluation
**Current Issue:** A hard 1-hour sleep during a BEAR market might cause the bot to miss a sudden V-shaped reversal.
**Improvement:**
- Make the BEAR market sleep dynamic.
- **Action:** Instead of sleeping for 60 minutes, sleep for 10 minutes. Upon waking, re-evaluate the regime. If it's still BEAR, sleep again. If it shifts to BULL/NEUTRAL, immediately resume the standard 15-minute scan interval.

---

## 🔵 Priority 3: User Experience & Telegram UI (Enhancements)
*Improvements to make the bot more intuitive, informative, and proactive, strictly following the UX v2.1 design.*

### 1. Proactive Event-Driven Alerts (Wins & Losses)
**Current Issue:** The user has to manually check the dashboard to know if a trade closed.
**Improvement:**
- Implement push notifications for critical position events, covering both Take Profits and Stop Losses.
- **Action:** The bot should automatically send a formatted Telegram message when:
  - A position hits its **Take Profit** (Win alert with 🟢 PnL).
  - A position hits its **Stop Loss** (Loss alert with 🔴 PnL).
  - *Note:* These alerts must include a `[ 👀 View Dashboard ]` inline button for quick navigation.

### 2. Interactive Position Management & "Move SL to BE"
**Current Issue:** The ACTIVE dashboard only shows the *count* of open positions, and modifying stops requires external exchange UI.
**Improvement:**
- Add a **`📊 View Positions`** inline button to the ACTIVE dashboard.
- **Action:** 
  - Display a detailed, formatted card for each open position.
  - Include a **`🛡 Move SL to BE`** button for each position.
  - When clicked, the bot amends the order on Bybit, edits the specific position card in-place to show `SL: $X (BE) ✅`, and sends a quick "Toast" notification confirming the action with a `[ 🗑 Dismiss ]` button.

### 3. Trade History & Analytics
**Current Issue:** Once a session ends, the data is buried in the database.
**Improvement:**
- Add a **`📜 Trade History`** button to the IDLE dashboard.
- **Action:** Display a summary of recent closed trades and past sessions, including Entry/Exit prices, PnL (Wins/Losses), and overall session performance.

### 4. Seamless UI State Management (Edit-over-Send)
**Current Issue:** Clicking buttons like Refresh or Pause spams the chat with new messages.
**Improvement:**
- Implement an "Edit-over-Send" paradigm for all dashboard interactions.
- **Action:** 
  - **Refresh:** Fetches data and uses `edit_message_text` to update the dashboard in place.
  - **Pause/Resume:** Toggles the Redis state and edits the dashboard header (🟢 to 🟡) and buttons in place.
  - **Settings:** Toggles parameters (Max Pos, Regime Filter, Alerts) and updates the Settings menu in place.

---

## 📋 Implementation Checklist

Use this checklist to track the rollout of these improvements:

### Phase 1: Safety & Core Reliability
- [ ] **P0:** Remove auto-clear of halt keys; implement `/clear_halt` command.
- [ ] **P0:** Build decoupled `risk_monitor_loop` (5-10s interval) for drawdown/liquidation.
- [ ] **P1:** Build `reconcile_state()` to sync Bybit open orders with Redis/DB on startup.
- [ ] **P1:** Implement `asyncio.Lock` in `_run_loop` to prevent overlapping scans.
- [ ] **P1:** Add `try/except` wrappers to dashboard health checks for graceful degradation.

### Phase 2: Operational Logic
- [ ] **P2:** Refactor signal cache to use Redis TTL (4 hours) instead of clearing.
- [ ] **P2:** Change BEAR market sleep from fixed 1h to dynamic 10m re-evaluation.

### Phase 3: UX & Telegram UI (v2.1 Alignment)
- [ ] **P3:** Implement push alerts for both **Take Profit (Win)** and **Stop Loss (Loss)** triggers.
- [ ] **P3:** Build `📊 View Positions` interactive inline menu.
- [ ] **P3:** Implement `🛡 Move SL to BE` functionality with in-place card editing and Toast notifications.
- [ ] **P3:** Build `📜 Trade History` view (replacing Session History) on the IDLE dashboard.
- [ ] **P3:** Refactor all dashboard handlers (Refresh, Pause, Settings) to use `edit_message_text` to prevent chat spam.
- [ ] **P3:** Ensure all utility formatters (`format_pnl`, `format_position_card`, etc.) are integrated into the new UI flows.