# 🔄 ASM Telegram Bot: Workflow Visualizations

## 1. System Architecture & Decoupled Background Tasks
This diagram illustrates the bot's internal logic, highlighting the shift from a monolithic loop to **decoupled background tasks** (Scan Loop vs. Risk Monitor) and the critical **Startup Reconciliation** phase.

```mermaid
flowchart TD
    %% Startup Phase
    subgraph Startup [1. Bot Startup & Reconciliation]
        A[Bot Starts] --> B{Active Session in Redis?}
        B -- Yes --> C[reconcile_state: Sync Bybit vs DB]
        B -- No --> D[Boot to IDLE State]
        C --> E{State is HALTED?}
        E -- Yes --> F[Refuse Start, Alert User to /clear_halt]
        E -- No --> G[Resume Session Tasks]
    end

    %% Background Tasks Phase
    subgraph Tasks [2. Decoupled Background Tasks]
        G --> H[Spawn Scan Loop]
        G --> I[Spawn Risk Monitor]
        
        %% Scan Loop
        subgraph Scan [Scan & Execution Loop - 15m Interval]
            H --> J{Acquire asyncio.Lock?}
            J -- No/Locked --> K[Skip Iteration, Sleep]
            J -- Yes/Acquired --> L{Market Regime?}
            L -- BEAR --> M[Sleep 10m, Re-evaluate Regime]
            L -- BULL/NEUTRAL --> N[Scan Universe for Signals]
            N --> O{Signals Found?}
            O -- Yes --> P[Execute Orders via Bybit]
            O -- No --> Q[Update TTL Signal Cache]
            P & Q --> R[Release Lock, Sleep 15m]
        end

        %% Risk Monitor
        subgraph Risk [Risk Monitor - 5s Interval]
            I --> S[Fetch Equity & Positions via WS/REST]
            S --> T{Drawdown > Threshold OR Liq Proximity?}
            T -- Yes --> U[Trigger HALT State]
            U --> V[Emergency Market Close All]
            V --> W[Send Telegram Alert]
            T -- No --> X[Update Redis Equity Metrics]
            X --> Y[Sleep 5s] --> S
        end
    end
```

***

## 2. Telegram UI State Machine & User Flow
This diagram maps out the exact user journey through the Telegram interface. It emphasizes the **"Edit-over-Send"** paradigm, where most dashboard interactions update the existing message in place to prevent chat spam.

```mermaid
flowchart TD
    %% Entry Points
    Start([User sends /start or /dashboard]) --> IdleDash[🤖 IDLE Dashboard]
    
    %% IDLE Actions
    IdleDash -->|Click 🚀 LAUNCH| Config[⚙️ Config Menu: Select Risk %]
    Config -->|Select Risk| Launch[Start Session & Update State]
    Launch --> ActiveDash[🟢 ACTIVE Dashboard]
    
    IdleDash -->|Click 📜 Trade History| TradeHist[📜 Trade History View]
    IdleDash -->|Click ⚙️ Settings| Settings[⚙️ Settings Toggles: Max Pos, Regime, Alerts]
    
    %% ACTIVE Actions (Edit-over-Send Paradigm)
    ActiveDash -->|Click 🔄 Refresh| EditActive[Edit Msg: Update PnL/Equity in place]
    ActiveDash -->|Click ⏸ Pause| PausedDash[🟡 PAUSED Dashboard - Scanning Disabled]
    PausedDash -->|Click ▶️ Resume| ActiveDash
    
    %% Position Management
    ActiveDash -->|Click 📊 View Pos| ViewPos[📊 Open Positions Detail Cards]
    ViewPos -->|Click 🛡 Move SL to BE| EditCard[Edit Card: SL = Entry Price]
    EditCard --> Toast[Send Toast: ✅ SL Moved + Dismiss Button]
    ViewPos -->|Click 🏃 Close| ClosePos[Market Close Specific Position]
    
    %% Destructive Actions
    ActiveDash -->|Click 🛑 Stop| ConfirmStop[🛑 Confirm Stop Screen]
    ConfirmStop -->|Click ✅ YES| CloseAll[Market Close All & Halt Session]
    CloseAll --> IdleDash
    ConfirmStop -->|Click ❌ NO| ActiveDash
    
    %% Background Proactive Alerts
    subgraph Background Alerts [3. Proactive Push Notifications]
        RiskMonitor((Risk Monitor / WS)) -->|Take Profit Hit| AlertWin[🎯 Send TP Alert Msg + View Dashboard Btn]
        RiskMonitor -->|Stop Loss Hit| AlertLoss[🛑 Send SL Alert Msg + View Dashboard Btn]
    end

    %% Styling
    classDef idle fill:#f9f9f9,stroke:#333,stroke-width:2px;
    classDef active fill:#e6ffed,stroke:#28a745,stroke-width:2px;
    classDef paused fill:#fff9e6,stroke:#ffc107,stroke-width:2px;
    classDef alert fill:#ffe6e6,stroke:#dc3545,stroke-width:2px;
    
    class IdleDash,TradeHist,Settings idle;
    class ActiveDash,EditActive,ViewPos,EditCard,Toast active;
    class PausedDash paused;
    class AlertWin,AlertLoss alert;
```

---

### Key Workflow Takeaways from the Visuals:

1. **Decoupled Execution:** The `Scan Loop` (15m) and `Risk Monitor` (5s) operate completely independently. If the Scan Loop is slow or blocked by the `asyncio.Lock`, the Risk Monitor continues to protect the capital every 5 seconds.
2. **State Persistence:** The startup flow explicitly checks for a `HALTED` state. The bot will *never* auto-resume trading after a crash if it was previously halted due to a drawdown.
3. **Frictionless but Safe UI:** The user can launch a session with a single click (no duration or high-risk friction screens), but destructive actions like `🛑 Stop` require a dedicated confirmation step.
4. **Clean Chat History:** By utilizing `edit_message_text` for Refresh, Pause, and Move SL to BE, the chat history remains a clean, single "Dashboard" message rather than a cluttered feed of updates. Only critical events (TP/SL hits) generate *new* messages.