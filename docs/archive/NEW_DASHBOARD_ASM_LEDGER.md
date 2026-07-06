# 📊 Karsa ASM: Grafana Trading Ledger Dashboard

## Overview

This Grafana dashboard serves as the **Ultimate Trading Ledger & Operational Monitor** for the Karsa Autonomous Session Manager (ASM). It provides a comprehensive "Bloomberg Terminal" view where you can monitor every financial and operational detail of the ASM in real-time.

---

## 🖥️ Dashboard Layout

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  📊 Karsa ASM: Trading Ledger              [ Last 24 Hours ▾ ]  [ Session: All ▾ ]   ⟳ │
─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                         │
│  ┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐ ┌────────┐ │
│  │ 🤖 ASM STATUS        │ │ 💰 CASH & EQUITY     │ │ 💵 PNL BREAKDOWN     │ │ 🎯 STATS│ │
│  │                      │ │                      │ │                      │ │        │ │
│  │ 🟢 RUNNING           │ │ Total Equity:        │ │ Total PnL:           │ │ Win %: │ │
│  │ Uptime: 14h 22m      │ │ $55,100.00           │ │ 🟢 +$5,100.00        │ │  68%   │ │
│  │ Profile: BALANCED    │ │                      │ │                      │ │        │ │
│  │ Next Scan: 02m 14s   │ │ Available: $45,000   │ │ Realized:            │ │ P. Fac:│ │
│  │                      │ │ Margin Used: $10,100 │ │ 🟢 +$4,200.00        │ │  2.14  │ │
│  │                      │ │                      │ │ Unrealized:          │ │        │ │
│  │                      │ │                      │ │ 🟢 +$900.00          │ │ Trades:│ │
│  │                      │ │                      │ │                      │ │   42   │ │
│  └──────────────────────┘ └──────────────────────┘ └──────────────────────┘ └────────┘ │
│                                                                                         │
│  ┌─────────────────────────────────────────────┐ ┌───────────────────────────────────┐  │
│  │ 📈 EQUITY & CASH CURVE                      │ │ 📊 DAILY NET PNL                  │  │
│  │                                             │ │                                   │  │
│  │  $55k ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐  │ │  +$1k │      █                  │  │
│  │       ╱╲      ╱╲   ╱╲                   │  │ │       │      █                  │  │
│  │  $52k ──╱──╲──╱──╲─╱──╲──              │  │ │    $0 ├──────█──────────         │  │
│  │     ╱─╯    ╲╱    ╲╯    ╲─              │  │ │       │  ▒   █   ▒              │  │
│  │  $50k ─╱───────────────────────        │  │ │  -$500│  ▒                     │  │
│  │     ╱                                  │  │ │       └──┴──┴──┴──┴──┴──┴──       │  │
│  │  $48k ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘  │ │         Mon Tue Wed Thu Fri Sat Sun │  │
│  │  ─── Total Equity ─── Available Cash     │ │                                   │  │
│  └─────────────────────────────────────────┘ └───────────────────────────────────┘  │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐  │
│  │ 💼 OPEN POSITIONS (Live)                                                          │  │
│  │                                                                                   │  │
│  │ Symbol    │ Side  │ Size  │ Entry Price │ Mark Price │ uPnL ($) │ uPnL (%) │ SL/TP │  │
│  │───────────┼───────┼───────┼─────────────┼────────────┼──────────┼──────────┼───────│  │
│  │ SOLUSDT   │ LONG  │ 10.0  │ $140.20     │ $148.50    │ 🟢+$830  │ +5.92%   │ 135/155│  │
│  │ AVAXUSDT  │ SHORT │ 25.0  │ $38.20      │ $36.80     │ 🟢+$350  │ +3.66%   │ 40/34 │  │
│  │ BTCUSDT   │ LONG  │ 0.1   │ $62,450     │ $62,100    │ 🔴-$35   │ -0.56%   │ 60k/65k│  │
│  └───────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐  │
│  │ 📜 TRADE HISTORY (Recent Closed)                                                  │  │
│  │                                                                                   │  │
│  │ Result │ Symbol    │ Side  │ Entry     │ Exit      │ Realized PnL │ Duration │ Exit │  │
│  │────────┼───────────┼───────┼───────────┼───────────┼──────────────┼────────────────│  │
│  │ 🟢 WIN │ ETHUSDT   │ LONG  │ $3,100.00 │ $3,185.00 │ +$425.00     │ 14h 20m  │ TP   │  │
│  │ 🔴 LOSS│ DOGEUSDT  │ SHORT │ $0.1420   │ $0.1450   │ -$150.00     │ 02h 15m  │ SL   │  │
│  │ 🟢 WIN │ LINKUSDT  │ LONG  │ $14.50    │ $15.10    │ +$300.00     │ 08h 45m  │ Trail│  │
│  │ 🟢 WIN │ MATICUSDT │ LONG  │ $0.850    │ $0.855    │ +$50.00      │ 00h 30m  │ Man  │  │
│  └───────────────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 📋 Panel Specifications

### Row 1: The "Heads-Up" Display (Stat Panels)

#### 1. 🤖 ASM STATUS
**Visualization:** Stat Panel  
**Purpose:** Display current ASM operational state

**Metrics:**
- **Status:** 🟢 RUNNING / 🟡 PAUSED / 🔴 IDLE
- **Uptime:** Session duration (e.g., `14h 22m`)
- **Profile:** Active risk profile (e.g., `BALANCED`)
- **Next Scan:** Countdown to next market scan (e.g., `02m 14s`)

**Grafana Configuration:**
- Use **Stat panel** visualization
- Enable **Color mode**: `Value` (Green/Yellow/Red based on status)
- Add multiple fields in one panel using "All values" display mode
- Data source: ASM heartbeat metrics (`asm_status`, `asm_uptime_seconds`, `asm_profile`)

---

#### 2. 💰 CASH & EQUITY
**Visualization:** Stat Panel with Sparkline  
**Purpose:** Real-time capital allocation overview

**Metrics:**
- **Total Equity:** Current account balance (e.g., `$55,100.00`)
- **Available Cash:** Unallocated capital (e.g., `$45,000`)
- **Margin Used:** Capital locked in positions (e.g., `$10,100`)

**Grafana Configuration:**
- Use **Stat panel** visualization
- Enable **Sparkline** for equity trend
- Set **Unit**: Currency (USD)
- Set **Decimals**: 2
- Data source: Exchange wallet API (`total_equity`, `available_balance`, `used_margin`)

---

#### 3. 💵 PNL BREAKDOWN
**Visualization:** Stat Panel (Number Display)  
**Purpose:** Clean, bold PnL numbers (Realized vs Unrealized)

**Metrics:**
- **Total PnL:** Combined realized + unrealized (e.g., `🟢 +$5,100.00`)
- **Realized PnL:** Closed trades profit/loss (e.g., `🟢 +$4,200.00`)
- **Unrealized PnL:** Open positions floating PnL (e.g., `🟢 +$900.00`)

**Grafana Configuration:**
- Use **Stat panel** visualization
- Set **Color mode**: `Value` (Green if > 0, Red if < 0)
- Set **Unit**: Currency (USD)
- Create 3 separate Stat panels or group in one panel
- Data source: Calculated metric (`total_pnl = realized_pnl + unrealized_pnl`)

---

#### 4. 🎯 STATS
**Visualization:** Stat Panel & Gauge  
**Purpose:** Key performance indicators

**Metrics:**
- **Win Rate:** Percentage of winning trades (e.g., `68%`)
- **Profit Factor:** Gross Profit / Gross Loss (e.g., `2.14`)
- **Total Trades:** Number of executed trades (e.g., `42`)

**Grafana Configuration:**
- **Win Rate**: Use **Gauge panel** (0-100% range)
- **Profit Factor & Trades**: Use **Stat panel**
- Set **Color thresholds**: Win Rate > 60% = Green, < 40% = Red
- Data source: Trade database aggregations (`win_rate_percentage`, `profit_factor`, `total_trades`)

---

### Row 2: Financial Time-Series

#### 5. 📈 EQUITY & CASH CURVE
**Visualization:** Time Series Panel (Dual Line)  
**Purpose:** Track capital deployment over time

**Lines:**
- **Line 1 (Solid, Blue):** Total Equity over time
- **Line 2 (Dashed, Gray):** Available Cash over time

**Why This Matters:**  
Watching the gap between Equity and Available Cash tells you exactly how much capital is deployed at any given second.

**Grafana Configuration:**
- Use **Time series** visualization
- Enable **Line mode**: Line
- Set **Fill opacity**: 10%
- Add **Thresholds**: Drawdown limit (e.g., -5%)
- Data source: Time-series database (`equity_curve`, `available_cash`)

---

#### 6. 📊 DAILY NET PNL
**Visualization:** Bar Chart  
**Purpose:** Calendar view of daily performance

**Bars:**
- **Solid bars (Green/Red):** Daily net realized PnL
- **Height:** Magnitude of profit/loss

**Why This Matters:**  
Provides a classic "calendar view" of which days the bot made money and which days it lost money, showing daily consistency.

**Grafana Configuration:**
- Use **Bar chart** visualization
- Set **Group by**: `time(1d)`
- Set **Bar width**: 0.8
- Set **Color scheme**: Green for positive, Red for negative
- Data source: Aggregated daily PnL (`SELECT sum(realized_pnl) GROUP BY time(1d)`)

---

### Row 3: The "Tape" (Live Data Tables)

#### 7. 💼 OPEN POSITIONS
**Visualization:** Table Panel  
**Purpose:** Real-time view of active positions

**Columns:**
| Column | Description | Formatting |
|--------|-------------|------------|
| Symbol | Trading pair (e.g., `SOLUSDT`) | Text |
| Side | LONG/SHORT | Icon + Text |
| Size | Position size | Number |
| Entry Price | Average entry price | Currency |
| Mark Price | Current market price | Currency |
| uPnL ($) | Unrealized PnL in USD | Green/Red |
| uPnL (%) | Unrealized PnL percentage | Green/Red |
| SL/TP | Stop Loss / Take Profit levels | Price |

**Grafana Configuration:**
- Use **Table** visualization
- Enable **Auto-refresh**: Every 10 seconds
- Set **Sorting**: Default sort by `uPnL (%)` descending
- Enable **Cell display modes**: 
  - `uPnL ($)` and `uPnL (%)`: Color background (Green/Red)
  - `Side`: Add icons (⬆️ LONG, ⬇️ SHORT)
- Data source: Live positions API (`open_positions`)

---

#### 8. 📜 TRADE HISTORY
**Visualization:** Table Panel  
**Purpose:** Log of closed trades with Win/Loss indicator

**Columns:**
| Column | Description | Formatting |
|--------|-------------|------------|
| **Result** | 🟢 WIN / 🔴 LOSS | **Color-coded badge** |
| Symbol | Trading pair | Text |
| Side | LONG/SHORT | Icon + Text |
| Entry | Entry price | Currency |
| Exit | Exit price | Currency |
| Realized PnL | Final profit/loss | Green/Red |
| Duration | Trade duration (e.g., `14h 20m`) | Time |
| Exit | Exit reason (TP/SL/Trail/Man) | Abbreviated |

**Grafana Configuration:**
- Use **Table** visualization
- **Result Column Setup:**
  - Add calculated field: `CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END as is_win`
  - Use **Value mappings**:
    - `1` → `🟢 WIN` (Green background, white text)
    - `0` → `🔴 LOSS` (Red background, white text)
- **Exit Reason Column:**
  - Use **Value mappings**:
    - `take_profit` → `TP`
    - `stop_loss` → `SL`
    - `trailing_stop` → `Trail`
    - `manual` → `Man`
- Set **Sorting**: Default sort by `timestamp` descending (newest first)
- Data source: Trade history database (`trade_history`)

---

## ⚙️ Data Architecture

### Database Requirements

To power this Grafana dashboard, your backend must push data to a time-series database. Recommended options:
- **InfluxDB** (Best for high-frequency metrics)
- **TimescaleDB** (PostgreSQL-based, good for relational + time-series)
- **Prometheus** (Good for infrastructure metrics)

### Data Push Frequency

#### High-Frequency Metrics (Every 5-10 seconds)
Push these metrics continuously:
```python
{
    "measurement": "asm_wallet",
    "fields": {
        "total_equity": 55100.00,
        "available_balance": 45000.00,
        "used_margin": 10100.00,
        "unrealized_pnl": 900.00
    },
    "tags": {
        "session_id": "A8F3",
        "profile": "BALANCED"
    }
}
```

#### Event-Driven Metrics (On Trade Close)
Push when a trade closes:
```python
{
    "measurement": "trade_closed",
    "fields": {
        "realized_pnl": 425.00,
        "duration_seconds": 51600,
        "entry_price": 3100.00,
        "exit_price": 3185.00,
        "is_win": 1  # 1 for win, 0 for loss
    },
    "tags": {
        "symbol": "ETHUSDT",
        "side": "LONG",
        "exit_reason": "take_profit",
        "session_id": "A8F3"
    }
}
```

#### ASM State Metrics (Every 1 minute)
Push ASM operational status:
```python
{
    "measurement": "asm_status",
    "fields": {
        "status_code": 1,  # 1=running, 0=idle, -1=paused
        "uptime_seconds": 51720,
        "next_scan_seconds": 134
    },
    "tags": {
        "profile": "BALANCED",
        "session_id": "A8F3"
    }
}
```

---

## 🔧 Grafana Setup Instructions

### Step 1: Create Data Source
1. Go to **Configuration** → **Data sources**
2. Click **Add data source**
3. Select your database type (InfluxDB/TimescaleDB/Prometheus)
4. Configure connection details:
   - URL: `http://your-database:port`
   - Database: `karsa_asm`
   - Authentication: As required

### Step 2: Create Dashboard
1. Go to **Dashboards** → **New dashboard**
2. Click **Add visualization**
3. Select your data source
4. Build each panel according to specifications above

### Step 3: Configure Refresh Rate
1. Click the **gear icon** (Dashboard settings)
2. Set **Time refresh**: `10s` (for live updates)
3. Set **Auto-refresh**: `On`

### Step 4: Set Up Variables (Optional)
Create dashboard variables for dynamic filtering:
- **Session ID**: `SELECT DISTINCT(session_id) FROM trades`
- **Symbol**: `SELECT DISTINCT(symbol) FROM trades`
- **Time Range**: Use Grafana's built-in time picker

### Step 5: Configure Alerts (Optional)
Set up Grafana alerts for critical conditions:
1. **Drawdown Alert**: Trigger if equity drops below threshold
2. **API Error Alert**: Trigger if error rate spikes
3. **Session Stop Alert**: Trigger if ASM status changes to STOPPED

**Alert Notification:**
- Configure webhook to send alerts to your Telegram bot
- Bot will forward critical alerts to your phone

---

## 🎨 Visual Design Guidelines

### Color Scheme
| Element | Color | Hex Code |
|---------|-------|----------|
| **Background** | Dark | `#1E1E2E` |
| **Positive PnL** | Green | `#00E396` |
| **Negative PnL** | Red | `#FF4560` |
| **Neutral/Info** | Blue | `#008FFB` |
| **Warning** | Yellow | `#FEB019` |

### Typography
- **Panel Titles**: 14px, Bold, White
- **Metric Values**: 18-24px, Semi-Bold
- **Table Text**: 12px, Regular
- **Labels**: 11px, Light Gray

### Thresholds (Color Coding)
| Metric | Green | Yellow | Red |
|--------|-------|--------|-----|
| **Win Rate** | > 60% | 40-60% | < 40% |
| **Profit Factor** | > 2.0 | 1.0-2.0 | < 1.0 |
| **Drawdown** | < -2% | -2% to -5% | > -5% |
| **Total PnL** | > $0 | $0 | < $0 |

---

## 📱 Mobile Responsiveness

Grafana dashboards are primarily designed for desktop/tablet viewing. For mobile access:

1. **Use Grafana Mobile App**: Available for iOS and Android
2. **Enable Touch Gestures**: Pinch to zoom, swipe to pan
3. **Simplified View**: Create a separate "Mobile" dashboard with only key metrics:
   - ASM Status
   - Total PnL
   - Win Rate
   - Open Positions count

---

## ✅ Checklist: Before Going Live

- [ ] Data source connected and tested
- [ ] All 8 panels created and configured
- [ ] Auto-refresh set to 10s
- [ ] Color thresholds configured
- [ ] Table sorting enabled
- [ ] Value mappings for Win/Loss configured
- [ ] Data push frequency verified

---

## 🎯 Summary

This Grafana dashboard provides:

✅ **Real-time financial monitoring** (Equity, Cash, PnL)  
✅ **Clear Win/Loss tracking** in trade history  
✅ **Live position management** with full details  
✅ **Performance analytics** (Win Rate, Profit Factor)  
✅ **Visual capital deployment** tracking  
✅ **Daily performance calendar** view  
✅ **Seamless Telegram integration**  

**Telegram Bot** = Command Center (Actions & Alerts)  
**Grafana Dashboard** = Trading Ledger (Analytics & History)  

Together, they form a complete, professional-grade autonomous trading system.