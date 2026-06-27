# 📄 CIO_AUDIT_REPORT_V4.md

**TO:** Lead Quant / System Architect
**FROM:** Office of the CIO / Head of Systematic Trading
**DATE:** June 28, 2026
**SUBJECT:** V4 Architecture Review & V5 Trading Logic Mandates
**STATUS:** 🛑 **HOLD FOR CAPITAL DEPLOYMENT** (Paper Trading Only)

## 1. Executive Summary
The V4 deployment script successfully resolves the infrastructure bottlenecks of V3. The isolation of API keys via `9Router`, the implementation of idempotency keys, and the append-only Postgres audit schema represent institutional-grade DevOps. 

However, the system currently operates as a **theoretical data pipeline, not a trading desk**. The reliance on scraping TradingView for execution signals, the use of LLMs in the critical execution path, and the implementation of raw Kelly Criterion sizing violate core market microstructure and risk management principles. V5 must pivot from "making the code run" to "surviving market realities."

---

## 2. Strategy & Alpha Decay Challenge

### A. IDX Foreign Flow Breakout
* **The V4 Logic:** 3-day foreign net buy > 5% volume + Bollinger breakout + ARA buffer.
* **The CIO Challenge:** 
  1. **Data Latency:** "TradingView TA (direct Python)" does not provide real-time, tick-by-tick IDX order book data. Foreign flow in IDX (especially the Big 4 Banks: BBCA, BBRI, BMRI, BBNI) is heavily front-run by local HFTs. By the time your scraper registers the 5% threshold, the stock is likely already locked at ARA (Auto Reject Atas).
  2. **Tick-Size Ignorance:** IDX tick sizes are strictly tiered by price band. A breakout on a Rp 50 stock behaves entirely differently than a breakout on a Rp 10,000 stock. 
* **V5 Mandate:** You must integrate a direct IDX broker API (e.g., IPOT, Mirae, or a direct FIX protocol feed) for Level 2 data. Scraped data is for *post-trade analysis*, not *pre-trade execution*.

### B. US Relative Strength Momentum
* **The V4 Logic:** 60-day RS > SPY by 15% + trend alignment (50 EMA > 200 EMA).
* **The CIO Challenge:** A 60-day lookback in modern US Equities is an eternity. You are buying assets that have already peaked in momentum and are likely entering a mean-reversion phase or facing institutional profit-taking.
* **V5 Mandate:** Shift to cross-sectional momentum (ranking the top 10% of a 500-stock universe based on 1-month and 3-month RS) and implement an **Earnings Blackout Filter**. Do not initiate momentum positions within 5 days of an earnings print.

### C. ETF Mean Reversion
* **The V4 Logic:** RSI < 30 + lower Bollinger Band touch.
* **The CIO Challenge:** Catching falling knives without a strict macro veto is how trading desks blow up. In a high-VIX regime, an RSI of 30 is not a buy signal; it is a signal of structural breakdown.
* **V5 Mandate:** The `MacroRegimeFilter` must have hard veto power. If `VIX > 25` OR `SPY < 200 SMA`, the Mean Reversion agent is **programmatically disabled**. 

---

## 3. Execution & Microstructure Risks

### A. The LLM Latency Bottleneck
Routing signals through `9Router` to Anthropic (Sonnet 4 / Haiku) for validation introduces 2 to 8 seconds of latency per decision. 
* **The Reality:** In a US equity flash crash or an IDX ARA queue lock, 8 seconds is the difference between a fill and a missed trade. 
* **V5 Mandate:** LLMs must be removed from the hot execution path. Use deterministic Python/Cython code for signal gating and order routing. Reserve the LLMs for the **Advisory Layer** (e.g., morning briefings, post-trade audit reasoning, and regime analysis).

### B. Telegram HITL (Human-in-The-Loop) Flaws
Using Telegram polling/webhooks for trade approval is an operational hazard.
* **The Reality:** If the PM is asleep during US market hours (which overlaps with深夜/early morning WIB), or if the Telegram API drops, the system halts. Furthermore, manual approval defeats the purpose of algorithmic scale.
* **V5 Mandate:** Implement **Threshold-Based Execution**. 
  * `< 0.5% AUM`: Auto-execute (Paper or Live).
  * `> 0.5% AUM`: Route to Telegram for HITL approval with a 15-minute timeout. If no response, auto-cancel.
  * Maintain the `/stop` kill-switch, but remove manual approval for standard deviations.

---

## 4. Risk Management & Position Sizing

### A. The Kelly Criterion Trap
The V4 `position_sizer.py` uses Kelly / fixed-fractional lot sizing.
* **The Reality:** Full Kelly assumes you know the *exact* win probability and payoff ratio of your edge. LLMs and TA cannot predict this with the precision Kelly requires. Full Kelly guarantees massive drawdowns (often >50%) during inevitable losing streaks.
* **V5 Mandate:** Cap sizing at **Quarter-Kelly (0.25x)** or switch to **Volatility Targeting (Risk Parity)**. Position size should be inversely proportional to the asset's 20-day ATR, ensuring every trade risks exactly 1% of total equity, regardless of the asset's volatility.

### B. IDX T+2 and Liquidity Traps
* **The Reality:** You have accounted for ARA/ARB limits, but not for liquidity traps. If you buy a mid-cap IDX stock and it hits ARB (Auto Reject Bawah) for 3 consecutive days, you cannot sell, and your capital is locked, destroying your portfolio's Sharpe ratio.
* **V5 Mandate:** Implement a **20-Day Average Volume (ADV) Gate**. The system must never take a position size that exceeds 10% of the asset's 20-day ADV. If the exit door is smaller than your position, you do not enter the room.

---

## 5. Infrastructure & Schema Directives (V5)

1. **Docker Compose Memory Limits:** Raising the orchestrator to `1536M` is a band-aid. If the orchestrator is holding state for 50 concurrent agent scans, it will OOM. Move the agent state to Redis (which you already have) and keep the orchestrator stateless.
2. **Postgres Schema Addition (`broker_fills`):** Your current schema tracks `trade_history` (internal signals) but lacks a reconciliation table. You must add a `broker_fills` table to ingest actual broker execution reports via webhook to calculate true slippage and realized PnL.
3. **9Router Fallback:** Removing DeepSeek was a mistake. DeepSeek (or a local Llama-3-8B instance) should remain as the ultra-low-cost, zero-latency fallback for basic JSON parsing of TA indicators, reserving Anthropic Sonnet strictly for complex macro reasoning.

## Sign-off
The V4 codebase is approved for **Shadow Paper Trading** to collect slippage and latency metrics. Real capital deployment is **DENIED** until the V5 Data Feed (Broker API) and V5 Risk Veto (Macro/ADV gates) are merged to `main`.

**[ SIGNED ]**
*CIO & Head of Systematic Trading*
*June 28, 2026*