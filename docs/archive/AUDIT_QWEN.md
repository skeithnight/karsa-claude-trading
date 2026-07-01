# AUDIT_QWEN.md: Karsa AI Advisory Desk - Institutional Architecture Audit

**Document Status:** Approved & Mandated for Implementation  
**Auditor:** Chief Investment Officer (CIO) / Head of Trading  
**Target Repository:** `skeithnight/karsa-claude-trading`  
**Architecture Paradigm:** Zero-Execution AI Quant Research & Advisory Desk  

---

## 1. Executive Summary

As the CIO of this trading desk, I have conducted a comprehensive architectural and strategic audit of the Karsa repository. 

**The Verdict:** The system has successfully transitioned from a fragile, retail-grade execution bot into a highly sophisticated **AI Quant Research & Advisory Desk**. The removal of live broker execution was a critical risk-mitigation milestone. The Telegram UI/UX overhaul provides institutional-grade visibility, and the multi-agent orchestration is well-structured.

However, to operate this desk at scale without suffering catastrophic data failures (e.g., HTTP 429 IP bans) or event-loop freezes, **the Data Infrastructure layer requires immediate hardening.** We are officially deprecating all unstable web scrapers and mandating a "Warehouse-First, Async-Router" architecture.

This document outlines the approved architecture, the strict rules of engagement, and the mandatory remediation steps for the engineering team.

---

## 2. Strategic Pivot: The "No-Execution" Advisory Model

### 2.1 The Execution Purge (Approved)
*   **Audit Finding:** All Alpaca, Interactive Brokers, and IDX broker API wrappers have been successfully removed. 
*   **CIO Directive:** This is permanent. We do not let unproven AI models touch live capital. The system's sole mandate is to generate high-fidelity signals, size risk, and advise the human trader.

### 2.2 Shadow Execution & Paper Trading (Mandated)
*   **Audit Finding:** The system must track its own performance to prove alpha before live deployment.
*   **CIO Directive:** The `Orchestrator` must output structured **Trade Idea JSONs** (including entry triggers, stops, targets, and LLM rationale). These must be ingested by a **Shadow Execution Engine** that updates hypothetical PnL in PostgreSQL in real-time. 

---

## 3. Telegram UI/UX & CIO Dashboard (Approved)

The Telegram interface has been successfully upgraded to a "Bloomberg Terminal in your pocket." The use of HTML and `<pre>` tags for monospaced ASCII tables is the correct approach for mobile financial data.

### 3.1 Command Center Standards
All commands must adhere to the strict HTML formatting rules defined in `DESIGN_TEXT_TELEGRAM.md`:
*   **No nested tags inside `<pre>`:** Use standard text and emojis inside monospaced blocks.
*   **Strict Escaping:** All dynamic LLM/user input must be HTML-escaped to prevent parser crashes.
*   **Inline Keyboards:** Major dashboards (`/briefing`, `/ideas`) must include inline buttons (e.g., `[🔍 Audit NVDA]`) to allow deep-drilling without typing.

### 3.2 Proactive Intelligence (Push Notifications)
The bot must not wait for the CIO to ask questions. The scheduler must push the following automated alerts:
1.  **Pre-Market Battle Plan (09:25 EST / 09:55 WIB):** Actionable triggers, exact price levels, macro regime context, and daily risk limits.
2.  **End-of-Day Review (16:15 EST):** Summary of triggered ideas, daily paper PnL, and win/loss ratio.
3.  **Automated Kill Switch:** If the Shadow Portfolio hits the `DAILY_LOSS_LIMIT_PCT` (e.g., -1.5%), the system must immediately push a 🛑 **HALT TRADING ALERT** and cease generating new ideas for the session.

---

## 4. Data Infrastructure & Resilience (CRITICAL REMEDIATION)

*This is the most critical section of the audit. The current reliance on fragile scrapers is an unacceptable operational risk.*

### 4.1 The Ban on Unstable Scrapers
*   **`yfinance` (Yahoo Finance):** **BANNED.** Aggressively blocks automated requests, resulting in fatal HTTP 429 rate limits.
*   **`Verdenroz/GoogleFinanceAPI`:** **BANNED.** Archived and dead.
*   **Raw TradingView MCP for OHLCV:** **BANNED.** Unofficial scrapers will get our IP blacklisted.

### 4.2 The New "Warehouse-First" Architecture
We are shifting from "scrape-on-demand" to a resilient, multi-tier API and Data Warehouse model.

#### Tier 1: The Historical Data Warehouse (Powered by `dlt`)
*   **Mechanism:** A nightly Python cron job uses `dlt` (data load tool) to fetch daily EOD data via official APIs (Polygon.io) and incrementally loads it into **PostgreSQL**.
*   **Rule:** The AI Orchestrator **never** calls an external API for data older than 24 hours. It queries our local PostgreSQL database.

#### Tier 2: The Real-Time "Traffic Cop" (Async Router)
When the bot needs *current* price data for the Shadow Portfolio, it must use the `MarketDataRouter`.
*   **Native Async HTTP:** Must use `httpx.AsyncClient`. **Never** use synchronous `requests` or `yfinance` inside an `async def` function, as it will block the Telegram bot's event loop.
*   **Waterfall Fallback:** Try Polygon.io (Tier 1) ➔ Fallback to Finnhub (Tier 2).
*   **Redis Circuit Breaker:** If an API fails 3 times, write a flag to Redis to bypass it for 10 minutes.

#### Tier 3: Aggressive Redis Caching
*   **Real-time quotes:** Cache for 60 seconds.
*   **Intraday 1m bars:** Cache for 5 minutes.
*   **Daily EOD bars:** Cache for 12 hours.
*   **Redis Locks:** Must implement distributed locks (`lock:{cache_key}`) to prevent "cache stampedes" (multiple threads hitting the API simultaneously when the cache expires).

---

## 5. AI Agents & Local Technical Analysis

### 5.1 The "No Math by LLM" Rule
*   **Audit Finding:** LLMs are slow, expensive, and hallucinate math. 
*   **CIO Directive:** The LLM is strictly reserved for **unstructured data** (news sentiment, earnings call parsing) and **final portfolio synthesis**. 

### 5.2 Local Indicator Engine (`pandas-ta`)
*   **Mechanism:** Once raw OHLCV data is fetched (from Postgres or the Async Router), technical indicators must be calculated locally in milliseconds using `pandas-ta`.
*   **Implementation:** 
    ```python
    import pandas_ta as ta
    # Calculate locally. Zero API cost. Instantaneous.
    df.ta.sma(length=200, append=True)
    df.ta.rsi(length=14, append=True)
    df.ta.atr(length=14, append=True) 
    ```
*   The Analyst Agents (`IDXAnalyst`, `USAnalyst`, `ETFAnalyst`) must read the `df.ta` columns directly. They must never ask the MCP or the LLM "What is the RSI?"

---

## 6. Risk Management & Portfolio Advisory

### 6.1 Macro Regime Filters
Strategies must be turned on or off based on the broader market environment. The `MacroRegimeFilter` must check:
*   **VIX > 25 OR SPY < 200 SMA:** Disable US Momentum and ETF Mean Reversion. Switch to cash/inverse.
*   **ADX > 25 (Strong Trend):** Disable ETF Mean Reversion (it fails in strong trends).

### 6.2 Volatility-Targeted Position Sizing
*   **Audit Finding:** Fixed percentage position sizing (e.g., 5% per trade) is naive.
*   **CIO Directive:** Implement ATR-based (Average True Range) volatility targeting. Every trade must risk exactly the same amount of capital (e.g., 1% of total equity), regardless of the stock's price or volatility.
    *   *Formula:* `Position Size = (Equity * 0.01) / (ATR * 2.0)`

### 6.3 The Portfolio Risk Advisor
The `RiskManager` agent must run *after* the Analysts generate signals but *before* they are sent to the CIO. It must evaluate:
*   Sector concentration limits (e.g., Max 40% in Tech).
*   Portfolio Beta exposure.
*   Correlation overlap between new ideas and existing Shadow Positions.

---

## 7. Actionable Remediation Plan (Next Sprints)

To bring the repository to 100% compliance with this audit, the engineering team will execute the following sprints:

### Week 1: Data Infrastructure Hardening
*   [ ] **Purge Scrapers:** Remove `yfinance` and any TV MCP OHLCV fetching logic from `requirements.txt` and codebase.
*   [ ] **Implement `MarketDataRouter`:** Build the `httpx` async router with `asyncio.Semaphore` throttling and Redis distributed locks.
*   [ ] **Setup Redis Caching:** Implement the strict TTL rules (60s for quotes, 5m for intraday).

### Week 2: The Data Warehouse (ETL)
*   [ ] **Implement `dlt` Pipeline:** Write the `polygon_daily_resource` script to batch-load historical data into PostgreSQL.
*   [ ] **Local TA Engine:** Refactor all Analyst agents to use `pandas-ta` for indicator calculation. Remove all LLM prompts asking for technical analysis math.
*   [ ] **Scheduler:** Setup the nightly Cron job for the ETL pipeline.

### Week 3: Risk & Advisory Refinement
*   [ ] **Volatility Sizing:** Update the `RiskManager` to calculate position sizes using ATR instead of fixed percentages.
*   [ ] **Regime Filters:** Integrate the VIX and 200 SMA checks into the Orchestrator's strategy selection logic.
*   [ ] **Kill Switch:** Implement the automated Telegram alert if Shadow PnL breaches the daily loss limit.

### Week 4: Telegram UI & Proactive Alerts
*   [ ] **Push Notifications:** Finalize the `APScheduler` jobs for the 09:25 EST Pre-Market Battle Plan and 16:15 EST EOD Review.
*   [ ] **HTML Polish:** Ensure all new commands strictly use the `<pre>` monospaced formatting and HTML escaping rules defined in `DESIGN_TEXT_TELEGRAM.md`.

---

## 8. Final CIO Directive

The Karsa AI Advisory Desk has the potential to be a highly lucrative, institutional-grade research tool. By removing the operational risk of live execution, banning fragile data scrapers, and enforcing local technical analysis, we are building a system that is **fast, resilient, and mathematically sound.**

The UI is beautiful. The agents are smart. Now, we must fortify the data pipeline so the system never flies blind. 

Review this document, align your Jira/Linear tickets to the Remediation Plan, and execute. 

**Signed,**  
*Chief Investment Officer*  
*Karsa Trading Desk*
```