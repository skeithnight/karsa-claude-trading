# 🎨 ASM Telegram Bot: UX & UI Design Specification (v2.1)

This document defines the complete user interface and interaction flows for the **Autonomous Session Manager (ASM) Telegram Bot**. The design focuses on clear, actionable, and visually structured messages using Telegram's MarkdownV2 and Inline Keyboards.

### Core UX Principles
1. **Edit over Send:** To prevent chat spam, the bot uses `edit_message_text` to update existing messages in place for actions like Refresh, Pause, and Settings.
2. **Visual State Feedback:** UI elements (emojis, headers, buttons) dynamically change to reflect the bot's current state (e.g., 🟢 Active vs 🟡 Paused).
3. **Destructive Action Friction:** High-impact actions like stopping a session require explicit confirmation screens.

---

## 1. Core Screens & Main Flow

### Screen 1: IDLE Dashboard (`/start` or `/dashboard`)
**Trigger:** User starts the bot or requests the dashboard while no session is active.

**Message Response:**
```text
🤖 *Autonomous Session Manager*
━━━━━━━━━━━━━━━━━━━━━━━
🟢 *System Status:* Healthy
💰 *Wallet Balance:* $10,000.00
📊 *Market Regime:* BULL 🟢
🏆 *Top Mover:* PEPE (+12.4%)

📉 *Last Trade PnL:* +$450.20 (+4.50%)
━━━━━━━━━━━━━━━━━━━━━━━
Ready to deploy capital. Select an action below.
```

**Inline Keyboard:**
```text
[ 🚀 LAUNCH NEW SESSION ]
[ 📜 Trade History ]    [ ⚙️ Settings ]
```

---

### Screen 2: Configuration Menu
**Trigger:** User clicks `[ 🚀 LAUNCH NEW SESSION ]`.

**Message Response:**
```text
⚙️ *Session Configuration*
━━━━━━━━━━━━━━━━━━━━━━━
Please configure your risk parameters. 
*Note: The session will run continuously until manually stopped or halted by risk limits.*

*Current Wallet:* $10,000.00
```

**Inline Keyboard:**
```text
*Select Risk Level:*
[ ▶️ 10% ($1k) ]  [ ▶️ 30% ($3k) ]
[ 🚀 50% ($5k) ]  [ 🔥 100% ($10k) ]

[ ❌ Cancel ]
```
*(Callback data format: `asm:config:risk:30`. Clicking a risk button immediately triggers the session start).*

---

### Screen 3: ACTIVE Dashboard
**Trigger:** A session is successfully launched, or user clicks refresh while active.

**Message Response:**
```text
🟢 *ACTIVE SESSION* 🟢
━━━━━━━━━━━━━━━━━━━━━━━
🆔 *Session ID:* `a8f9-2b`
⏱ *Uptime:* 2h 14m | *Next Scan:* 4m
💰 *Starting Equity:* $10,000.00
📈 *Current Equity:* $10,450.00

*Performance:*
Realized PnL: +$300.00 🟢
Unrealized PnL: +$150.00 🟢
Total PnL: +$450.00 (+4.50%) 🟢

📂 *Open Positions:* 3 / 5 Max
```

**Inline Keyboard:**
```text
[ 📊 View Positions ]  [ 🔄 Refresh ]
[ ⏸ Pause Session ]    [ 🛑 Stop & Close All ]
```

---

### Screen 4: View Positions (Sub-Menu)
**Trigger:** User clicks `[ 📊 View Positions ]` on the Active Dashboard.

**Message Response:**
```text
📊 *Open Positions Detail*
━━━━━━━━━━━━━━━━━━━━━━━
*1. BTC/USDT (LONG)* 🟢
┣ Entry: $65,000 | Now: $65,500
┣ Size: 0.05 BTC | Liq: $58,000
┣ PnL: +$25.00 (+1.5%)
┗ SL: $64,000 | TP: $67,000

*2. ETH/USDT (SHORT)* 🔴
┣ Entry: $3,400 | Now: $3,450
┣ Size: 2.0 ETH | Liq: $3,800
┣ PnL: -$100.00 (-1.4%)
┗ SL: $3,500 | TP: $3,200

💡 *Note:* "Move SL to BE" shifts your Stop Loss to your exact Entry Price, securing a risk-free trade.
```

**Inline Keyboard:**
```text
*BTC/USDT:*
[ 🏃 Close BTC ]  [ 🛡 Move SL to BE ]

*ETH/USDT:*
[ 🏃 Close ETH ]  [ 🛡 Move SL to BE ]

[ 🔙 Back to Dashboard ]
```

---

### Screen 5: Proactive Alerts (Push Notifications)
**Trigger:** Background tasks detect SL/TP hits or drawdown warnings. These are sent as *new* messages to the chat.

**Message Response (Take Profit Hit - Win):**
```text
🎯 *TAKE PROFIT HIT* 🎯
━━━━━━━━━━━━━━━━━━━━━━━
*Symbol:* SOL/USDT (LONG)
*Exit Price:* $145.00
*PnL:* +$250.00 (+5.0%) 🟢

Position closed successfully.
```
**Inline Keyboard:** `[ 👀 View Dashboard ]`

**Message Response (Stop Loss Hit - Loss):**
```text
🛑 *STOP LOSS HIT* 🛑
━━━━━━━━━━━━━━━━━━━━━━━
*Symbol:* AVAX/USDT (SHORT)
*Exit Price:* $38.50
*PnL:* -$120.00 (-2.4%) 🔴

Position closed to protect capital.
```
**Inline Keyboard:** `[ 👀 View Dashboard ]`

---

## 2. Interactive Actions & State Transitions

### Action 1: Settings (`⚙️ Settings`)
**Trigger:** User clicks `[ ⚙️ Settings ]` from the IDLE or ACTIVE dashboard.
**UX Behavior:** **Seamless Edit.** Toggles update Redis and edit the message in place.

**Message Response:**
```text
⚙️ *Bot Settings & Preferences*
━━━━━━━━━━━━━━━━━━━━━━━
*Current Configuration:*

📂 *Max Open Positions:* 5
📊 *Regime Filter:* ENABLED (Only trade in BULL/NEUTRAL)
🔔 *Trade Alerts:* ENABLED (SL/TP notifications)

Select a parameter below to modify it.
```

**Inline Keyboard:**
```text
[ 📂 Max Pos: 5 ]   [ 📊 Regime: ON ]
[ 🔔 Alerts: ON ]   

[ 🔙 Back to Dashboard ]
```

---

### Action 2: Stop & Close All (`🛑 Stop`)
**Trigger:** User clicks `[ 🛑 Stop & Close All ]` on the ACTIVE dashboard.
**UX Behavior:** **Requires Confirmation.** This is a destructive action.

**Step 1: Confirmation Screen**
```text
🛑 *CONFIRM STOP SESSION* 🛑
━━━━━━━━━━━━━━━━━━━━━━━
Are you sure you want to stop the session?

This will:
1️⃣ Halt all new scanning.
2️⃣ *Market Close* all 3 open positions immediately.
3️⃣ Return the bot to IDLE state.
```
**Inline Keyboard:**
```text
[ ✅ YES, STOP & CLOSE ]
[ ❌ NO, KEEP RUNNING ]
```

**Step 2: Execution & Success (Edits the confirmation message)**
```text
✅ *SESSION STOPPED SUCCESSFULLY*
━━━━━━━━━━━━━━━━━━━━━━━
📂 *Closed Positions:* 3
💰 *Final Session PnL:* +$450.00 (+4.50%) 🟢

The bot is now IDLE.
```
**Inline Keyboard:**
```text
[ 🚀 LAUNCH NEW SESSION ]
[ 📜 Trade History ]
```

---

### Action 3: Refresh (`🔄 Refresh`)
**Trigger:** User clicks `[ 🔄 Refresh ]` on the ACTIVE dashboard.
**UX Behavior:** **Seamless Edit.** The bot fetches the latest data and uses `edit_message_text` to update the dashboard in place. *(Note: The `send_or_edit_message` helper silently ignores "Message is not modified" Telegram errors).*

---

### Action 4: Pause / Resume Session (`⏸ Pause` / `▶️ Resume`)
**Trigger:** User clicks `[ ⏸ Pause Session ]` on the ACTIVE dashboard.
**UX Behavior:** **State Toggle.** Updates Redis state to `PAUSED` and edits the dashboard to reflect the paused status.

**Updated ACTIVE Dashboard (When Paused):**
```text
🟡 *PAUSED SESSION* 🟡
━━━━━━━━━━━━━━━━━━━━━━━
🆔 *Session ID:* `a8f9-2b`
⏱ *Uptime:* 2h 14m | *Status:* PAUSED
💰 *Starting Equity:* $10,000.00
📈 *Current Equity:* $10,450.00

*Performance:*
Realized PnL: +$300.00 🟢
Unrealized PnL: +$150.00 🟢
Total PnL: +$450.00 (+4.50%) 🟢

📂 *Open Positions:* 3 (Scanning Disabled)
```
**Inline Keyboard:**
```text
[ 📊 View Positions ]  [ 🔄 Refresh ]
[ ▶️ RESUME SESSION ]  [ 🛑 Stop & Close All ]
```

---

### Action 5: Move SL to BE (`🛡 Move SL to BE`)
**Trigger:** User clicks `[ 🛡 Move SL to BE ]` inside the **View Positions** screen.
**UX Behavior:** **Targeted Update + Toast.** The bot amends the order on Bybit. Upon success, it edits *only that specific position's card* within the message to show the updated SL, and sends a quick confirmation toast.

**Step 1: Updated Position Card (Edited in place)**
```text
📊 *Open Positions Detail*
━━━━━━━━━━━━━━━━━━━━━━━
*1. BTC/USDT (LONG)* 🟢
┣ Entry: $65,000 | Now: $65,500
┣ Size: 0.05 BTC | Liq: $58,000
┣ PnL: +$25.00 (+1.5%)
┗ SL: $65,000 (BE) ✅ | TP: $67,000  <-- UPDATED LINE

*2. ETH/USDT (SHORT)* 🔴
... (rest of the message remains the same)

💡 *Note:* "Move SL to BE" shifts your Stop Loss to your exact Entry Price, securing a risk-free trade.
```

**Step 2: Toast Notification (Sent as a new, short message)**
```text
✅ *SL Moved to Breakeven*
*Symbol:* BTC/USDT
*New SL:* $65,000.00
```
**Inline Keyboard:** `[ 🗑 Dismiss ]` *(Clicking this deletes the toast message).*

---

## 3. Integration with `utils` Formatters

To ensure the UX is clean and consistent, the bot handlers must utilize the existing formatting helpers located in the `src/utils/` directory.

### A. Financial & Number Formatters (`src/utils/formatters.py`)

| Utility Function | Purpose | UX Application Example |
| :--- | :--- | :--- |
| `format_usd(value: float)` | Formats floats to `$1,234.56` | Used for Wallet Balance, Entry/Current Prices, Equity. |
| `format_pct(value: float)` | Formats to `+12.34%` or `-5.67%` | Used for Market Regime movers, Position PnL percentages. |
| `format_pnl(value: float)` | Adds 🟢/🔴 emojis and formats USD | Used for Realized/Unrealized PnL in the Active Dashboard & Alerts. |
| `format_number(value, decimals)` | Truncates floats to specific decimals | Used for Position Sizes (e.g., `0.05` BTC, `2.0` ETH). |

### B. Telegram Specific Helpers (`src/utils/telegram_helpers.py`)

| Utility Function | Purpose | UX Application Example |
| :--- | :--- | :--- |
| `build_inline_keyboard(buttons_dict)` | Converts a dict/list into `InlineKeyboardMarkup` | Standardizes keyboard generation across all screens. |
| `escape_markdown_v2(text: str)` | Escapes special characters for MDV2 | Prevents bot crashes when sending messages with `$`, `_`, or `*`. |
| `send_or_edit_message(update, text, kb)`| Smart sender: edits existing msg or sends new | Prevents "Message is not modified" errors when user clicks `[ 🔄 Refresh ]`. |

### C. Required Custom Formatters (Action Items for Dev)

Ensure the following specific helpers exist in `src/utils/formatters.py`:

1.  **`format_position_card(position: dict) -> str`**: 
    *   *Why:* The "View Positions" screen requires a highly specific, multi-line formatted string for each position. Centralizing this logic keeps the handler clean.
2.  **`format_risk_button_text(risk_pct: float, wallet_bal: float) -> str`**:
    *   *Why:* The config menu needs to show both the percentage and the exact dollar amount (e.g., `▶️ 30% ($3k)`). This helper calculates the dollar amount dynamically based on the current wallet balance.
3.  **`get_regime_display(regime: str) -> str`**:
    *   *Why:* Standardizes the output of the market regime (e.g., mapping `BULL` to `BULL 🟢`, `BEAR` to `BEAR 🔴`, `NEUTRAL` to `NEUTRAL 🟡`).