# Karsa Crypto Telegram UI Design System
Version: v2
Target: Telegram Bot API (HTML)
Reference:
- https://core.telegram.org/api/entities
- https://github.com/gramiojs/format

---

# Philosophy

The bot should look like Bloomberg Terminal meets GitHub Actions.

Goals:

- Extremely scannable
- No emoji spam
- Fixed visual hierarchy
- Every command uses identical layout
- Numbers easier to compare
- Status easier to identify

Information order:

Header
↓
Summary
↓
Metrics
↓
Analysis
↓
Actions
↓
Footer

---

# Typography

Use only four formatting styles.

## Title

<b>...</b>

Example

📊 Portfolio

---

## Section

<b>Wallet</b>

---

## Inline Label

<b>Balance</b>

---

## Fixed Width

<pre>

Tables
Numbers
Logs

</pre>

---

## Command

<code>/portfolio</code>

---

# Status Color Language

| Status | Emoji |
|----------|--------|
Healthy | 🟢
Warning | 🟡
Danger | 🔴
Information | 🔵
Action | ⚡
Success | ✅
Rejected | ⛔
Paused | ⏸️
Running | ▶️
Stopped | ⏹️

Never invent new emoji.

---

# Global Layout

Every screen follows

HEADER

Summary sentence

Section A

Section B

Section C

Actions

Timestamp

Example

📊 Portfolio

3 open positions
Total PnL +$245.30

Wallet
...

Positions
...

Risk
...

Actions

/scan BTC
/sellall

────────────
2026-06-30 22:15

---

# Header Style

Instead of

📊 CRYPTO STATUS

Use

━━━━━━━━━━━━━━━━
📊 Karsa Crypto Status
━━━━━━━━━━━━━━━━

or

<b>📊 Karsa Crypto Status</b>

Do not mix both.

Prefer bold title only.

---

# Timestamp

Move timestamp to footer.

Not header.

Example

────────────
2026-06-30 22:10 WIB

instead of

2026...
Status...

Reason:

Users care about data first.

---

# START

Current

Long command list.

Replace with

🤖 Karsa Crypto

Autonomous crypto trading system.

Status
• Auto Execution
• Risk Managed
• Bybit Testnet

Available Commands

Trading
/scan
/portfolio
/pnl

Risk
/risk
/kill
/resume

System
/status
/activity
/audit

Cleaner and grouped.

---

# STATUS

Current

System:
<pre>

DB...
Redis...

Wallet:
<pre>

Recommended

📊 Karsa Status

System

<pre>
Database      🟢
Redis         🟢
Exchange      🟢 Testnet
API           🟢 Valid
Regime        🟢 Bull Trend
Trading       🟢 Active
</pre>

Wallet

<pre>
Equity      $12,540
Available   $11,822
Margin      $718
uPnL        +$42.17
</pre>

Risk

• Global Halt: No
• Cooldown: No

Footer

Last Update
2026...

More compact.

---

# PORTFOLIO

Current

ASCII table only.

Recommended

💼 Portfolio

Summary

Open Positions : 4

Total uPnL : 🟢 +$145.80

Positions

<pre>

BTCUSDT
LONG
0.025
+$45

ETHUSDT
SHORT
1.00
-$12

SOLUSDT
LONG
25
+$110

</pre>

Much better on phones.

Optional

Per-position card

BTCUSDT
🟢 LONG

Entry
104100

Current
104850

uPnL
+$43

Much more readable.

---

# SCAN RESULT

Old

Ticker
Direction
Confidence

New

🔍 BTCUSDT

Recommendation

🟢 LONG

Confidence

92 / 100

Execution

✅ Executed

Trade

Entry
104250

Stop
103100

Target
107800

Reason

Momentum breakout
High volume
Strong market regime

Much easier.

---

# FULL MARKET SCAN

Current

Long list.

Instead

🔍 Scan Complete

Signals

🟢 BTC 92
✅ Executed

🟡 ETH 74
Rejected

🔴 XRP 41
Ignored

Summary

Signals
18

Executed
4

Rejected
11

Watchlist
3

---

# RISK

Recommended

🛡 Risk Dashboard

Limits

<pre>

Risk/Trade      1%
Max Position    15%
Concurrent      5
Loss Limit      5%

</pre>

Current

<pre>

Open            3/5
Margin          12%
Cooldown        No

</pre>

Simple.

---

# PNL

📈 Performance

Summary

Realized
+$214

Unrealized
+$41

Open Trades
3

Closed Trades
54

Win Rate

63%

Profit Factor

1.82

Max Drawdown

4.1%

Much more professional.

---

# ACTIVITY

Instead of giant paragraphs

📋 Recent Activity

22:14

BTC

🟢 LONG

Executed

22:11

ETH

Rejected

Risk Filter

22:07

SOL

Pending

Cleaner timeline.

---

# AUDIT

Recommended

🔍 Weekly Audit

Overall

Grade

A-

Performance

<pre>

Trades      43
Win Rate    61%
PnL         +$821

</pre>

Strengths

✅ Strong BTC accuracy

✅ Good trend following

Weaknesses

⚠ ETH losing

⚠ Low RR

Recommendations

• Reduce ETH exposure

• Increase confidence threshold

• Avoid ranging markets

---

# KILL

Instead of

EMERGENCY KILL

Use

🚨 Trading Halted

Summary

All positions closed.

Trading disabled.

Positions Closed

5

Status

⛔ HALTED

Resume

/resume

---

# SELL ALL

🧹 Positions Closed

Summary

Closed

5 positions

Cooldown

15 minutes

Memory

Reset

Trading

Paused

---

# RESUME

▶️ Trading Resumed

Status

🟢 Active

Global Halt

Disabled

Cooldown

Removed

Ready for new signals.

---

# Error Style

Instead of

❌ Scan failed.

Use

❌ Scan Failed

Reason

Unable to reach Bybit API.

Suggestion

Retry in a few seconds.

---

# Empty State

Instead of

No positions

Use

📭 Portfolio Empty

No active positions.

Run

/scan BTC

to evaluate a new opportunity.

---

# Success Style

Always

✅ Action Completed

instead of

Done.

---

# Navigation Buttons

Always bottom.

Example

[📊 PnL]
[💼 Portfolio]

[🛡 Risk]
[📋 Activity]

Never place more than 4 buttons.

---

# Footer

────────────

Karsa Crypto

Auto Trading Engine

2026-06-30 22:11 WIB

---

# Visual Rules

Maximum 3 hierarchy levels.

Avoid more than:

2 consecutive bold sections

Never use

❌❌❌

or

🔥🔥🔥🔥🔥

One emoji per line maximum.

Use whitespace generously.

Avoid paragraphs longer than 4 lines.

Prefer bullets over prose.

---

# Implementation Mapping

Current Helper

bold()

→ Titles

italic()

→ Footer / timestamps / notes

code()

→ Commands / grades

pre()

→ Tables / metrics

fmt()

→ Entire message composition

join()

→ Lists

---

# Future Components

Create reusable builders:

StatusCard()

MetricCard()

TradeCard()

PositionCard()

RiskCard()

AuditCard()

TimelineItem()

Section()

Footer()

Each command should compose these components instead of manually concatenating strings.

This keeps every Telegram message visually consistent, easier to maintain, and much closer to a professional trading terminal experience.