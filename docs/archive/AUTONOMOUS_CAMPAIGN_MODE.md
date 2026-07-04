# Karsa Trading System: Autonomous Campaign Mode (Hardened Implementation Plan)

**Repository:** skeithnight/karsa-claude-trading  
**Document Version:** 2.0 (Post-Audit Redesign)  
**Date:** 2026-07-03  
**Status:** Production-Ready Architecture Specification  

---

## 📋 Executive Summary

This document outlines the hardened, institutional-grade architecture for the **Autonomous Campaign Mode** in Karsa. 

Following a brutal 6-point architectural audit of the initial design, this plan eliminates critical failure modes including "Ghost Sessions" (memory state desync), "Double-Execution Collisions" (competing orchestrators), "Unrealized PnL Sizing Traps" (death spirals), and "Regime Blindness" (trading into meat grinders).

This redesign transitions the Autonomous Mode from a fragile retail script into a **self-healing, state-resilient, regime-aware autonomous trading agent**.

---

## 🏗️ Architecture Overview

The Autonomous Session Manager (ASM) does not replace the core Karsa infrastructure; it acts as a **supervisory control layer** that orchestrates the existing Advisory, Risk, and Execution engines under strict, user-defined campaign parameters.

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                      TELEGRAM COMMAND INTERFACE                         │
│        /auto_start (params)  │  /auto_stop  │  /auto_status             │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              AUTONOMOUS SESSION MANAGER (The Brain)                     │
│                                                                         │
│  1. STATE CHECK: Am I active? (Redis)                                   │
│  2. RESURRECTION: Did I crash? Auto-restart loop if Redis says yes.     │
│  3. REGIME GATE: Is macro environment safe? (Pause if BEAR/CHOP)        │
│  4. CAPACITY CHECK: Am I at max positions?                              │
│  5. SCANNING: Fetch Universe -> Run Technicals -> Filter by Confidence  │
│  6. TELEMETRY: Update Prometheus / Send Telegram Progress (w/ Cooldown) │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │ (If Signal Found)
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      EXECUTION & RISK LAYER                             │
│                                                                         │
│  1. DISTRIBUTED LOCK: Acquire Redis lock for {symbol}                   │
│  2. CASH SIZING: Calculate size based on AVAILABLE CASH (Not Equity!)   │
│  3. RISK GATES: Pass through existing 8 Crypto Risk Gates               │
│  4. SOR: Smart Order Router executes Maker/Taker order                  │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   EXISTING KARSA INFRASTRUCTURE                         │
│  • StopLossEngine (WS Ticker + REST Fallback)                           │
│  • TakeProfitEngine                                                     │
│  • Postgres Trade Ledger (Source of Truth for Final Reporting)          │
│  • 9Router (LLM Gateway with Semantic Caching)                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 🛡️ Core Design Principles (The "Hardening")

To prevent the 6 critical flaws identified in the audit, the ASM strictly adheres to these four immutable laws:

1. **Ephemeral Memory is Forbidden:** The Python process can die at any time. All session state, locks, and metrics are stored in Redis. On boot, the system checks Redis and resurrects the loop if a session was interrupted.
2. **Cash is King (No Floating PnL Sizing):** Position sizing is calculated using `availableBalance` (realized cash), strictly forbidding the use of `totalEquity` (which includes unrealized floating PnL).
3. **One Brain, One Body:** A distributed Redis lock prevents the ASM and the standard Orchestrator from executing trades on the same symbol simultaneously.
4. **Truth is in the Database:** Final performance reports do not rely on Redis counters. They query the Postgres trade ledger for realized PnL and calculate live Mark-to-Market (MTM) for open positions.

---

## ⚙️ Component Implementation Details

### 1. State Management & Container Resurrection (Fixes Flaw #1)
The session state is entirely decoupled from Python memory. If the Docker container OOM-kills, the loop resurrects automatically on the next boot.

**Redis Schema:**
* `karsa:auto:state:active` -> `"1"` or `"0"`
* `karsa:auto:config` -> JSON of session parameters (risk_pct, max_pos, etc.)

**Boot Sequence (`src/main_crypto.py`):**
```python
async def on_startup():
    # ... existing OMS reconciliation ...
    
    # Check for Ghost Sessions (Container crashed while running)
    is_active = await redis_client.get("karsa:auto:state:active")
    if is_active == b"1" or is_active == "1":
        logger.critical("🚨 Detected interrupted Autonomous Session. Resurrecting loop...")
        config = await redis_client.hgetall("karsa:auto:config")
        
        # Silently resurrect the loop without sending a "Started" Telegram message
        asm = AutonomousSessionManager(redis_client, bybit_client, config)
        asyncio.create_task(asm._run_resurrected_loop(ADMIN_CHAT_ID))
```

### 2. Distributed Execution & Cash Sizing (Fixes Flaws #2 & #3)
Before any order is sent to the Smart Order Router (SOR), the ASM must acquire a lock and size the trade using *only* realized cash.

**Execution Lock (`src/risk/distributed_lock.py`):**
```python
async def acquire_execution_lock(symbol: str, ttl_seconds: int = 60) -> bool:
    """Prevents double-execution between ASM and standard Orchestrator."""
    lock_key = f"karsa:lock:exec:{symbol}"
    # SET NX EX: Set if Not Exists, with Expiration
    acquired = await redis_client.set(lock_key, "1", nx=True, ex=ttl_seconds)
    return bool(acquired)
```

**Cash-Based Sizing (`src/agents/autonomous_session.py`):**
```python
async def _calculate_position_size(self, signal: dict) -> float:
    """Sizes position using AVAILABLE CASH, not Total Equity."""
    wallet = await self.bybit.get_wallet_balance(accountType="CONTRACT")
    
    # CRITICAL: Use availableBalance (cash), NOT totalEquity (cash + floating PnL)
    available_cash = float(wallet['coin'][0]['availableToTrade']) 
    
    risk_amount = available_cash * self.config.risk_per_trade_pct
    
    # Calculate size based on SL distance
    sl_distance_pct = abs(signal["entry_price"] - signal["stop_loss"]) / signal["entry_price"]
    position_size_usd = risk_amount / sl_distance_pct
    
    return position_size_usd
```

### 3. Regime-Aware Loop & Telemetry (Fixes Flaws #5 & #6)
The loop checks the macro regime before scanning and uses Redis timestamps to prevent Telegram spam.

**The Hardened Loop (`src/agents/autonomous_session.py`):**
```python
async def _run_loop(self, chat_id: int):
    while True:
        # 1. Check if manually stopped via Telegram
        if not await self._is_active():
            logger.info("Autonomous session stopped via Telegram.")
            break

        try:
            # 2. REGIME GATE (Fixes Flaw #6)
            macro_regime = await self.regime_filter.get_global_regime()
            if macro_regime in [Regime.PURE_DEAD_CHOP, Regime.TREND_BEAR]:
                if await self._should_send_regime_alert():
                    await self.telegram.send_message(
                        chat_id, 
                        f"⚠️ <b>Auto-Session Paused</b>\nMarket is in {macro_regime}. Waiting for trend."
                    )
                await asyncio.sleep(3600) # Sleep 1 hour before re-checking regime
                continue

            # 3. CAPACITY CHECK
            open_positions = await self._get_open_position_count()
            if open_positions >= self.config.max_concurrent_positions:
                await asyncio.sleep(self.config.scan_interval_minutes * 60)
                continue

            # 4. SCANNING & EXECUTION
            signals = await self._scan_for_high_quality_signals()
            for signal in signals:
                if await self._get_open_position_count() >= self.config.max_concurrent_positions:
                    break
                await self._execute_signal(signal, chat_id)

            # 5. TELEMETRY / PROGRESS UPDATE (Fixes Flaw #5)
            await self._send_progress_update_if_due(chat_id)

        except Exception as e:
            logger.critical(f"Error in autonomous loop: {e}")
            await self.telegram.send_message(chat_id, f"🚨 <b>Loop Error:</b> {e}")

        await asyncio.sleep(self.config.scan_interval_minutes * 60)

async def _send_progress_update_if_due(self, chat_id: int):
    """Prevents Telegram spam by enforcing a strict 1-hour cooldown via Redis."""
    last_update = await redis_client.get("karsa:auto:last_progress_ts")
    now = time.time()
    
    if last_update and (now - float(last_update)) < 3600:
        return # Cooldown active, do not send
        
    # Send update...
    await self._generate_and_send_progress(chat_id)
    
    # Update timestamp
    await redis_client.set("karsa:auto:last_progress_ts", str(now))
```

### 4. Mark-to-Market (MTM) Final Reporting (Fixes Flaw #4)
When `/auto_stop` is called, the bot generates a professional tear sheet. It does not trust Redis counters; it queries the Postgres ledger and calculates live MTM.

**Final Report Generator (`src/agents/autonomous_session.py`):**
```python
async def _generate_final_report(self) -> str:
    """Generates institutional-grade MTM performance report."""
    start_time = await redis_client.get("karsa:auto:start_time")
    
    # 1. Query Postgres for all trades opened during this session
    session_trades = await self.db.get_trades_by_timeframe(float(start_time), time.time())
    
    realized_pnl = 0.0
    wins = 0
    losses = 0
    
    for trade in session_trades:
        if trade.is_closed:
            realized_pnl += trade.realized_pnl
            if trade.realized_pnl > 0: wins += 1
            else: losses += 1
            
    # 2. Calculate live Mark-to-Market (MTM) for STILL OPEN positions
    open_trades = [t for t in session_trades if not t.is_closed]
    unrealized_pnl = 0.0
    
    for trade in open_trades:
        current_price = await self.bybit.get_ticker(trade.symbol)
        unrealized_pnl += calculate_unrealized_pnl(trade, float(current_price['lastPrice']))
        
    total_mtm_pnl = realized_pnl + unrealized_pnl
    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    
    # 3. Fetch starting equity from Redis
    start_equity = float(await redis_client.get("karsa:auto:start_equity"))
    
    report = f"""
🏁 *AUTONOMOUS SESSION COMPLETED*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *Financial Summary:*
• Starting Equity: `${start_equity:,.2f}`
• Realized PnL: `${realized_pnl:,.2f}`
• Open/Unrealized PnL: `${unrealized_pnl:,.2f}`
• *Total MTM PnL:* `${total_mtm_pnl:,.2f}`

📊 *Performance Metrics:*
• Total Closed Trades: `{total_trades}`
• Win Rate: `{win_rate:.1f}%` ({wins}W / {losses}L)
• Open Positions: `{len(open_trades)}` (Managed by SL/TP Engine)

⏱️ *Session Info:*
• Status: `Halted by User`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    """
    
    # 4. Clean up Redis state
    await self._cleanup_session_state()
    
    return report.strip()
```

---

## 📱 Telegram Command Interface

The user interacts with the ASM via three primary commands.

| Command | Description | Parameters |
| :--- | :--- | :--- |
| `/auto_start` | Initiates a new autonomous campaign. Validates parameters and snapshots starting equity. | `risk_pct` (default: 2.0), `max_pos` (default: 3), `interval` (default: 15m) |
| `/auto_stop` | Halts the scanning loop. Generates and sends the final MTM performance report. Leaves open positions to the SL/TP engine. | None |
| `/auto_status` | Forces an immediate progress update (bypasses the 1-hour cooldown). Shows current MTM PnL and open positions. | None |

**Example Interaction:**
```text
User: /auto_start risk=1.5 max_pos=2
Bot: 🚀 Autonomous Session Started
     Starting Cash: $10,000.00
     Risk per trade: 1.5% | Max positions: 2
     Scanning every 15m. Regime checks active.

[... 4 hours later ...]

Bot: 🟢 Autonomous Session Progress
     ━━━━━━━━━━━━━━━━━━━━
     💰 Current Cash: $10,150.00
     📈 Realized PnL: +$120.00
     📊 Open MTM PnL: +$30.00
     ━━━━━━━━━━━━━━━━━━━━
     📊 Trades: 2 (2W / 0L) | Win Rate: 100%
     ⏱️ Running for: 4h 15m

User: /auto_stop
Bot: 🏁 AUTONOMOUS SESSION COMPLETED ... [Full Tear Sheet]
```

---

## 📊 Grafana & Prometheus Integration

To monitor the ASM in Grafana, the following metrics are exposed via the `prometheus_client`:

```python
# src/telemetry/prometheus_metrics.py
from prometheus_client import Gauge, Counter

# Session State
AUTO_SESSION_ACTIVE = Gauge('karsa_auto_session_active', '1 if auto session is running, 0 if stopped')
AUTO_SESSION_CASH_USD = Gauge('karsa_auto_session_available_cash_usd', 'Available cash (excluding floating PnL)')

# Performance
AUTO_SESSION_REALIZED_PNL = Gauge('karsa_auto_session_realized_pnl_usd', 'Realized PnL during session')
AUTO_SESSION_UNREALIZED_PNL = Gauge('karsa_auto_session_unrealized_pnl_usd', 'Unrealized MTM PnL')

# Execution
AUTO_SESSION_TRADES_TOTAL = Counter('karsa_auto_session_trades_total', 'Total trades taken', ['result']) # result: win, loss
AUTO_SESSION_REGIME_PAUSES = Counter('karsa_auto_session_regime_pauses_total', 'Times loop paused due to bad regime')
```

**Grafana Dashboard Panels:**
1. **Cash vs MTM Equity:** Stacked area chart showing `AUTO_SESSION_CASH_USD` + `AUTO_SESSION_UNREALIZED_PNL`.
2. **Regime Pauses:** Bar chart of `AUTO_SESSION_REGIME_PAUSES` to see how often the bot saved you from chop.
3. **Trade Distribution:** Pie chart of `AUTO_SESSION_TRADES_TOTAL` by `result`.

---

## 🚀 Deployment & Rollback Plan

### Phase 1: Testnet Validation (Days 1-3)
1. Deploy the hardened ASM code to the `testnet` Docker environment.
2. Run `/auto_start` with minimal risk (0.5%).
3. **Chaos Testing:** 
   * Manually `docker kill` the orchestrator container while a session is active. Verify it resurrects on the next `docker compose up -d`.
   * Manually trigger a `PURE_DEAD_CHOP` regime in the mock data. Verify the loop pauses and sends the Telegram alert.
   * Verify position sizing strictly uses `availableToTrade` and ignores floating PnL.

### Phase 2: Production Soft Launch (Days 4-7)
1. Deploy to production.
2. Run `/auto_start` with strict limits: `risk=1.0`, `max_pos=1`.
3. Monitor Grafana for Redis lock contention and API rate limits.
4. Verify Telegram progress updates respect the 1-hour cooldown.

### Phase 3: Full Autonomy (Day 8+)
1. Increase parameters to target campaign settings (`risk=2.0`, `max_pos=3`).
2. Monitor the Mark-to-Market final reports for accuracy against manual broker statements.

---

## 📝 Conclusion

By addressing the 6 critical flaws identified in the initial audit, the Autonomous Campaign Mode is now architected to survive container crashes, network partitions, and hostile market regimes. 

It strictly separates **state** (Redis), **truth** (Postgres), and **execution** (Distributed Locks), ensuring that when you press `/auto_start`, the system operates with the reliability and risk-management rigor of a professional quantitative trading desk.