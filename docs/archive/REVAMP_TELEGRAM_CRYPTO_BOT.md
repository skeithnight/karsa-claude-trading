### 🏗️ The Unified Architecture

The bot now has a single root view: **The ASM Dashboard**. 
Everything else (History, Profiles, Activity) is accessed via a clean navigation grid directly on this home screen.

```text
[ USER OPENS BOT ]
       │
       ▼
┌──────────────────────────────────────┐
│  🤖 ASM DASHBOARD (THE MAIN HUB)     │
│  (Market Context + Wallet + ASM      │
│   Engine + Navigation Grid)          │
└──────────┬───────────────────────────┘
           │
    ┌──────┼──────┬────────────┬──────────────┐
    ▼      ▼      ▼            ▼              ▼
[LAUNCH] [HISTORY] [PROFILES] [ACTIVITY] [CONTROL]
```

---

### 1. 🏠 THE UNIFIED DASHBOARD (State: IDLE)
*This is the default home screen. It gives the user the "Dashboard" context (Wallet, Market) at the top, and the "ASM" engine status at the bottom, with direct access to all management tools.*

```text
🤖 ASM DASHBOARD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 SYSTEM ONLINE • 🔴 ASM IDLE

💰 CAPITAL & MARKET CONTEXT
Balance: $50,000.00 • Available: 100%
Regime: 🟢 TREND_BULL • Top: SOL (+8.2%)

🤖 AUTONOMOUS ENGINE
Status: 🔴 IDLE • Ready to deploy
Last Run: 🟢 +$1,240 (+2.4%) • 12h ago
Active Profile: ⚖️ BALANCED

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ 🚀 LAUNCH NEW SESSION ]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ 📂 SESSION HISTORY ]  [ ⚙️ MANAGE PROFILES ]
[ 📋 LIVE ACTIVITY   ]  [ 🎛️ GLOBAL CONTROL  ]
```

**UX Breakdown:**
*   **Context at a Glance:** The top section satisfies the need for a "Dashboard" by showing system health, wallet balance, and market regime without requiring a separate screen.
*   **Engine Status:** The middle section clearly shows the ASM is idle, what the last result was, and what profile is currently loaded.
*   **The Grid:** The bottom 2x2 grid provides instant, one-tap access to **Session History** and **Manage Profiles**, fulfilling the requirement to make these core features visible from the main hub.

---

### 2. 🟢 THE UNIFIED DASHBOARD (State: ACTIVE)
*When a session is running, the Dashboard morphs. The "Launch" button is replaced by live controls, and the navigation grid shifts to focus on live management.*

```text
🤖 ASM DASHBOARD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 SYSTEM ONLINE • 🟢 ASM ACTIVE

💰 CAPITAL & MARKET CONTEXT
Balance: $51,240.50 • Margin: $1,250
Regime: 🟢 TREND_BULL • Top: SOL (+8.2%)

🤖 AUTONOMOUS ENGINE
🟢 RUNNING • ID: #A8F3 • Uptime: 04h 12m
PnL: 🟢 +$1,240.50 (+2.48%)
Next Scan: 02m 14s... | Open: 3 Pos

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ ⏸ PAUSE SESSION ]  [ 🛑 STOP SESSION ]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ 📂 SESSION HISTORY ]  [ 💼 OPEN POSITIONS ]
[ 📋 LIVE ACTIVITY   ]  [ 🎛️ GLOBAL CONTROL ]
```

**UX Breakdown:**
*   **Live Morphing:** The "Launch" button disappears, replaced by "Pause" and "Stop". The "Manage Profiles" button is replaced by "Open Positions" because you shouldn't be changing risk profiles while the bot is actively trading.
*   **Heartbeat Integration:** The "Next Scan" timer acts as the heartbeat, proving the bot is alive directly on the main dashboard.

---

### 3. 📂 SESSION HISTORY (Slide-up View)
*Accessed directly from the Unified Dashboard. It slides up or replaces the screen, but always keeps a "Back to Dashboard" button.*

```text
📂 SESSION HISTORY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Total Sessions: 42 • Net PnL: 🟢 +$14,200

🟢 #A8F3 (ACTIVE)
PnL: 🟢 +$1,240 (+2.48%) • 04h 12m
[ VIEW DETAILS ]

🟢 #A8F2 (COMPLETED)
PnL: 🟢 +$1,240 (+2.4%) • Target Reached
[ VIEW DETAILS ] [ 🔄 RERUN CONFIG ]

🔴 #A8F1 (STOPPED)
PnL: 🔴 -$450 (-0.9%) • Drawdown Limit
[ VIEW DETAILS ] [ 🔄 RERUN CONFIG ]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ ⬅️ PREV ]  [ PAGE 1/3 ]  [ NEXT ➡️ ]
[ 🏠 BACK TO ASM DASHBOARD ]
```

---

### 4. ⚙️ MANAGE PROFILES (Slide-up View)
*Accessed directly from the Unified Dashboard. This is where the user defines the bot's "personality" before launching.*

```text
⚙️ MANAGE PROFILES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Select the risk doctrine for the ASM.

🛡️ CONSERVATIVE
Max 1% Risk • Max 2 Pos • 1.0x ATR SL
[ SELECT ] [ EDIT ]

⚖️ BALANCED (CURRENT)
Max 3% Risk • Max 5 Pos • 1.5x ATR SL
[ ACTIVE ] [ EDIT ]

🔥 AGGRESSIVE
Max 5% Risk • Max 8 Pos • 2.0x ATR SL
[ SELECT ] [ EDIT ]

🛠️ CUSTOM
[ ➕ CREATE NEW PROFILE ]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ 🌐 MANAGE UNIVERSE SCOPE ]
[ 🏠 BACK TO ASM DASHBOARD ]
```

---

### 🎨 Why this Unified Design Works

1.  **Zero Navigation Friction:** The user never has to guess "Is the dashboard separate from the ASM?" The moment they open Telegram, they see the market context, their money, and the bot's status all in one unified view.
2.  **Contextual Actions:** The navigation grid at the bottom of the Dashboard changes based on the bot's state. When idle, it offers `Manage Profiles`. When active, it swaps to `Open Positions`. This prevents users from accidentally changing settings while the bot is running.
3.  **History is Front-and-Center:** By putting `Session History` directly on the main dashboard grid, it becomes a first-class citizen. Users are naturally encouraged to review past runs right from the home screen.
4.  **The "Glass Box" Effect:** Because the market context (Regime, Top Movers) is permanently visible at the top of the ASM Dashboard, the user always understands *why* the bot might be making certain decisions, without having to navigate to a separate "Market" tab.