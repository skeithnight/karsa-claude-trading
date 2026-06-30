# KARSA IDX Intelligence — Design & Technical Specification

> **Codename:** Zeta-Parity  
> **Target Repo:** `karsa-claude-trading`  
> **Author:** Dwiki Nugraha  
> **Version:** 1.0.0  
> **Status:** Design Draft

---

## 0. Executive Summary

This document specifies the design and technical implementation to bring `karsa-claude-trading` to Zeta AI IDX parity — adding a full **IHSG Screener**, **Technical Indicator Engine**, and **Bandarmologi (Smart Money)** module, while preserving karsa's existing strengths: Claude AI reasoning, multi-market scope (IDX + US + ETF), Guard Pipeline risk management, and Human-in-the-Loop (HITL) approval before order execution.

```
Gap Before:  watchlist-limited | no TA engine | no bandarmologi
Goal After:  800+ IDX scan    | deterministic TA | broker flow + foreign net
```

---

## 1. Architecture Overview

### 1.1 High-Level Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│                    KARSA IDX INTELLIGENCE                    │
│                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │    IHSG      │    │  TECHNICAL   │    │ BANDARMOLOGI │  │
│  │  SCREENER    │───▶│  INDICATOR   │───▶│   MODULE     │  │
│  │  (800+ IDX)  │    │   ENGINE     │    │ (smart money)│  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│          │                  │                   │           │
│          └──────────────────┴───────────────────┘          │
│                             │                               │
│                    ┌────────▼────────┐                      │
│                    │  SIGNAL FUSION  │                      │
│                    │  (Unified Score)│                      │
│                    └────────┬────────┘                      │
│                             │                               │
│              ┌──────────────▼──────────────┐               │
│              │     CLAUDE AI REASONING      │               │
│              │  (existing: LLM analyst)     │               │
│              └──────────────┬──────────────┘               │
│                             │                               │
│         ┌───────────────────▼───────────────────┐          │
│         │         GUARD PIPELINE (risk gate)     │          │
│         └───────────────────┬───────────────────┘          │
│                             │                               │
│         ┌───────────────────▼───────────────────┐          │
│         │    TELEGRAM SIGNAL (fmt.py output)     │          │
│         │    BUY/HOLD/IGNORE + Entry/TP/SL       │          │
│         └───────────────────┬───────────────────┘          │
│                             │                               │
│              ┌──────────────▼──────────────┐               │
│              │    HUMAN-IN-THE-LOOP (HITL)  │              │
│              │    ✅ Approve → Order Exec   │              │
│              │    ❌ Reject  → Archive      │              │
│              └─────────────────────────────┘               │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 Directory Structure (new modules)

```
karsa-claude-trading/
├── src/
│   ├── screener/                    # NEW
│   │   ├── __init__.py
│   │   ├── ihsg_screener.py         # Main screener (800+ stocks)
│   │   ├── universe.py              # IDX ticker universe management
│   │   ├── volume_filter.py         # Volume spike detection
│   │   └── pattern_filter.py        # Price pattern pre-filter
│   │
│   ├── indicators/                  # NEW
│   │   ├── __init__.py
│   │   ├── engine.py                # Indicator computation orchestrator
│   │   ├── momentum.py              # RSI, MACD, ADX
│   │   ├── trend.py                 # EMA (9, 21, 50, 200)
│   │   ├── volatility.py            # ATR, Bollinger Bands
│   │   ├── fibonacci.py             # Fib retracement levels
│   │   └── scorer.py                # TA composite score (0-100)
│   │
│   ├── bandarmologi/                # NEW
│   │   ├── __init__.py
│   │   ├── broker_flow.py           # IDX broker net flow scraper
│   │   ├── foreign_flow.py          # IDX foreign net buy/sell
│   │   ├── accumulation.py          # Accumulation/distribution detector
│   │   └── bandar_scorer.py         # Smart money composite score
│   │
│   ├── signal/                      # ENHANCED (existing fmt.py)
│   │   ├── __init__.py
│   │   ├── fusion.py                # Combine screener + TA + bandar scores
│   │   ├── formatter.py             # Telegram message builder (from fmt.py)
│   │   └── decision.py              # BUY/HOLD/IGNORE logic
│   │
│   ├── scheduler/                   # ENHANCED (existing APScheduler)
│   │   ├── __init__.py
│   │   └── market_cron.py           # 3x daily jobs (08:55 / 11:30 / 15:45 WIB)
│   │
│   └── data/                        # NEW
│       ├── __init__.py
│       ├── provider.py              # Data source abstraction
│       ├── idx_client.py            # IDX / BEI data integration
│       └── cache.py                 # Redis cache layer
│
├── config/
│   ├── universe_idx.json            # Full IDX ticker list
│   ├── screener_config.yaml         # Screener thresholds
│   └── indicator_config.yaml        # TA parameter tuning
│
└── tests/
    ├── test_screener.py
    ├── test_indicators.py
    └── test_bandarmologi.py
```

---

## 2. Module 1: IHSG Screener

### 2.1 Purpose

Scan all 800+ IDX-listed stocks daily to produce a shortlist of candidates worth further analysis. Acts as the top-of-funnel filter before expensive TA computation and Bandarmologi scraping.

### 2.2 Design

```python
# src/screener/ihsg_screener.py

from dataclasses import dataclass
from typing import List, Optional
import pandas as pd

@dataclass
class ScreenerCandidate:
    ticker: str
    close: float
    volume: int
    volume_ratio: float          # today_vol / 20d_avg_vol
    price_change_pct: float
    pattern_tag: Optional[str]   # e.g. "breakout", "reversal", "squeeze"
    screen_score: float          # 0-100

class IHSGScreener:
    """
    Entry point: scan full IDX universe.
    Returns ranked shortlist (default top 30).
    """
    def __init__(self, config: dict, data_provider):
        self.config = config
        self.provider = data_provider
        self.universe = self._load_universe()

    def run(self, top_n: int = 30) -> List[ScreenerCandidate]:
        candidates = []
        for ticker in self.universe:
            df = self.provider.get_ohlcv(ticker, period="60d")
            if df is None or len(df) < 20:
                continue
            score = self._score(ticker, df)
            if score > self.config["min_screen_score"]:  # default: 40
                candidates.append(score)
        return sorted(candidates, key=lambda x: x.screen_score, reverse=True)[:top_n]

    def _score(self, ticker: str, df: pd.DataFrame) -> ScreenerCandidate:
        vol_ratio = self._volume_ratio(df)
        price_chg = self._price_change(df)
        pattern   = self._detect_pattern(df)
        score     = self._compute_score(vol_ratio, price_chg, pattern)
        return ScreenerCandidate(
            ticker=ticker,
            close=df["close"].iloc[-1],
            volume=int(df["volume"].iloc[-1]),
            volume_ratio=vol_ratio,
            price_change_pct=price_chg,
            pattern_tag=pattern,
            screen_score=score
        )
```

### 2.3 Screening Criteria & Weights

| Criteria | Formula | Weight |
|---|---|---|
| **Volume Spike** | `today_vol / avg_vol_20d > 2.0` | 35% |
| **Price Momentum** | `abs(price_change_1d) > 1.5%` | 25% |
| **Trend Alignment** | `close > EMA_50` | 20% |
| **Pattern Match** | breakout / reversal / squeeze | 20% |

### 2.4 Pattern Tags

```python
PATTERNS = {
    "breakout":   "close crosses above resistance (20d high)",
    "reversal":   "hammer/doji at support + volume surge",
    "squeeze":    "Bollinger Band width < 5% (compression)",
    "golden_x":  "EMA9 crosses above EMA21",
    "dead_x":    "EMA9 crosses below EMA21 (short candidate)",
}
```

### 2.5 Universe Management

```python
# src/screener/universe.py
# IDX universe: 3 tiers

TIER_1 = ["BBCA", "BBRI", "TLKM", "ASII", "BMRI", ...]   # LQ45 (liquid)
TIER_2 = ["GOTO", "TPIA", "ANTM", "BBTN", ...]            # IDX80 extended
TIER_3 = ["CUAN", "DMAS", ...]                             # Watchlist personal

# Total: ~820 stocks from BEI listing
# Source: idx.co.id/id/data-pasar/data-saham/daftar-saham
```

---

## 3. Module 2: Technical Indicator Engine

### 3.1 Purpose

Deterministic, rule-based TA computation on screener shortlist. NOT LLM-based. Fast, reproducible, auditable. Claude AI reasoning layer sits on top of these computed values.

### 3.2 Indicator Stack

```python
# src/indicators/engine.py

import pandas_ta as ta
import pandas as pd
from dataclasses import dataclass

@dataclass
class TAResult:
    ticker: str
    # Momentum
    rsi_14: float
    macd_line: float
    macd_signal: float
    macd_hist: float
    adx_14: float
    # Trend
    ema_9: float
    ema_21: float
    ema_50: float
    ema_200: float
    trend_direction: str        # "UP" | "DOWN" | "SIDEWAYS"
    # Volatility
    atr_14: float
    atr_pct: float              # ATR / close * 100
    bb_upper: float
    bb_lower: float
    bb_width_pct: float
    # Fibonacci
    fib_levels: dict            # {"0.236": x, "0.382": x, "0.618": x}
    nearest_fib_support: float
    nearest_fib_resistance: float
    # Score
    ta_score: float             # 0-100 composite
    signal_tag: str             # "STRONG_BUY" | "BUY" | "NEUTRAL" | "SELL"

class IndicatorEngine:
    def compute(self, ticker: str, df: pd.DataFrame) -> TAResult:
        df.ta.rsi(length=14, append=True)
        df.ta.macd(append=True)
        df.ta.adx(length=14, append=True)
        df.ta.ema(length=9, append=True)
        df.ta.ema(length=21, append=True)
        df.ta.ema(length=50, append=True)
        df.ta.ema(length=200, append=True)
        df.ta.atr(length=14, append=True)
        df.ta.bbands(length=20, append=True)
        fib = self._compute_fibonacci(df)
        score, tag = self._compute_score(df, fib)
        return TAResult(...)
```

### 3.3 Fibonacci Implementation

```python
# src/indicators/fibonacci.py

def compute_fibonacci(df: pd.DataFrame, lookback: int = 60) -> dict:
    """
    Swing high/low based Fibonacci retracement.
    Lookback: 60 candles (3 bulan trading).
    """
    high = df["high"].tail(lookback).max()
    low  = df["low"].tail(lookback).min()
    diff = high - low

    levels = {
        "0.000": low,
        "0.236": low + 0.236 * diff,
        "0.382": low + 0.382 * diff,
        "0.500": low + 0.500 * diff,
        "0.618": low + 0.618 * diff,
        "0.786": low + 0.786 * diff,
        "1.000": high,
    }

    close = df["close"].iloc[-1]
    # Find nearest support (fib level below close)
    support    = max([v for v in levels.values() if v < close], default=low)
    resistance = min([v for v in levels.values() if v > close], default=high)

    return {
        "levels": levels,
        "nearest_support": support,
        "nearest_resistance": resistance,
        "risk_reward_ratio": (resistance - close) / (close - support)
    }
```

### 3.4 TA Composite Scoring

```python
# src/indicators/scorer.py

def compute_ta_score(df, fib_result) -> tuple[float, str]:
    """Returns (score: 0-100, tag: str)"""
    score = 0

    # RSI (0-25 pts)
    rsi = df["RSI_14"].iloc[-1]
    if 40 <= rsi <= 60:  score += 10   # neutral range
    if rsi < 35:         score += 20   # oversold (buy setup)
    if rsi > 70:         score -= 10   # overbought (risk)

    # MACD (0-20 pts)
    hist = df["MACDh_12_26_9"].iloc[-1]
    prev = df["MACDh_12_26_9"].iloc[-2]
    if hist > 0 and hist > prev: score += 20   # bullish momentum rising
    if hist > 0 and hist < prev: score += 10   # bullish but weakening

    # EMA Alignment (0-25 pts)
    close  = df["close"].iloc[-1]
    ema9   = df["EMA_9"].iloc[-1]
    ema21  = df["EMA_21"].iloc[-1]
    ema50  = df["EMA_50"].iloc[-1]
    if close > ema9 > ema21 > ema50: score += 25  # perfect bullish stack
    elif close > ema21 > ema50:      score += 15
    elif close < ema9 < ema21:       score -= 15  # bearish stack

    # ADX (0-15 pts) — trend strength
    adx = df["ADX_14"].iloc[-1]
    if adx > 25: score += 15   # strong trend
    if adx < 15: score -= 5    # weak/choppy

    # Fibonacci RR (0-15 pts)
    rr = fib_result["risk_reward_ratio"]
    if rr >= 2.0: score += 15
    elif rr >= 1.5: score += 10

    score = max(0, min(100, score))

    tag_map = {
        (75, 100): "STRONG_BUY",
        (55, 74):  "BUY",
        (35, 54):  "NEUTRAL",
        (0, 34):   "SELL",
    }
    tag = next(v for (lo, hi), v in tag_map.items() if lo <= score <= hi)
    return score, tag
```

### 3.5 Entry / TP / SL Computation

```python
def compute_entry_tp_sl(df, atr_14, fib_result, signal_tag) -> dict:
    """
    ATR-based dynamic SL, Fibonacci-based TP.
    """
    close = df["close"].iloc[-1]
    atr   = atr_14

    entry = close                                   # market order at current
    sl    = close - (1.5 * atr)                    # 1.5x ATR below entry
    tp1   = fib_result["nearest_resistance"]        # nearest fib resistance
    tp2   = tp1 + (0.618 * (tp1 - entry))          # extended TP

    risk        = entry - sl
    reward_tp1  = tp1 - entry
    rr_ratio    = reward_tp1 / risk if risk > 0 else 0

    return {
        "entry": round(entry, 0),
        "sl":    round(sl, 0),
        "tp1":   round(tp1, 0),
        "tp2":   round(tp2, 0),
        "rr_ratio": round(rr_ratio, 2),
        "sl_pct": round((entry - sl) / entry * 100, 2),
        "tp1_pct": round((tp1 - entry) / entry * 100, 2),
    }
```

---

## 4. Module 3: Bandarmologi

### 4.1 Purpose

Detect institutional / "bandar" accumulation or distribution by tracking:
1. **Broker net flow per ticker** (from IDX broker summary)
2. **Net foreign buy/sell** (from BEI daily report)

This is the differentiating layer that Zeta AI IDX uses and karsa currently lacks.

### 4.2 Data Sources

| Source | URL | Data | Update |
|---|---|---|---|
| **IDX Broker Summary** | `idx.co.id/id/data-pasar/ringkasan-perdagangan/ringkasan-broker` | Net buy/sell per broker code per saham | Daily, T+0 after close |
| **IDX Foreign Flow** | `idx.co.id/id/data-pasar/ringkasan-perdagangan/transaksi-investor-asing` | Net asing (buy - sell) aggregate | Daily |
| **IPOT API** (if subscribed) | `indopremier.com` | Richer broker flow data | Real-time |
| **RTI Business** (alt) | `rtiindonesia.com` | Broker data enriched | Daily |

### 4.3 Broker Flow Scraper

```python
# src/bandarmologi/broker_flow.py

import httpx
from bs4 import BeautifulSoup
import pandas as pd
from dataclasses import dataclass
from typing import List

SUSPICIOUS_BROKERS = {
    "YP": "Indo Premier (ritel besar)",
    "AK": "UBS (asing institusional)",
    "CC": "Mandiri Sekuritas",
    "ZP": "Kim Eng (asing)",
    "DH": "Daewoo (asing Korea)",
    "BK": "JP Morgan",
    "ML": "Merrill Lynch",
    "RX": "Macquarie",
}

@dataclass
class BrokerFlowData:
    ticker: str
    date: str
    top_buyer_broker: str
    top_buyer_net: float        # in billion IDR
    top_seller_broker: str
    top_seller_net: float
    institutional_net: float    # sum of known institutional brokers net
    retail_net: float
    flow_direction: str         # "ACCUMULATE" | "DISTRIBUTE" | "NEUTRAL"

class BrokerFlowScraper:
    BASE_URL = "https://www.idx.co.id"

    async def fetch_broker_summary(self, ticker: str, date: str) -> BrokerFlowData:
        """
        Fetch IDX broker summary for a ticker on given date.
        Parses the table: broker code, vol_buy, val_buy, vol_sell, val_sell, net_val
        """
        url = f"{self.BASE_URL}/id/data-pasar/ringkasan-perdagangan/ringkasan-broker"
        params = {"stock": ticker, "date": date}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table", {"id": "broker-summary-table"})
            df = pd.read_html(str(table))[0]

        return self._parse_broker_flow(ticker, date, df)

    def _parse_broker_flow(self, ticker, date, df) -> BrokerFlowData:
        df["net_val"] = df["val_buy"] - df["val_sell"]
        df_sorted = df.sort_values("net_val", ascending=False)

        institutional_brokers = list(SUSPICIOUS_BROKERS.keys())
        inst_net = df[df["broker"].isin(institutional_brokers)]["net_val"].sum()
        retail_net = df[~df["broker"].isin(institutional_brokers)]["net_val"].sum()

        direction = (
            "ACCUMULATE" if inst_net > 0 and inst_net > abs(retail_net) * 0.5
            else "DISTRIBUTE" if inst_net < 0
            else "NEUTRAL"
        )

        return BrokerFlowData(
            ticker=ticker,
            date=date,
            top_buyer_broker=df_sorted.iloc[0]["broker"],
            top_buyer_net=df_sorted.iloc[0]["net_val"],
            top_seller_broker=df_sorted.iloc[-1]["broker"],
            top_seller_net=df_sorted.iloc[-1]["net_val"],
            institutional_net=inst_net,
            retail_net=retail_net,
            flow_direction=direction
        )
```

### 4.4 Foreign Net Flow

```python
# src/bandarmologi/foreign_flow.py

@dataclass
class ForeignFlowData:
    ticker: str
    date: str
    foreign_buy: float       # in shares
    foreign_sell: float
    net_foreign: float       # buy - sell
    net_foreign_val: float   # in billion IDR
    consecutive_buy_days: int
    consecutive_sell_days: int
    foreign_signal: str      # "STRONG_IN" | "IN" | "NEUTRAL" | "OUT" | "STRONG_OUT"

class ForeignFlowTracker:
    def compute_foreign_signal(self, net_val: float, consecutive: int) -> str:
        if net_val > 5 and consecutive >= 3:   return "STRONG_IN"
        if net_val > 0:                         return "IN"
        if net_val < -5 and consecutive >= 3:  return "STRONG_OUT"
        if net_val < 0:                         return "OUT"
        return "NEUTRAL"
```

### 4.5 Bandar Score

```python
# src/bandarmologi/bandar_scorer.py

def compute_bandar_score(broker_flow: BrokerFlowData,
                         foreign_flow: ForeignFlowData,
                         ta_result: TAResult) -> tuple[float, str]:
    """
    Returns (bandar_score: 0-100, interpretation: str)
    """
    score = 50  # baseline neutral

    # Broker flow direction (0-35 pts)
    broker_bonus = {
        "ACCUMULATE": +35,
        "NEUTRAL":    0,
        "DISTRIBUTE": -35,
    }
    score += broker_bonus.get(broker_flow.flow_direction, 0)

    # Foreign flow (0-35 pts)
    foreign_bonus = {
        "STRONG_IN":  +35,
        "IN":         +20,
        "NEUTRAL":    0,
        "OUT":        -20,
        "STRONG_OUT": -35,
    }
    score += foreign_bonus.get(foreign_flow.foreign_signal, 0)

    # Confirmation with TA (0-15 pts)
    if broker_flow.flow_direction == "ACCUMULATE" and ta_result.signal_tag in ("BUY", "STRONG_BUY"):
        score += 15  # bandar accumulate + TA confirms = premium signal

    # Consecutive days bonus
    if foreign_flow.consecutive_buy_days >= 5:
        score += 10
    elif foreign_flow.consecutive_buy_days >= 3:
        score += 5

    score = max(0, min(100, score))

    interpretation = (
        "🔥 BANDAR MASUK KUAT" if score >= 80
        else "✅ AKUMULASI"    if score >= 65
        else "⚠️ DISTRIBUSI"   if score <= 35
        else "🔴 BANDAR KELUAR" if score <= 20
        else "😐 NETRAL"
    )

    return score, interpretation
```

---

## 5. Signal Fusion & Output

### 5.1 Unified Score Fusion

```python
# src/signal/fusion.py

WEIGHTS = {
    "screener": 0.20,    # top-of-funnel signal quality
    "ta":       0.45,    # technical analysis (core)
    "bandar":   0.35,    # smart money flow (differentiator)
}

@dataclass
class FusedSignal:
    ticker: str
    unified_score: float
    decision: str           # "BUY" | "HOLD" | "IGNORE"
    confidence: str         # "HIGH" | "MEDIUM" | "LOW"
    screener_score: float
    ta_score: float
    bandar_score: float
    entry: float
    tp1: float
    tp2: float
    sl: float
    rr_ratio: float
    reasoning: str          # Claude AI narrative (injected after)

def fuse_signal(screener, ta, bandar, price_data) -> FusedSignal:
    unified = (
        screener.screen_score * WEIGHTS["screener"] +
        ta.ta_score            * WEIGHTS["ta"] +
        bandar[0]              * WEIGHTS["bandar"]   # bandar_score
    )

    if unified >= 70:   decision, confidence = "BUY",    "HIGH"
    elif unified >= 55: decision, confidence = "BUY",    "MEDIUM"
    elif unified >= 40: decision, confidence = "HOLD",   "LOW"
    else:               decision, confidence = "IGNORE", "LOW"

    return FusedSignal(unified_score=unified, decision=decision, ...)
```

### 5.2 Telegram Signal Format (fmt.py output)

```
╔══════════════════════════════════╗
║   🔥 KARSA SIGNAL — [TICKER]     ║
║   IDX | [DATE] | [TIME] WIB      ║
╚══════════════════════════════════╝

📊 SKOR GABUNGAN: 82/100 | HIGH CONFIDENCE

┌─ SCREENER ──────── 71/100 ✅
│  Volume ratio: 3.2x | Pattern: breakout
│
├─ TEKNIKAL ──────── 85/100 ✅
│  RSI: 52 | MACD: ↑ bullish hist rising
│  EMA Stack: 9 > 21 > 50 ✅
│  ADX: 28 (trend kuat)
│
└─ BANDAR ─────────── 88/100 🔥
   Broker flow: ACCUMULATE
   Inst. net: +Rp 12.3B
   Foreign: STRONG_IN (4 hari)

─────────────────────────────────
📈 REKOMENDASI: BUY
─────────────────────────────────
💰 Entry  : 1.250
🎯 TP1    : 1.380  (+10.4%)
🎯 TP2    : 1.460  (+16.8%)
🛑 SL     : 1.195  (-4.4%)
📐 R:R    : 2.37

🤖 CLAUDE ANALYSIS:
[Claude AI narrative: konteks makro + katalis +
 alignment dengan IHSG trend hari ini]

⏰ Signal: MORNING SCAN — 08:55 WIB
─────────────────────────────────
[✅ APPROVE] [❌ REJECT] [📋 DETAIL]
```

### 5.3 Decision Logic

```python
# src/signal/decision.py

DECISION_MATRIX = {
    # (unified_score, bandar_direction, ta_signal) → decision
    ("HIGH",   "ACCUMULATE", "STRONG_BUY"): "BUY",
    ("HIGH",   "ACCUMULATE", "BUY"):        "BUY",
    ("MEDIUM", "ACCUMULATE", "BUY"):        "BUY",
    ("MEDIUM", "NEUTRAL",    "BUY"):        "HOLD",
    ("LOW",    "DISTRIBUTE", "NEUTRAL"):    "IGNORE",
    ("LOW",    "DISTRIBUTE", "SELL"):       "IGNORE",
}

# Override rules (Guard Pipeline compatible)
IGNORE_IF = [
    "atr_pct > 8",           # terlalu volatile
    "volume_ratio < 0.5",    # volume terlalu sepi
    "rr_ratio < 1.5",        # R:R tidak layak
    "adx < 15",              # no trend
]
```

---

## 6. Scheduler: 3x Daily Cadence

```python
# src/scheduler/market_cron.py

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

WIB = pytz.timezone("Asia/Jakarta")

scheduler = AsyncIOScheduler(timezone=WIB)

# --- Morning Scan: pre-market ---
@scheduler.scheduled_job("cron", hour=8, minute=55, day_of_week="mon-fri")
async def morning_scan():
    """
    Objective: Position entry before open
    Scope: Full 800+ screener → top 5 candidates
    Signal type: Setup candidates for today's session
    """
    await run_full_pipeline(scan_type="MORNING")

# --- Midday Scan: intraday ---
@scheduler.scheduled_job("cron", hour=11, minute=30, day_of_week="mon-fri")
async def midday_scan():
    """
    Objective: Intraday momentum follow-through
    Scope: Watchlist + morning scan survivors
    Signal type: Add / hold / exit signals
    """
    await run_full_pipeline(scan_type="MIDDAY")

# --- Closing Scan: end of day ---
@scheduler.scheduled_job("cron", hour=15, minute=45, day_of_week="mon-fri")
async def closing_scan():
    """
    Objective: Tomorrow's swing setup preparation
    Scope: Full screener + updated bandar data
    Signal type: Overnight hold / cut loss / new setup
    """
    await run_full_pipeline(scan_type="CLOSING")

# --- Bandar Data Refresh ---
@scheduler.scheduled_job("cron", hour=16, minute=30, day_of_week="mon-fri")
async def refresh_bandar_data():
    """IDX broker summary available ~30 min after close"""
    await fetch_and_cache_broker_flow_all()
```

---

## 7. Data Provider Abstraction

```python
# src/data/provider.py

from enum import Enum
import yfinance as yf

class DataSource(Enum):
    TWELVEDATA  = "twelvedata"
    ALPHA_VANTAGE = "alpha_vantage"
    YFINANCE    = "yfinance"          # fallback

class IDXDataProvider:
    """
    3-tier fallback (existing karsa pattern):
    TwelveData → Alpha Vantage → yfinance
    IDX tickers: append ".JK" suffix for yfinance
    """
    def get_ohlcv(self, ticker: str, period: str = "60d") -> pd.DataFrame | None:
        jk_ticker = f"{ticker}.JK"
        for source in [DataSource.TWELVEDATA, DataSource.ALPHA_VANTAGE, DataSource.YFINANCE]:
            try:
                df = self._fetch(source, jk_ticker, period)
                if df is not None and len(df) > 20:
                    return df
            except Exception as e:
                logger.warning(f"[{source}] failed for {ticker}: {e}")
        return None

# Cache layer
class RedisCache:
    """
    TTL strategy:
    - OHLCV data:     30 min (intraday refresh)
    - Broker flow:    24h (daily update)
    - Screener rank:  15 min (stale quickly)
    """
    TTL = {
        "ohlcv":        1800,
        "broker_flow":  86400,
        "screener":     900,
        "ta_result":    1800,
    }
```

---

## 8. Integration with Existing karsa

### 8.1 Claude AI Reasoning Layer (existing, enhanced)

```python
# Existing Claude agent is called AFTER screener + TA + bandar
# It receives structured context, not raw data

claude_context = {
    "ticker": signal.ticker,
    "unified_score": signal.unified_score,
    "ta_summary": {
        "rsi": ta.rsi_14,
        "macd_direction": "bullish",
        "trend": ta.trend_direction,
        "key_levels": {"support": fib["nearest_support"], "resistance": fib["nearest_resistance"]}
    },
    "bandar_summary": {
        "broker_direction": broker.flow_direction,
        "institutional_net_b": broker.institutional_net,
        "foreign_signal": foreign.foreign_signal,
        "consecutive_days": foreign.consecutive_buy_days,
    },
    "macro_context": "IHSG +0.8% today, sektor keuangan outperform",
    "task": "Provide 3-sentence investment thesis for this setup. Be concise and actionable."
}
```

### 8.2 Guard Pipeline Integration

```python
# Guard pipeline checks BEFORE signal goes to Telegram
GUARD_RULES = [
    {"name": "min_rr",       "check": "rr_ratio >= 1.5",       "action": "BLOCK"},
    {"name": "max_sl_pct",   "check": "sl_pct <= 7.0",         "action": "BLOCK"},
    {"name": "min_score",    "check": "unified_score >= 50",    "action": "BLOCK"},
    {"name": "volume_check", "check": "volume_ratio >= 1.0",    "action": "WARN"},
    {"name": "bandar_align", "check": "bandar_score >= 40",     "action": "WARN"},
]
```

### 8.3 HITL Telegram Flow (unchanged philosophy)

```
Signal generated → Guard Pipeline → Telegram APPROVE/REJECT
                                              ↓
                                    APPROVE → Order execution via broker
                                    REJECT  → Archived in DB + reason logged
```

---

## 9. Dependencies

```toml
# pyproject.toml additions

[tool.poetry.dependencies]
pandas-ta = "^0.3.14b"      # All TA indicators
httpx = "^0.27"              # Async HTTP for IDX scraping
beautifulsoup4 = "^4.12"     # HTML parsing for IDX tables
lxml = "^5.2"                # HTML parser backend
redis = "^5.0"               # Cache layer (existing)
apscheduler = "^3.10"        # Scheduler (existing, enhanced)
yfinance = "^0.2"            # Fallback data source (existing)
pybit = "^5.6"               # Bybit (existing)
```

---

## 10. Implementation Roadmap

```
PHASE 1 — IHSG Screener (Week 1-2)
├── universe.py: load full 820 IDX ticker list
├── volume_filter.py: volume_ratio computation
├── pattern_filter.py: breakout/reversal/squeeze
├── ihsg_screener.py: full scan orchestrator
└── tests/test_screener.py

PHASE 2 — Technical Indicator Engine (Week 2-3)
├── engine.py: pandas-ta integration
├── fibonacci.py: swing high/low fib levels
├── scorer.py: TA composite score 0-100
├── entry_tp_sl: ATR + fibonacci based
└── tests/test_indicators.py

PHASE 3 — Bandarmologi (Week 3-5)
├── broker_flow.py: IDX scraper + parser
├── foreign_flow.py: net asing tracker
├── accumulation.py: pattern detector
├── bandar_scorer.py: composite 0-100
└── tests/test_bandarmologi.py

PHASE 4 — Signal Fusion (Week 5-6)
├── fusion.py: weighted score aggregation
├── decision.py: BUY/HOLD/IGNORE matrix
├── formatter.py: enhanced fmt.py Telegram output
└── integration test: end-to-end pipeline

PHASE 5 — Scheduler + Production (Week 6-7)
├── market_cron.py: 3x daily APScheduler jobs
├── data caching: Redis TTL strategy
├── monitoring: scan latency, error rate alerts
└── Docker Compose: add new service containers

PHASE 6 — Claude AI Enhancement (Week 7-8)
├── Richer context injection (TA + bandar summary)
├── Macro context integration (IHSG, sektor rotation)
├── Backtesting module (signal quality retrospective)
└── Performance tracking per signal source
```

---

## 11. Success Metrics

| Metric | Target | Measurement |
|---|---|---|
| Scan latency (800 stocks) | < 5 min | APM timing |
| Signal precision (BUY → profit) | > 60% | Backtesting + live tracking |
| Bandar alignment accuracy | > 65% | Correlation: bandar score vs next-5d return |
| Screener recall (miss rate) | < 10% | Retrospective: did good stocks pass filter? |
| System uptime (3x daily) | > 99% | Healthcheck + alert |

---

## 12. Risk & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| IDX scraper breaks (site changes) | High | RTI Business API fallback + alert |
| Data staleness | Medium | Redis TTL + freshness timestamp check |
| False positive bandar signal | Medium | Require TA confirmation (AND logic) |
| Over-scanning performance | Low | Batch processing + async I/O |
| HITL approval bottleneck | Low | Non-blocking: signal queued, not blocking scan |

---

*Document ends. Next: implement Phase 1 — IHSG Screener.*