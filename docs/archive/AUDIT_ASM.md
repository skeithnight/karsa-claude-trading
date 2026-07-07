### 🚨 Critical Logic Traps (Must Fix)

#### Trap #1: The "Clear Win" Dump (The +3% Trap)
**The Flaw:** In Phase 2, if a coin pumps to `+4%`, the ASM says "Clear Win! -> HOLD" and advances the checkpoint. But what if the coin immediately dumps to `-5%` five minutes later? 
Because it advanced the checkpoint, the next check might be the 4-hour checkpoint (which allows `-2%`). The bot will hold a `-5%` position, completely forgetting it was just up `+4%`.
**The Fix:** A "Clear Win" **must** automatically activate a trailing stop. When it crosses `+3%`, the ASM should instantly write `dynamic_stop_pct = 1.0%` (or your breakeven fee amount) to the database. If it dumps, the trailing stop catches it, not the scheduled checkpoint.

#### Trap #2: The "Mid-Checkpoint" Bleed Blindspot
**The Flaw:** Look at the time between checkpoints. If a Meme coin passes the 1-hour checkpoint (at `+1.5%`), the next checkpoint is at 2 hours. What happens at **1 hour and 45 minutes** if the price drops to `-4%`? 
The ASM just says "Waiting for time" and does **nothing**. It doesn't trigger the AI, and it hasn't hit the `-8%` hard fail. It just bleeds silently for 15 more minutes.
**The Fix:** You need a **"Drawdown from Peak"** check. If a position drops by more than `X%` (e.g., 3%) from its highest recorded gain *since the last checkpoint*, it should immediately trigger the AI Judge, regardless of the time schedule.

#### Trap #3: The `NULL` Dynamic Stop Crash
**The Flaw:** Your database column `dynamic_stop_pct` defaults to `NULL`. In Python, evaluating `if current_gain < position.dynamic_stop_pct` when the value is `None` will either throw a `TypeError` or evaluate incorrectly, crashing the loop or skipping the trailing stop entirely.
**The Fix:** The Python guardrail must explicitly check for `None` first: 
`if position.dynamic_stop_pct is not None and current_gain < position.dynamic_stop_pct:`

---

### ⚠️ Edge Cases (Silent Killers)

#### Edge Case #1: Stale Price Feeds / RPC Lag
**The Flaw:** If your RPC node lags or the DEX API returns a stale price, the ASM might calculate a fake `-10%` drop and trigger a Hard Fail, exiting a perfectly good trade.
**The Fix:** Add a **Price Freshness Check**. Every time you fetch the price, check the timestamp. If the price data is older than 2 minutes, **skip the Hard Fail check** for this loop and log a warning.

#### Edge Case #2: AI Infinite "HOLD" Loop
**The Flaw:** If a trade is bleeding slowly, the AI Judge might say "HOLD" at 1h, "HOLD" at 4h, and "HOLD" at 12h. The bot will hold it until it hits the `-8%` hard fail, wasting capital.
**The Fix:** Pass `consecutive_ai_holds` to the AI prompt. Add a rule to the AI's system prompt: *"If consecutive_ai_holds >= 3 and the position is still negative, you MUST exit. Do not hold a bleeding trade indefinitely."*

---

### 📊 Corrected ASM Workflow (v2)

Here is the updated Mermaid diagram with the traps and edge cases fixed.

```mermaid
graph TD
    %% --- PHASE 1: HEARTBEAT & DATA INTEGRITY ---
    A([ASM Loop: Wakes Every 5 Min]) --> B[Fetch Open Positions & Prices]
    B --> B1{Is Price Data Fresh? < 2 mins}
    B1 -- No --> B2[🟡 SKIP Hard Fail, Log Warning]
    B1 -- Yes --> C[Calculate Gain %, Time Held, & Peak Gain]
    
    %% --- PHASE 2: GUARDRAILS ---
    B2 --> C
    C --> D{Gain < -8% OR < dynamic_stop_pct?}
    D -- Yes --> E[🔴 EXIT: Hard Fail / Trailing Stop]
    D -- No --> F{Gain > +3%?}
    F -- Yes --> F1[🟢 CLEAR WIN: Set Trailing Stop to +1.0%]
    F1 --> F2[Advance Checkpoint Index]
    
    %% --- PHASE 3: CHECKPOINTS & DRAWDOWN ---
    F -- No --> G{Drawdown from Peak > 3%?}
    G -- Yes --> L[⚠️ Enter Ambiguous Zone: Trigger AI]
    G -- No --> H{Time >= Next Checkpoint?}
    H -- No --> I[🟡 HOLD: Waiting for Time]
    H -- Yes --> J{Gain >= min_gain_pct?}
    J -- Yes --> K[🟢 PASS: Advance Checkpoint]
    J -- No --> L
    
    %% --- PHASE 4: AI BRAIN ---
    L --> M((Route to PositionJudge))
    M --> N[Tier 1: Cheap LLM]
    N --> O{High Confidence?}
    O -- Yes --> P[Execute Tier 1]
    O -- No --> Q[Tier 2: Deep Dive w/ Summarized OHLCV]
    Q --> R[Execute Tier 2]
    
    %% --- AI ACTIONS ---
    P --> S{AI Final Decision}
    R --> S
    S -- HOLD --> T[🟢 Update DB: last_judgment, consecutive_holds++]
    S -- EXIT --> U[🔴 EXIT: AI Judge Exit]
    S -- TIGHTEN_STOP --> V[💾 Update DB: dynamic_stop_pct]
    
    %% --- STYLING ---
    classDef exit fill:#ff9999,stroke:#cc0000,stroke-width:2px,color:#000;
    classDef hold fill:#99ccff,stroke:#0066cc,stroke-width:2px,color:#000;
    classDef ai fill:#ffcc99,stroke:#cc6600,stroke-width:2px,color:#000;
    classDef db fill:#ccffcc,stroke:#009900,stroke-width:2px,color:#000;
    classDef warn fill:#ffff99,stroke:#cccc00,stroke-width:2px,color:#000;
    
    class E,U exit;
    class F2,I,K,T hold;
    class M,N,O,Q,P,R,S ai;
    class F1,V db;
    class B2 warn;
```

### Summary of the Audit
By adding the **Price Freshness Check**, the **Drawdown from Peak** trigger, and forcing the **+3% Clear Win to set a trailing stop**, you have completely closed the loopholes. The ASM will no longer get tricked by fake price drops, it won't bleed silently between checkpoints, and it will actually lock in profits when a coin pumps. 