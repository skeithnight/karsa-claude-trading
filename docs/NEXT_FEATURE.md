# NEXT_FEATURE.md: Karsa AI Advisory Desk Evolution

**Document Status:** Approved for Implementation  
**Author:** CIO / Head of Trading  
**Date:** October 2023  
**Target Architecture:** AI Quant Research & Advisory Desk (Non-Execution)

---

## 1. Executive Summary

We are pivoting the Karsa architecture from an **"Automated Execution Bot"** to an **"AI Quant Research & Advisory Desk."** 

By removing live broker execution (Alpaca/IDX), we eliminate catastrophic execution risk, broker API latency issues, and regulatory overhead. The system's sole mandate is now to act as an institutional-grade research analyst: generating high-fidelity signals, rigorously sizing risk, and delivering actionable intelligence to the CIO via Telegram.

---

## 2. Architectural Pivot: The "No-Execution" Model

### 2.1 Purge Broker Integrations
*   **Action:** Delete all Alpaca, Interactive Brokers, and IDX broker API wrappers. 
*   **Action:** Remove `EXECUTION_MODE` and broker API keys from `.env`.

### 2.2 Refactor Orchestrator: Orders ➔ Trade Ideas
The Orchestrator must no longer format data for a broker API. It must output structured **Trade Idea JSONs**.
```json
{
  "ticker": "NVDA",
  "conviction_score": 8.5,
  "strategy": "US_Momentum",
  "trigger_condition": "Break and hold $125.50 on 1m close",
  "entry_zone": [125.50, 126.00],
  "stop_loss": 122.00,
  "target": 135.00,
  "time_horizon": "3_days",
  "llm_rationale": "Strong relative strength, breaking pre-market high..."
}
```

### 2.3 Implement "Shadow Execution" (Paper Trading Engine)
*   Build a lightweight internal paper-trading engine.
*   Every generated "Trade Idea" is logged to PostgreSQL (`Signals`, `Paper_Positions`, `Closed_Paper_Trades`).
*   The engine continuously updates hypothetical PnL using real-time market data to prove alpha before live capital is ever risked.

### 2.4 Upgrade Risk Manager ➔ Portfolio Risk Advisor
Without live orders to block, the Risk Manager evaluates and sizes signals for the human trader. It outputs portfolio-level risk scores (e.g., *"Taking all 5 signals increases Portfolio Beta to 1.2. Recommend reducing TSLA size by 50%."*).

---

## 3. Alpha & Strategy Enhancements

### 3.1 Deterministic Technical Analysis
*   **Rule:** LLMs must **never** calculate RSI, Bollinger Bands, or Foreign Flow. 
*   **Action:** Move all technical signal generation to deterministic Python (Pandas/TA-Lib). The LLM is strictly reserved for unstructured data (news sentiment, earnings parsing) and final synthesis.

### 3.2 Macro Regime Filters
Implement a `Regime_Filter` module to turn strategies on/off based on market environment:
*   If `VIX > 25` OR `SPY < 200 SMA`: Disable US Momentum and ETF Mean Reversion.
*   If `ADX > 25` (Strong trend): Disable ETF Mean Reversion.
*   ETF Mean Reversion: Only trigger if long-term trend is up (Price > 200 SMA).

### 3.3 Volatility-Targeted Position Sizing
Replace fixed percentage sizing with ATR-based volatility targeting so every trade risks exactly the same amount of capital.
```python
def calculate_position_size(equity, risk_per_trade_pct, entry_price, atr_14):
    risk_amount = equity * risk_per_trade_pct # e.g., 1% of total equity
    stop_distance = atr_14 * 2.0 # Stop loss set at 2x ATR
    if stop_distance == 0: return 0
    position_size = risk_amount / stop_distance
    return position_size / entry_price 
```

---

## 4. CIO Telegram Command Center

The Telegram bot must be upgraded to a high-signal, mobile-optimized dashboard using `MarkdownV2`.

### `/briefing` (Morning Dashboard)
> 📊 **KARSA DAILY BRIEFING** | *Fri, Jun 26, 08:00 AM*
> 🌍 **MACRO REGIME:** Risk-On (VIX: 14.2 | SPY > 200 SMA)
> 🎯 **TOP 3 IDEAS TODAY:**
> 1. $NVDA (Long) | Conviction: 9.2 | Strat: Momentum
> 2. $XLK (Long) | Conviction: 8.5 | Strat: Sector Rotation
> ⚠️ **RISK ALERTS:** Tech sector concentration at 45% (Limit: 40%).

### `/ideas [strategy]` (Deep Dive)
> 💡 **ACTIVE TRADE IDEAS** (US Momentum)
> **$NVDA** | 🟢 LONG
> 📈 **Entry:** $120.50 - $122.00 | 🛑 **Stop:** $115.00 (2x ATR)
> 🧠 **AI Rationale:** Breaking 3-month consolidation on 2x avg volume.
> ⚖️ **Risk Sizing:** Risk 1% of equity = 450 shares.

### `/regime` (Market State & Strategy Health)
> 🌡️ **MARKET REGIME CHECK**
> **Volatility:** Low (VIX 13.5) -> *Favors Momentum*
> 📉 **Strategy Perf (30d):** US Momentum: +4.2% | IDX Foreign Flow: -0.5% *(Flagged)*

### `/audit [ticker]` (The "Why" Command)
> 🔍 **AUDIT LOG: $TSLA**
> **Decision:** REJECTED Long Signal
> **Risk Manager Override:** REJECTED.
> **Reasoning:** Earnings in 2 days (Event Risk). Portfolio at max 5% Auto limit.

### `/pnl` (Shadow Portfolio Performance)
> 💰 **SHADOW PORTFOLIO PNL**
> **Total Paper Return:** +12.4% (Benchmark SPY: +8.1%)
> **Sharpe Ratio:** 1.85 | **Max Drawdown:** -4.2%

---

## 5. Automated Proactive Intelligence

The system will push automated alerts to Telegram based on market schedules.

### 5.1 Pre-Market Battle Plan (Automated Push)
**Schedule:** 9:25 AM EST (US) / 09:55 AM WIB (IDX)
**Content:** Actionable triggers, exact price levels, and risk parameters.

> 🚨 **KARSA PRE-MARKET BATTLE PLAN** 🚨
> 📅 *Fri, Jun 26 | US Equities | 09:25 AM EST*
> 🌡️ **REGIME:** Risk-On (Futures +0.4%, VIX 13.2)
> 
> 🎯 **ACTIONABLE TRADE IDEAS**
> 1️⃣ **$NVDA** | 🟢 **LONG** (Momentum)
> 🔹 **Trigger:** Buy *IF* breaks & holds **$125.50** (1m close).
> 🔹 **Stop:** Hard stop at **$122.00** | 🎯 **Target:** Scale out at **$130.00**.
> 
> ⚠️ **RISK & AVOIDANCE**
> 🚫 **DO NOT TOUCH:** $TSLA (Earnings after close. Binary risk).
> 📉 **Max Daily Drawdown:** -1.5%.

### 5.2 Automated Kill Switch Alert
If the Shadow Portfolio hits the `DAILY_LOSS_LIMIT_PCT` (e.g., -1.5%), the system immediately pushes:
> 🛑 **HALT TRADING ALERT** 🛑
> Daily loss limit breached. **Directive:** Do not take new Trade Ideas today. Review `/pnl`.

### 5.3 End-of-Day (EOD) Review (Automated Push)
**Schedule:** 4:15 PM EST (15 mins post-close).
**Content:** Summary of triggered ideas, paper PnL for the day, and win/loss ratio.

---

## 6. AI Execution Directives (Rules of Engagement)

When generating the Pre-Market Battle Plan and Trade Ideas, the LLM Orchestrator must strictly adhere to these rules:

1.  **Trigger-Based Language Only:** Never say "Buy $AAPL at market." Always say "Buy $AAPL **IF** [Condition]." (Prevents bad fills on opening gaps).
2.  **Time-in-Force (TIF) Context:** Every idea must include a time horizon tag (e.g., `[Horizon: Intraday]`, `[Horizon: 3 Days]`).
3.  **Strict Formatting:** All Telegram outputs must use `parse_mode='MarkdownV2'` for perfect mobile rendering.

---

## 7. Implementation Roadmap

### Week 1: The Purge & Paper Engine
*   [ ] Remove all broker execution code and `.env` variables.
*   [ ] Build PostgreSQL schema (`Signals`, `Paper_Positions`, `Closed_Paper_Trades`).
*   [ ] Implement Shadow Execution engine to update paper positions via real-time data feeds.

### Week 2: Telegram CIO Dashboard
*   [ ] Build `/briefing`, `/ideas`, `/regime`, `/audit`, and `/pnl` commands.
*   [ ] Implement `MarkdownV2` formatting and inline keyboard buttons for quick drilling.

### Week 3: Proactive Intelligence & Scheduling
*   [ ] Integrate `APScheduler` for 9:25 AM EST / 09:55 AM WIB Pre-Market pushes.
*   [ ] Integrate 4:15 PM EST EOD review push.
*   [ ] Implement the automated Kill Switch alert logic.

### Week 4: Alpha Refinement & Prompt Engineering
*   [ ] Move all Technical Analysis out of LLM prompts into deterministic Python functions.
*   [ ] Update Orchestrator system prompts to enforce "Trigger-Based Language" and "TIF Context".
*   [ ] Implement Macro Regime Filters and Volatility-Targeted Position Sizing.