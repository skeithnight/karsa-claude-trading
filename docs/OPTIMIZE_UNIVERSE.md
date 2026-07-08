# Karsa Trading Bot: Universe Scorer Upgrade & Validation Guide

## 📑 Executive Summary
The default universe scoring logic in Karsa was optimized for **momentum**, which inadvertently caused it to buy exhausted pumps (FOMO). For example, a token up 174% in 24 hours would receive a perfect score, right before experiencing a massive mean-reversion dump.

This document outlines the **Upgraded Universe Scorer**, which shifts the bot's focus from *lagging indicators* (24h change) to *leading indicators* (1h breakouts, short squeezes, and overextension penalties). It also provides a strict **Validation Protocol** to ensure the new logic generates alpha before risking real capital.

---

## 🧠 The Core Improvements

### 1. The "Overextension" Penalty (Stop Buying the Top)
Tokens that have already moved >50% in 24 hours are heavily penalized. This prevents the bot from entering at the exact top of a retail FOMO cycle.

### 2. Early Breakout Detection (1h vs 24h)
The new logic prioritizes the **1-hour price change**. If a coin is up 6% in the last hour, but only up 15% in 24h, it is just starting its run. This catches the *start* of the momentum, not the end.

### 3. The Short Squeeze Multiplier
If a token is pumping (price up) but the **Funding Rate is negative** (shorts are paying longs), it indicates a short squeeze. The bot now aggressively rewards this specific market microstructure.

---

## 💻 Implementation: Upgraded Scorer Code

Replace the core scoring function in `src/advisory/universe_scorer.py` (or your equivalent universe engine file) with the following:

```python
def calculate_universe_score(token_data: dict) -> float:
    """
    Upgraded Scoring Logic: Focuses on early breakouts, penalizes exhaustion, 
    and rewards short-squeeze mechanics.
    """
    score = 0.0
    
    # 1. Extract Data (Ensure your Bybit API fetch includes 1h klines/tickers)
    vol_24h = float(token_data.get('turnover24h', 0))  
    price_change_24h = float(token_data.get('price24hPcnt', 0)) * 100  
    # Fallback to 0 if 1h data isn't fetched yet
    price_change_1h = float(token_data.get('price1hPcnt', 0)) * 100 if 'price1hPcnt' in token_data else 0.0
    funding_rate = float(token_data.get('fundingRate', 0))
    
    # Base Volume Filter (Hard floor to ensure liquidity)
    if vol_24h < 250_000:
        return 0.0 

    # ==========================================
    # A. VOLUME SCORE (Max 30 points)
    # ==========================================
    if vol_24h >= 100_000_000: score += 30
    elif vol_24h >= 50_000_000: score += 25
    elif vol_24h >= 10_000_000: score += 20
    elif vol_24h >= 2_000_000: score += 10
    else: score += 5

    # ==========================================
    # B. EARLY MOMENTUM SCORE (Max 40 points)
    # ==========================================
    # 1. The "Early Breakout" Bonus (The real alpha)
    if price_change_1h > 5.0 and price_change_24h < 30.0:
        score += 40  # Catching the start of the move
    elif price_change_1h > 3.0 and price_change_24h < 20.0:
        score += 30
    elif price_change_1h > 1.5:
        score += 20
    # 2. Standard 24h Momentum (Fallback)
    elif price_change_24h > 10.0:
        score += 25
    elif price_change_24h > 5.0:
        score += 15
        
    # ==========================================
    # C. THE "OVEREXTENSION" PENALTY
    # ==========================================
    if price_change_24h > 80.0:
        score -= 40  # Severe penalty: DO NOT BUY THE TOP
    elif price_change_24h > 50.0:
        score -= 25  # Heavy penalty
    elif price_change_24h > 30.0:
        score -= 10  # Mild penalty
        
    # ==========================================
    # D. SHORT SQUEEZE DETECTOR (Max 30 points)
    # ==========================================
    if price_change_1h > 2.0 and funding_rate < -0.0001:
        score += 30  # Massive bonus: Short squeeze in progress
    elif price_change_24h > 5.0 and funding_rate < 0:
        score += 15  # Mild bonus
        
    # Penalize extreme long-funding (overheated longs)
    if funding_rate > 0.0005:
        score -= 15 

    return max(0.0, score)
```

> **⚠️ Data Ingestion Note:** Bybit's standard `/v5/market/tickers` endpoint does not always return `price1hPcnt`. You must ensure your `UniverseEngine` fetches 1-hour klines (or uses a websocket stream) to calculate the 1-hour price change and injects it into the `token_data` dictionary as `price1hPcnt`.

---

## 🧪 Validation Protocol: How to Prove It Works

Do not deploy this directly to live trading. Follow this 4-phase validation process to ensure the new logic actually improves profitability.

### Phase 1: Unit Testing (Local Logic Check)
Before running the bot, write a quick Python script to verify the math behaves exactly as expected with mock data.

**Create `test_scorer.py`:**
```python
import pytest
from src.advisory.universe_scorer import calculate_universe_score

def test_exhausted_pump_is_penalized():
    # Simulating EVA (+174% 24h, high volume, normal funding)
    data = {'turnover24h': 75000000, 'price24hPcnt': 1.74, 'price1hPcnt': 0.02, 'fundingRate': 0.0001}
    score = calculate_universe_score(data)
    assert score < 30, "Exhausted pumps should be heavily penalized!"

def test_early_breakout_is_rewarded():
    # Simulating EDGE (+20% 24h, but +6% in the last 1h)
    data = {'turnover24h': 22000000, 'price24hPcnt': 0.20, 'price1hPcnt': 0.06, 'fundingRate': 0.0001}
    score = calculate_universe_score(data)
    assert score > 60, "Early breakouts should score highly!"

def test_short_squeeze_multiplier():
    # Simulating CLO (+15% 24h, +4% 1h, NEGATIVE funding)
    data = {'turnover24h': 10000000, 'price24hPcnt': 0.15, 'price1hPcnt': 0.04, 'fundingRate': -0.0005}
    score = calculate_universe_score(data)
    assert score > 70, "Short squeezes should get the maximum bonus!"
```
*Run `pytest test_scorer.py` to ensure the logic is mathematically sound.*

### Phase 2: Historical Backtesting
Run the bot's backtester (or a custom script) over the last 30 days of market data. 
1. Run the backtest with the **Old Scorer** and record the Win Rate and Max Drawdown.
2. Run the backtest with the **New Scorer** and record the same metrics.
3. **Validation Metric:** The New Scorer should show a **higher win rate** and **fewer massive drawdowns** (because it avoided the +100% dump traps).

### Phase 3: Shadow / Paper Trading (Live Data, Fake Money)
Run the bot in your live environment, but disable actual order execution (`DRY_RUN=True` or Paper Trading mode). Let it run for **48 to 72 hours**.

**What to monitor in the logs:**
*   **Universe Selection:** Check the logs when `_job_refresh_universe` runs. Are the top 50 tokens now featuring early breakouts instead of exhausted pumps?
*   **Trade Triggers:** When the bot decides to enter a trade, check the `price_change_24h` and `price_change_1h` in the logs. It should be entering on coins with healthy 1h momentum, not 24h exhaustion.
*   **Paper PnL:** Compare the paper trading PnL against a simple "Buy and Hold BTC" baseline.

### Phase 4: Live Telemetry & Guardrails
Once you switch to live trading with real capital, monitor these specific Karsa dashboard metrics for the first week:

1.  **Average Entry 24h Change:** This should drop significantly. If it's still averaging +50% 24h change, the overextension penalty isn't working.
2.  **Funding Rate at Entry:** You want to see a mix of positive and negative funding rates. If it's *only* entering on highly positive funding, the short-squeeze logic might be overpowered.
3.  **Time-in-Trade:** Early breakouts should result in faster take-profits. If the bot is holding tokens for days without moving, your take-profit targets might need adjustment.

---

## 🚀 Next Steps
1. Apply the **Database Pool Fixes** (from the previous step) to ensure the bot doesn't crash.
2. Implement the **Upgraded Scorer Code** and ensure your API fetches 1h price data.
3. Run **Phase 1 (Unit Tests)** to verify the math.
4. Deploy to **Phase 3 (Paper Trading)** for 48 hours.
5. Go Live.