# DESIGN_TEXT_TELEGRAM.md: Karsa Bot UI/UX Specifications (v3.0 - Institutional Grade)

**Document Status:** Approved for Production  
**Target Platform:** Telegram (Mobile & Desktop)  
**Parse Mode:** `HTML` (Strict adherence to Telegram Bot API)  
**Author:** CIO / Head of Trading  

---

## 1. Core Design Philosophy & Telegram API Rules

### 1.1 The "Bloomberg Terminal" Aesthetic
As an institutional trading desk, our Telegram bot must feel like a high-end terminal. 
*   **Monospaced Data:** All tabular data, metrics, and logs must be wrapped in `<pre>` tags to force monospaced alignment.
*   **Visual Anchors:** Use emojis strictly as semantic indicators (🟢 Long/Positive, 🔴 Short/Negative, 🟡 Warning/Hold, ⚪ Neutral/Cash). Do not use them decoratively.
*   **Information Density:** Maximize screen real estate. Avoid unnecessary line breaks. Use horizontal rules (`━━━━━━━━━━━━━━━━`) to separate logical blocks.

### 1.2 Strict Telegram HTML API Constraints
According to the [Telegram Bot API](https://core.telegram.org/bots/api#html-style):
1.  **NO NESTED TAGS IN `<pre>`:** You **cannot** use `<b>`, `<i>`, `<u>`, `<code>`, or `<a>` *inside* a `<pre>` block. They will be rendered as literal text or break the parser.
2.  **Emojis in `<pre>`:** Emojis are fully supported inside `<pre>` blocks and will align correctly if treated as a single character width.
3.  **Strict Escaping:** You MUST escape `<`, `>`, and `&` inside `<pre>` (and everywhere else) as `&lt;`, `&gt;`, and `&amp;`.
4.  **Whitespace Preservation:** Spaces and newlines inside `<pre>` are rendered exactly as written. Use them to manually align columns.

### 1.3 Interactive UI (Inline Keyboards)
Text is for reading; buttons are for acting. Every major dashboard command must include inline keyboard buttons to allow the CIO to drill down without typing.
*   *Example:* The `/briefing` command should have buttons like `[🔍 Audit NVDA]`, `[📊 View PnL]`, `[🌡️ Check Regime]`.

---

## 2. Automated Proactive Intelligence (Push Notifications)

These messages are pushed by the scheduler. They must be highly scannable and actionable.

### 2.1 🚨 Pre-Market Battle Plan (09:25 EST / 09:55 WIB)
*Pushed 5 minutes before the open. Focuses on triggers and risk.*

```html
🚨 <b>KARSA PRE-MARKET BATTLE PLAN</b> 🚨
📅 <i>Fri, Jun 26 | US Equities | 09:25 AM EST</i>

🌡️ <b>REGIME &amp; CONTEXT</b>
<pre>
Vibe        : 🟢 Risk-On (Futures +0.4%)
VIX         : 13.2 (Low)
Catalyst    : PCE Data at 08:30 (High Vol Expected)
</pre>

🎯 <b>ACTIONABLE TRADE IDEAS</b>
<pre>
Ticker  Dir   Trigger Condition         Stop      Target
──────────────────────────────────────────────────────
NVDA    🟢L   Break/Hold $125.50 (1m)   $122.00   $130.00
TLT     🔴S   Reject $92.50 (200 SMA)   $93.20    $90.00
XLE     ⚪N   Wait for pullback to $88  N/A       N/A
</pre>

⚠️ <b>RISK &amp; AVOIDANCE</b>
🚫 <b>DO NOT TOUCH:</b> $TSLA (Earnings after close. Binary risk).
📉 <b>Max Daily Drawdown:</b> -1.5% (Auto-Halt).

💡 <i>Tap buttons below to audit specific ideas.</i>
```
*(Inline Keyboard: `[🔍 Audit NVDA]` `[🔍 Audit TLT]` `[📊 View Full Briefing]`)*

### 2.2 🏁 End-of-Day (EOD) Review (16:15 EST)
*Pushed 15 minutes after close. Summarizes paper performance.*

```html
🏁 <b>KARSA EOD REVIEW</b>
📅 <i>Fri, Jun 26 | 16:15 EST</i>

📊 <b>DAILY PAPER PERFORMANCE</b>
<pre>
Ideas Triggered : 2 / 3
Daily P&amp;L       : 🟢 +$1,250.00 (+0.85%)
Win Rate (Today): 100% (2W / 0L)
</pre>

📝 <b>TRADE RECAP</b>
<pre>
Ticker  Result     P&amp;L       Notes
────────────────────────────────────
NVDA    🟢 Target  +$1,500   Hit $130 target at 14:00
TLT     🔴 Stop    -$250     Stopped out at $93.20
</pre>

💡 <i>System is resetting daily risk limits. Have a good evening.</i>
```

### 2.3 🛑 Automated Kill Switch Alert
*Pushed immediately if Shadow PnL hits the daily loss limit.*

```html
🛑🛑🛑 <b>HALT TRADING ALERT</b> 🛑🛑🛑

<b>CRITICAL RISK BREACH</b>
The Shadow Portfolio has hit the daily loss limit of <b>-1.5%</b>.

<b>DIRECTIVE:</b> 
<i>DO NOT take any new Trade Ideas for the remainder of the session. Review /pnl for details and contact the Quant team.</i>

⏱ <i>Triggered at: 11:42 AM EST</i>
```

---

## 3. Interactive Command Mockups

### 🤖 `/start` (Welcome & Menu)
```html
🤖 <b>KARSA ADVISORY DESK</b>
<i>Institutional AI Quant Research</i>

📊 <b>PORTFOLIO</b>
/portfolio - Holdings &amp; cash
/add, /remove, /edit - Manage positions

🧠 <b>ADVISORY</b>
/analyze - Full portfolio review
/ideas - Active trade signals
/audit &lt;TICKER&gt; - Why did we do this?

📈 <b>CIO DASHBOARD</b>
/briefing - Morning dashboard
/regime - Macro market state
/pnl - Shadow performance
/trades - Paper trade log

⚙️ <b>SYSTEM</b>
/status - Infrastructure health
```
*(Inline Keyboard: `[📊 Portfolio]` `[🧠 Analyze]` `[☀️ Briefing]` `[⚙️ Status]`)*

---

### ☀️ `/briefing` (Morning Dashboard)
```html
☀️ <b>MORNING BRIEFING</b>
📅 <i>2023-10-27 08:00</i>

🌡️ <b>MARKET REGIME</b>
<pre>
State       : 🟢 BULL
VIX         : 16.5 (Low)
SPY         : $415.20 (Above 200 SMA)
Rec         : Risk-On. Favor momentum.
</pre>

💼 <b>PORTFOLIO STATUS</b>
<pre>
Total Value : $125,400.00
Cash        : $50,000.00 (39.8%)
Positions   : 4 open
</pre>

📈 <b>PAPER TRADING</b>
<pre>
Open Trades : 2
Unrealized  : 🟢 +$1,370.00 (+1.1%)
</pre>
```
*(Inline Keyboard: `[🌡️ Deep Regime]` `[📈 View PnL]` `[📋 Open Trades]`)*

---

### 💡 `/ideas` (Active Trade Signals)
```html
💡 <b>ACTIVE TRADE IDEAS</b>
<i>Generated by Karsa Orchestrator</i>

🟢 <b>US MOMENTUM</b>
<pre>
Ticker  Conviction  Entry Zone     Stop      Target
──────────────────────────────────────────────────
NVDA    9.2/10      $120.5-$122.0  $115.0    $135.0
AAPL    8.1/10      $175.0-$176.5  $171.0    $182.0
</pre>

🟡 <b>ETF MEAN REVERSION</b>
<pre>
Ticker  Conviction  Entry Zone     Stop      Target
──────────────────────────────────────────────────
XLE     7.5/10      $88.0-$88.5    $86.5     $91.0
</pre>

💡 <i>Tap a ticker to see the AI's full reasoning.</i>
```
*(Inline Keyboard: `[🔍 Audit NVDA]` `[🔍 Audit AAPL]` `[🔍 Audit XLE]`)*

---

### 🔍 `/audit <ticker>` (The "Why" Command)
*Crucial for CIO oversight. Exposes the LLM's chain-of-thought.*

```html
🔍 <b>AUDIT LOG: NVDA</b>
⏱ <i>Generated: Today, 09:22 AM</i>

📊 <b>SIGNAL METRICS</b>
<pre>
Decision    : 🟢 LONG
Strategy    : US Momentum
Conviction  : 9.2 / 10
Time Horizon: 3 Days
</pre>

🧠 <b>AI REASONING (LLM Synthesis)</b>
<i>Breaking out of a 3-month consolidation on 2x average volume. Earnings beat expectations by 12%. LLM sentiment analysis on semiconductor supply chain news is highly positive (0.85 score). Technicals align with macro regime.</i>

⚖️ <b>RISK MANAGER CHECK</b>
<pre>
Spread Check   : ✅ Pass (0.02%)
Event Risk     : ✅ Pass (No earnings this week)
Sector Limit   : ✅ Pass (Tech at 35%, Limit 50%)
Position Size  : Risk 1% equity = 450 shares
</pre>
```

---

### 💼 `/portfolio` (Holdings & Cash)
```html
💼 <b>PORTFOLIO OVERVIEW</b>
💵 <b>Cash:</b> $50,000 (USD) | Rp 15.0M (IDR)

📈 <b>US MARKET</b>
<pre>
Ticker  Qty    Avg Cost   Curr Price  Unrealized P&amp;L
────────────────────────────────────────────────────
AAPL    100    $175.50    $180.00     🟢 +$450.00
TSLA    50     $240.00    $237.60     🔴 -$120.00
</pre>

📈 <b>IDX MARKET</b>
<pre>
Ticker  Qty    Avg Cost   Curr Price  Unrealized P&amp;L
────────────────────────────────────────────────────
BBCA    500    8,500      8,750       🟢 +Rp 125k
</pre>
```

---

### 🧠 `/analyze` (Deep Dive & Portfolio Review)
```html
🧠 <b>PORTFOLIO ANALYSIS</b>
💰 <b>Value:</b> $125,400 | <b>P&amp;L:</b> 🟢 +4.25% | <b>Cash:</b> 39.8%

📊 <b>US MARKET</b>
<pre>
Action  Ticker  P&amp;L     AI Reasoning Summary
────────────────────────────────────────────
🟢 ADD  AAPL    +2.5%   Breakout confirmed, strong flow
🔴 CUT  TSLA    -5.0%   Below 200 SMA, high beta risk
⚪ HOLD MSFT    +1.2%   Consolidating, no catalyst
</pre>

📊 <b>IDX MARKET</b>
<pre>
Action  Ticker  P&amp;L     AI Reasoning Summary
────────────────────────────────────────────
🟢 ADD  BBCA    +2.1%   Foreign inflow peaking
⚪ HOLD TLKM    -0.5%   Waiting for earnings
</pre>

━━━━━━━━━━━━━━━━
📌 <b>Top Actions:</b>
<i>1. Add to AAPL on pullback to $172.</i>
<i>2. Cut TSLA by 50% to manage risk.</i>

⚠️ <b>Portfolio Risks:</b>
<i>• Tech concentration at 65% (Limit: 50%).</i>
<i>• Portfolio beta is 1.2 (Aggressive).</i>
```

---

### 📊 `/pnl` (Shadow Performance)
```html
📊 <b>SHADOW PORTFOLIO P&amp;L</b>

🟢 <b>OPEN POSITIONS</b>
<pre>
Count       : 2
Unrealized  : 🟢 +$1,370.00 (+1.8%)
</pre>

🏁 <b>CLOSED TRADES</b>
<pre>
Total       : 12
Wins/Losses : 8W / 4L
Win Rate    : 66.7%
Realized    : 🟢 +$3,320.00
Avg P&amp;L     : +2.1%
</pre>
```

---

## 4. Edge Cases & Error Handling

Never show raw Python tracebacks to the CIO. Always catch exceptions and return clean, formatted HTML.

### 4.1 Empty States
When there is no data, don't just say "None". Explain what to do next.

```html
💼 <b>PORTFOLIO OVERVIEW</b>
💵 <b>Cash:</b> $0.00

<i>📭 No positions open.</i>
<i>💡 Use /add &lt;market&gt; &lt;ticker&gt; &lt;qty&gt; &lt;price&gt; to add your first position.</i>
```

### 4.2 System Errors (Database / LLM Down)
```html
❌ <b>SYSTEM ERROR</b>
━━━━━━━━━━━━━━━━
<b>Command:</b> /analyze
<b>Status:</b> Failed

<i>⚠️ The AI Orchestrator is currently unreachable. 
Please check the 9Router status using /status.</i>
```

---

## 5. Developer Implementation Guide

### 5.1 Python Helper: Aligned `<pre>` Tables
Do not manually type spaces. Use this utility to generate perfectly aligned ASCII tables.

```python
import html

def format_pre_table(headers: list[str], rows: list[list[str]], align_right: list[int] = None) -> str:
    """
    Formats data into an aligned ASCII table for Telegram <pre> tags.
    :param align_right: List of column indices that should be right-aligned (e.g., numbers).
    """
    if not rows:
        return "No data available."
    
    align_right = align_right or []
    
    # Calculate max width for each column
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))
            
    # Build header
    header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    separator = "─" * len(header_line)
    
    # Build rows
    table_lines = [header_line, separator]
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            cell_str = str(cell)
            if i in align_right:
                cells.append(cell_str.rjust(col_widths[i]))
            else:
                cells.append(cell_str.ljust(col_widths[i]))
        table_lines.append("  ".join(cells))
        
    return "\n".join(table_lines)

# Usage:
headers = ["Ticker", "Qty", "P&L"]
rows = [["AAPL", "100", "🟢 +$450"], ["TSLA", "50", "🔴 -$120"]]
table_str = format_pre_table(headers, rows, align_right=[1, 2])
final_msg = f"<b>US MARKET</b>\n<pre>\n{html.escape(table_str)}\n</pre>"
```

### 5.2 Python Helper: Message Chunking
Telegram limits messages to 4096 characters. `<pre>` blocks count towards this. If a message is too long, split it logically.

```python
async def send_long_message(update: Update, text: str, parse_mode: str = "HTML"):
    """Sends a message, splitting it into chunks if it exceeds Telegram's 4096 limit."""
    limit = 4000 # Leave buffer for parse tags
    
    if len(text) <= limit:
        await update.message.reply_text(text, parse_mode=parse_mode)
        return

    # Simple chunking by lines (ensure we don't break inside a <pre> tag if possible)
    lines = text.split('\n')
    chunks = []
    current_chunk = []
    current_len = 0
    
    for line in lines:
        if current_len + len(line) + 1 > limit:
            chunks.append('\n'.join(current_chunk))
            current_chunk = [line]
            current_len = len(line)
        else:
            current_chunk.append(line)
            current_len += len(line) + 1
            
    if current_chunk:
        chunks.append('\n'.join(current_chunk))
        
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode=parse_mode)
```

### 5.3 Python Helper: Inline Keyboards
Use `telegram.InlineKeyboardMarkup` to make the bot interactive.

```python
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def build_audit_keyboard(tickers: list[str]):
    """Builds inline buttons for auditing specific tickers."""
    keyboard = []
    row = []
    for i, ticker in enumerate(tickers):
        row.append(InlineKeyboardButton(f"🔍 {ticker}", callback_data=f"audit_{ticker}"))
        if (i + 1) % 3 == 0: # 3 buttons per row
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    return InlineKeyboardMarkup(keyboard)

# In your command handler:
# await update.message.reply_text(text, parse_mode="HTML", reply_markup=build_audit_keyboard(["NVDA", "AAPL", "TSLA"]))
```

### 5.4 Strict HTML Escaping Rule
Whenever you inject dynamic data (especially from the LLM or user input) into an HTML string, you **must** escape it.

```python
import html

# WRONG:
reasoning = result.get('reasoning') # Might contain "<" or ">"
msg = f"<pre>{reasoning}</pre>" 

# CORRECT:
safe_reasoning = html.escape(result.get('reasoning', 'No reasoning'))
msg = f"<pre>{safe_reasoning}</pre>"
```
