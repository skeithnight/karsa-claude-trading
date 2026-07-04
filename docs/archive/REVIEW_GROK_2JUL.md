# Karsa Crypto Autonomous Agent — Enhancement Plan & Architectural Review

**Document Version:** 1.0  
**Date:** 2026-07-02  
**Status:** Approved for Implementation  
**Context:** Architectural review and integration of the "Crypto Autonomous Agent Enhancement Plan" with the Phase 1 & Phase 2 Master Design.

---

## 1. Executive Summary

The `karsa-claude-trading` repository possesses a highly sophisticated foundation, particularly in crypto perpetuals (featuring Hurst Exponents, Smart Order Routers, and regime awareness). 

The objective of this enhancement plan is to transform the crypto component into a **fully focused, robust, and statistically grounded autonomous trading agent**, while preserving the existing multi-market capabilities for traditional assets. 

This document critically analyzes the proposed enhancement plan, separating high-value architectural upgrades from dangerous anti-patterns, and provides a concrete, phased execution roadmap.

---

## 2. Critical Analysis of the Enhancement Plan

### 2.1 The 3 "Genius" Additions (Must Implement)

These three proposals are critical for transitioning the bot from a "smart script" to an institutional-grade system.

#### A. Crypto Separation (`CRYPTO_ONLY_MODE`)
*   **The Concept:** Decouple the crypto orchestrator from the traditional market (IDX/US/ETF) orchestrator.
*   **Why it’s Critical:** Crypto is 24/7/365; traditional markets sleep. Running them in the same process creates scheduling nightmares, memory bloat, and coupled failure domains. A crash in the US equity data fetcher should never interrupt an active crypto trailing stop.
*   **Implementation:** Create a dedicated `src/main_crypto.py` and a separate Docker service (`karsa-crypto-orchestrator`).

#### B. Confidence Calibration System
*   **The Concept:** Track the LLM's predicted confidence against actual trade outcomes and auto-adjust thresholds.
*   **Why it’s Critical:** LLMs are notoriously overconfident. If the LLM outputs "80% confidence" but the actual win rate is 40%, the risk model is fundamentally broken. 
*   **Implementation:** A deterministic Python tracker that maintains a rolling window of the last 100 trades. It calculates a `Calibration Multiplier` (e.g., `Actual Win Rate / Predicted Confidence`). All future LLM confidence scores are multiplied by this factor before hitting the Risk Manager.

#### C. Event-Driven Perp Backtesting (Funding & Slippage)
*   **The Concept:** A backtester that simulates 8-hour funding payments, maker/taker fees, and orderbook slippage.
*   **Why it’s Critical:** Standard equity backtesters assume zero cost to hold a position. In crypto perpetuals, funding rates can drain 15-30% of annual returns if you hold the wrong side of a trend. A strategy that looks profitable in a basic backtester might be deeply unprofitable once funding is applied.

### 2.2 The 3 "Danger Zones" (Do NOT Implement as Written)

These suggestions sound excellent in theory but will cause catastrophic latency or state-corruption in a live, autonomous environment.

#### 🚨 Danger 1: "Ensemble Reasoning" for Trade Entries
*   **The Proposal:** Run multiple prompts/models and aggregate signals for higher conviction trades.
*   **Why it Fails:** LLM API calls take 2–5 seconds. Running an ensemble of 3 models for a 5-minute candle entry adds 15 seconds of latency. In crypto, the move is already over, resulting in massive slippage.
*   **The Guardrail:** Use ensemble reasoning **strictly for macro regime analysis** (e.g., weekly trend classification) or **Universe Scoring**. Never use it for real-time, minute-level entry execution.

#### 🚨 Danger 2: Multi-Exchange Execution Fallback
*   **The Proposal:** Bybit primary + Binance secondary fallback for execution.
*   **Why it Fails:** If Bybit goes down, switching *active position management* (trailing stops, OMS reconciliation) to Binance is a state-reconciliation nightmare. You will end up with ghost positions, mismatched leverage, and double-exposure.
*   **The Guardrail:** Keep execution strictly single-venue (Bybit). Use Binance **only as a redundant data feed** (Websocket price streams) to verify Bybit's price data, but do not route execution orders to it.

#### 🚨 Danger 3: Raw Sentiment Injection (X/Twitter, News)
*   **The Proposal:** Feed raw tweets or news headlines into the LLM prompt as context.
*   **Why it Fails:** If you feed raw text into an LLM, it will become highly distracted, hallucinate patterns, and start trading meme-coin hype.
*   **The Guardrail:** Sentiment must be quantified **deterministically in Python first**. Use an NLP model to calculate a "Bullish/Bearish Ratio" (e.g., `0.65`) and pass *only that single float* to the LLM as a technical indicator. Do not let the LLM read the raw text.

---

## 3. Integration with Master Architecture

This enhancement plan perfectly complements the Phase 1 (Signal/Entry) and Phase 2 (Lifecycle/Execution) designs.

| Enhancement Proposal | Status in Master Design | Phase |
| :--- | :---: | :---: |
| **Dynamic Universe Management** | ✅ Fully Designed (Bybit pipeline, liquidity filters, profile-aware sizing) | Phase 1 |
| **Monitoring (Prometheus/Grafana)** | ✅ Fully Designed (Metrics, alerts, Docker stack) | Phase 1 |
| **Advanced Portfolio Risk** | ✅ Fully Designed (Cross-Market Capital Allocator) | Phase 2 |
| **Position Lifecycle / OMS** | 🔄 Enhanced (Adding Websocket local-stop engine & Reconciliation Cron) | Phase 2 |
| **Crypto Separation** | 🆕 New (Requires Docker Compose & Entrypoint split) | Phase 1 |
| **Confidence Calibration** | 🆕 New (Requires deterministic Python tracker) | Phase 2 |
| **Vector Memory (PGVector)** | 🆕 New (Requires RAG implementation for trade history) | Phase 3 |

---

## 4. Final Prioritized Roadmap

To maximize ROI and minimize system instability, implementation must follow this strict sequence.

### Sprint 1: Foundation & Observability (Weeks 1-2)
*Goal: Stabilize the architecture, decouple crypto, and see exactly what the bot is doing.*

1.  **Crypto Separation:** 
    *   Create `src/main_crypto.py`.
    *   Split `docker-compose.yml` to add `karsa-crypto-orchestrator`.
    *   Implement `CRYPTO_ONLY_MODE` environment flag.
2.  **Monitoring Stack:** 
    *   Deploy Prometheus and Grafana.
    *   Implement core metrics: Signal rejections, Universe size, Position sizing, API latency.
3.  **Risk Profiles:** 
    *   Implement the 3-tier Risk Profile Manager (Conservative/Semi/Aggressive).
    *   Wire Redis state and Telegram controls (`/mode`, `/setmode`).

### Sprint 2: Core Alpha & Intelligence (Weeks 3-5)
*Goal: Improve the statistical quality of the signals and the universe.*

1.  **Dynamic Universe:** 
    *   Implement the Bybit pipeline, volume/momentum scoring, and profile-aware filtering.
2.  **Confidence Calibration:** 
    *   Build the Python tracker comparing LLM confidence vs. actual win rate.
    *   Implement the dynamic penalty multiplier applied to LLM outputs.
3.  **Perp-Aware Backtester:** 
    *   Build the event-driven backtester simulating 8-hour funding rates and maker/taker fees.
    *   Validate all strategies against this backtester before live deployment.

### Sprint 3: Advanced Autonomy & Execution (Weeks 6-8)
*Goal: Make the bot truly autonomous, resilient, and self-improving.*

1.  **Vector Memory (PGVector):** 
    *   Implement trade memory. Before analyzing an asset, query PGVector for the last 3 times we traded it in the current regime and inject those outcomes into the LLM prompt.
2.  **Websocket Local-Stop Engine:** 
    *   Build the ultra-fast Python loop monitoring open positions via Websockets.
    *   Trigger trailing stops locally without waiting for the LLM/Orchestrator loop.
3.  **OMS Reconciliation:** 
    *   Implement the 60-second cron syncing PostgreSQL state with Bybit's actual open orders to catch partial fills or dropped connections.

---

## 5. Technical Implementation Guidelines

### 5.1 Confidence Calibration Logic (Concept)

```python
# src/risk/calibration_engine.py

class ConfidenceCalibrator:
    def __init__(self, window_size=100):
        self.window_size = window_size
        self.trade_history = [] # Loaded from PostgreSQL

    def calculate_multiplier(self) -> float:
        """
        Calculates the calibration multiplier based on recent performance.
        Returns a value between 0.5 and 1.5.
        """
        recent_trades = self.trade_history[-self.window_size:]
        if len(recent_trades) < 20:
            return 1.0 # Not enough data, trust the LLM

        # Calculate actual win rate
        wins = sum(1 for t in recent_trades if t['pnl'] > 0)
        actual_win_rate = wins / len(recent_trades)

        # Calculate average predicted confidence
        avg_predicted_conf = sum(t['llm_confidence'] for t in recent_trades) / len(recent_trades)

        if avg_predicted_conf == 0:
            return 1.0

        # Multiplier = Actual / Predicted
        # If LLM predicts 80% but wins 40%, multiplier is 0.5 (penalty)
        # If LLM predicts 50% but wins 70%, multiplier is 1.4 (boost)
        multiplier = actual_win_rate / avg_predicted_conf
        
        # Clamp to prevent extreme adjustments
        return max(0.5, min(1.5, multiplier))

    def calibrate_signal(self, llm_confidence: float) -> float:
        multiplier = self.calculate_multiplier()
        return llm_confidence * multiplier
```

### 5.2 Event-Driven Perp Backtester Requirements

The backtester (`src/backtest/perp_simulator.py`) must include:
1.  **Funding Fee Simulation:** Deduct/add funding fees every 8 hours based on historical funding rate data.
2.  **Slippage Model:** Apply a dynamic slippage penalty based on the order size relative to the 1-minute orderbook volume.
3.  **Fee Tier Simulation:** Apply maker/taker fees (e.g., 0.02% maker, 0.055% taker) based on the assumed order type.
4.  **Liquidation Check:** Continuously monitor margin ratio. If maintenance margin is breached, simulate forced liquidation at the bankruptcy price.

### 5.3 Vector Memory (RAG) Implementation

Using `pgvector` in PostgreSQL to give the LLM context of its own past mistakes/successes.

```python
# src/agents/memory_retriever.py

async def get_relevant_trade_memory(ticker: str, current_regime: str, pg_conn):
    """
    Retrieves the 3 most similar past trades for the given ticker and regime.
    """
    query = """
        SELECT trade_thesis, outcome, pnl_pct, reasoning 
        FROM trade_memory 
        WHERE ticker = $1 AND regime = $2
        ORDER BY embedding <-> $3 
        LIMIT 3;
    """
    # Generate embedding for current market context
    context_embedding = await generate_embedding(f"{ticker} in {current_regime} regime")
    
    results = await pg_conn.fetch(query, ticker, current_regime, context_embedding)
    
    memory_context = "Past similar trades:\n"
    for r in results:
        memory_context += f"- Thesis: {r['trade_thesis']} | Outcome: {r['outcome']} | PnL: {r['pnl_pct']}%\n"
        
    return memory_context
```

---

## 6. Conclusion

By adopting the **Crypto Separation**, **Confidence Calibration**, and **Dynamic Universe**, while strictly avoiding the latency traps of **Ensemble Execution** and **Multi-Exchange State Sync**, the Karsa system will evolve from a highly capable script into a statistically robust, operationally resilient, and truly autonomous crypto trading agent.

Execution should strictly follow the 3-sprint roadmap to ensure stability at each phase.