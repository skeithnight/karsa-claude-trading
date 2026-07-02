# Dynamic Crypto Universe - Integration Architecture

**Document Version:** 1.0  
**Status:** Draft  
**Last Updated:** 2026-07-02  
**System:** Karsa AI Trading Platform  

---

## Table of Contents
1. [High-Level System Integration Diagram](#1-high-level-system-integration-diagram)
2. [Data Flow Sequence Diagram](#2-data-flow-sequence-diagram)
3. [Component Integration Matrix](#3-component-integration-matrix)
4. [Redis Key Structure](#4-redis-key-structure)
5. [PostgreSQL Schema Relationships](#5-postgresql-schema-relationships)
6. [Failure Modes & Recovery Strategies](#6-failure-modes--recovery-strategies)
7. [Monitoring Dashboard Layout](#7-monitoring-dashboard-layout)
8. [Quick Start Integration Checklist](#8-quick-start-integration-checklist)

---

## 1. High-Level System Integration Diagram

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                         EXTERNAL DATA SOURCES                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │   Binance    │  │   Bybit      │  │  CoinGecko   │  │  TradingView │   │
│  │   API        │  │  API         │  │  API         │  │  Webhooks    │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
└─────────┼──────────────────┼──────────────────┼──────────────────┼─────────┘
          │                  │                  │                  │
          └──────────────────┴──────────────────┴──────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    DYNAMIC CRYPTO UNIVERSE ENGINE                            │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │  Universe Generator (Python) - Runs every 4 hours                     │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │ │
│  │  │ Fetch 200+  │→ │ Liquidity   │→ │ Trend       │→ │ Score &     │ │ │
│  │  │ Tickers     │  │ Filter      │  │ Filter      │  │ Rank        │ │ │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └──────┬──────┘ │ │
│  │                                                             │        │ │
│  │  ┌──────────────────────────────────────────────────────┐   │        │ │
│  │  │ Final Selection: Top 8-12 Coins                      │←──┘        │ │
│  │  │ • BTCUSDT, ETHUSDT (always included)                 │            │ │
│  │  │ • SOLUSDT, AVAXUSDT, LINKUSDT (dynamic)             │            │ │
│  │  └──────────────────────────────────────────────────────┘            │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                   │                                         │
│                                   │ Write to Redis                          │
│                                   ▼                                         │
│                    ┌─────────────────────────────┐                         │
│                    │ Redis: karsa:state:         │                         │
│                    │ crypto_universe             │                         │
│                    │ TTL: 4 hours                │                         │
│                    └──────────────┬──────────────┘                         │
└───────────────────────────────────┼─────────────────────────────────────────┘
                                    │
                                    │
┌───────────────────────────────────┼─────────────────────────────────────────┐
│                         CORE ORCHESTRATION LAYER                            │
│                                   │                                         │
│  ┌────────────────────────────────▼──────────────────────────────────────┐ │
│  │                    Orchestrator Service                                │ │
│  │                                                                        │ │
│  │  ┌────────────────────────────────────────────────────────────────┐  │ │
│  │  │ 1. Read Universe from Redis                                    │  │ │
│  │  │    → ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"...]         │  │ │
│  │  └────────────────────────────────────────────────────────────────┘  │ │
│  │                            │                                           │ │
│  │  ┌────────────────────────▼────────────────────────────────────────┐  │ │
│  │  │ 2. Check Risk Profile (Conservative/Semi/Aggressive)            │  │ │
│  │  │    → Determines max positions, confidence threshold             │  │ │
│  │  └────────────────────────────────────────────────────────────────┘  │ │
│  │                            │                                           │ │
│  │  ┌────────────────────────▼────────────────────────────────────────┐  │ │
│  │  │ 3. BTC Macro Check                                              │  │ │
│  │  │    → If BTC bearish: veto altcoin LONGs                         │  │ │
│  │  └────────────────────────────────────────────────────────────────┘  │ │
│  │                            │                                           │ │
│  │  ┌────────────────────────▼────────────────────────────────────────┐  │ │
│  │  │ 4. Parallel Analysis (asyncio.gather)                           │  │ │
│  │  │    → Spawn tasks for each coin in universe                      │  │ │
│  │  └────────────────────────────────────────────────────────────────┘  │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                            │                   │                              │
└────────────────────────────┼───────────────────┼──────────────────────────────┘
                             │                   │
┌────────────────────────────▼──────┐   ┌────────▼──────────────────────────────┐
│     LLM AGENT LAYER               │   │     RISK & EXECUTION LAYER            │
│                                   │   │                                       │
│  ┌─────────────────────────────┐  │   │  ┌─────────────────────────────────┐ │
│  │ Crypto Analyst Agent        │  │   │  │ Risk Profile Manager            │ │
│  │                             │  │   │  │                                 │ │
│  │ Input:                      │  │   │  │ Validates:                      │ │
│  │ - Ticker data (OHLCV)       │  │   │  │ • Confidence threshold          │ │
│  │ - Technical indicators      │  │   │  │ • Position sizing               │ │
│  │ - Market regime             │  │   │  │ • Stop loss calculation         │ │
│  │ - Risk profile context      │  │   │  │ • Max daily trades              │ │
│  │                             │  │   │  │                                 │ │
│  │ Output:                     │  │   │  └────────────┬────────────────────┘ │
│  │ - Signal (LONG/SHORT/HOLD)  │  │   │               │                      │
│  │ - Confidence score (0-1)    │  │   │               ▼                      │
│  │ - Entry/Exit zones          │  │   │  ┌─────────────────────────────────┐ │
│  │ - Reasoning (thesis)        │  │   │  │ Position Sizer                  │ │
│  └─────────────────────────────┘  │   │  │                                 │ │
│                                   │   │  │ Calculates:                     │ │
│  ┌─────────────────────────────┐  │   │  │ • Quantity based on risk %      │ │
│  │ Prompt Builder              │  │   │  │ • ATR-based stop loss           │ │
│  │                             │  │   │  │ • Take profit levels            │ │
│  │ Injects risk profile into   │  │   │  │ • Risk/reward ratio             │ │
│  │ system prompt               │  │   │  └────────────┬────────────────────┘ │
│  └─────────────────────────────┘  │   │               │                      │
│                                   │   │               ▼                      │
│  ┌─────────────────────────────┐  │   │  ┌─────────────────────────────────┐ │
│  │ Batch Processing            │  │   │  │ Order Validator                 │ │
│  │                             │  │   │  │                                 │ │
│  │ Groups 5-8 coins into       │  │   │  │ Pre-flight checks:              │ │
│  │ single LLM prompt to save   │  │   │  │ • Liquidity check               │ │
│  │ API costs                   │  │   │  │ • Correlation limit             │ │
│  └─────────────────────────────┘  │   │  │ • Open position count           │ │
└───────────────────────────────────┘   │  │ • Daily loss limit              │ │
                                        │  └────────────┬────────────────────┘ │
                                        │               │                      │
                                        │               ▼                      │
                                        │  ┌─────────────────────────────────┐ │
                                        │  │ Execution Engine                │ │
                                        │  │                                 │ │
                                        │  │ • Sends orders to exchange      │ │
                                        │  │ • Idempotency keys              │ │
                                        │  │ • Order status tracking         │ │
                                        │  └────────────┬────────────────────┘ │
                                        └───────────────┼──────────────────────┘
                                                        │
┌───────────────────────────────────────────────────────┼──────────────────────┐
│                      STATE & AUDIT LAYER              │                      │
│                                                       │                      │
│  ┌───────────────────────────────────────────────────▼────────────────┐    │
│  │ Redis (Real-time State)                                            │    │
│  │                                                                    │    │
│  │ • karsa:state:crypto_universe (Top 8-12 coins)                    │    │
│  │ • karsa:state:risk_profile (conservative/semi_aggressive/agg)    │    │
│  │ • karsa:state:kill_switch (true/false)                            │    │
│  │ • karsa:cache:indicators:* (OHLCV data cache)                     │    │
│  │ • karsa:pubsub:signals (Real-time signal broadcast)               │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │ PostgreSQL (Immutable Audit Trail)                                  │  │
│  │                                                                     │  │
│  │ • trade_signals                                                     │  │
│  │   - id, ticker, signal_type, confidence, risk_profile              │  │
│  │   - created_at, executed_at, status                                │  │
│  │                                                                     │  │
│  │ • executed_trades                                                   │  │
│  │   - id, signal_id, ticker, side, quantity, entry_price             │  │
│  │   - stop_loss, take_profit, pnl, exit_price                        │  │
│  │                                                                     │  │
│  │ • risk_profile_audit                                                │  │
│  │   - previous_profile, new_profile, changed_by, reason              │  │
│  │                                                                     │  │
│  │ • universe_history                                                  │  │
│  │   - timestamp, universe_json, selection_criteria                   │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼───────────────────────────────────────────────┐
│                      TELEGRAM BOT (HITL INTERFACE)                         │
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │ Real-time Notifications                                              │ │
│  │                                                                      │ │
│  │ 🔍 PENDING: SOLUSDT LONG (Conf: 65%)                                │ │
│  │    Thesis: Trend convergence with volume spike...                   │ │
│  │    [Approve] [Reject] [Modify]                                      │ │
│  │                                                                      │ │
│  │ ✅ EXECUTED: Bought 2.5 SOL @ $145.20                               │ │
│  │    Stop: $138.50 | Target: $158.00                                  │ │
│  │                                                                      │ │
│  │ 📊 UNIVERSE UPDATED: Now scanning 10 coins                          │ │
│  │    BTC, ETH, SOL, AVAX, LINK, NEAR, DOT, ADA, XRP, BNB             │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │ Commands                                                             │ │
│  │ /mode | /setmode | /universe | /refresh_universe | /status          │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Data Flow Sequence Diagram

```text
┌─────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────┐  ┌──────────┐
│ Exchange│  │   Universe   │  │   Redis      │  │Orchestrator│  │ LLM Agent│
│  APIs   │  │   Generator  │  │   Cache      │  │ Service   │  │          │
└────┬────┘  └──────┬───────┘  └──────┬───────┘  └─────┬─────┘  └────┬─────┘
     │              │                  │                │              │
     │ 1. Fetch 200+ tickers           │                │              │
     │─────────────>│                  │                │              │
     │              │                  │                │              │
     │              │ 2. Filter & Rank │                │              │
     │              │─────────┐        │                │              │
     │              │         │        │                │              │
     │              │<────────┘        │                │              │
     │              │                  │                │              │
     │              │ 3. Write Universe│                │              │
     │              │─────────────────>│                │              │
     │              │                  │                │              │
     │              │                  │ 4. Schedule    │              │
     │              │                  │───────────────>│              │
     │              │                  │                │              │
     │              │                  │ 5. Read Universe              │
     │              │                  │<───────────────│              │
     │              │                  │                │              │
     │              │                  │ 6. Check Risk Profile         │
     │              │                  │───────────────┐│              │
     │              │                  │               ││              │
     │              │                  │<──────────────┘│              │
     │              │                  │                │              │
     │              │                  │ 7. BTC Macro Check            │
     │              │                  │                │              │
     │              │                  │ 8. Batch coins for LLM        │
     │              │                  │──────────────────────────────>│
     │              │                  │                │              │
     │              │                  │                │ 9. Analyze   │
     │              │                  │                │──────────┐   │
     │              │                  │                │          │   │
     │              │                  │                │<─────────┘   │
     │              │                  │                │              │
     │              │                  │ 10. Return Signals            │
     │              │                  │<──────────────────────────────│
     │              │                  │                │              │
     │              │                  │ 11. Validate vs Risk Profile  │
     │              │                  │                │              │
     │              │                  │ 12. Calculate Position Size   │
     │              │                  │                │              │
     │              │                  │ 13. Log to PostgreSQL         │
     │              │                  │──────────────┐ │              │
     │              │                  │              │ │              │
     │              │                  │<─────────────┘ │              │
     │              │                  │                │              │
     │              │                  │ 14. Send to Telegram          │
     │              │                  │─────────────────────────────┐ │
     │              │                  │                             │ │
     │              │                  │                             ▼ ▼
     │              │                  │                    ┌──────────────┐
     │              │                  │                    │   Telegram   │
     │              │                  │                    │     Bot      │
     │              │                  │                    │              │
     │              │                  │                    │ 🔍 PENDING   │
     │              │                  │                    │ SOLUSDT LONG │
     │              │                  │                    │ Conf: 65%    │
     │              │                  │                    │ [Approve]    │
     │              │                  │                    └──────────────┘
     │              │                  │                             │
     │              │                  │ 15. User Approves           │
     │              │                  │<────────────────────────────┘
     │              │                  │                             │
     │              │                  │ 16. Execute Order           │
     │              │                  │──────────────┐              │
     │              │                  │              │              │
     │              │                  │<─────────────┘              │
     │              │                  │                             │
     │              │                  │ 17. Confirm Execution       │
     │              │                  │───────────────────────────> │
```

---

## 3. Component Integration Matrix

| Component | Integration Point | Data Flow | Frequency | Criticality |
|-----------|------------------|-----------|-----------|-------------|
| **Exchange APIs** | Universe Generator | Raw ticker data (OHLCV, volume) | Every 5 min | 🔴 Critical |
| **Redis** | Universe Cache | JSON array of selected coins | Every 4 hours | 🔴 Critical |
| **Orchestrator** | Universe Reader | Reads universe from Redis | Every analysis cycle | 🔴 Critical |
| **Risk Profile Manager** | Validation Layer | Filters signals based on profile | Per signal | 🔴 Critical |
| **LLM Agent** | Analysis Engine | Receives batched coin data | Per cycle | 🟡 High |
| **PostgreSQL** | Audit Trail | Logs universe history, signals | Per event | 🟢 Medium |
| **Telegram Bot** | User Interface | Shows universe, pending signals | Real-time | 🟡 High |
| **BTC Macro Filter** | Gatekeeper | Veto altcoin LONGs if BTC bearish | Per signal | 🔴 Critical |

---

## 4. Redis Key Structure

```text
┌─────────────────────────────────────────────────────────────────┐
│                      REDIS DATA STRUCTURES                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  karsa:state:crypto_universe (String)                           │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │ ["BTCUSDT","ETHUSDT","SOLUSDT","AVAXUSDT","LINKUSDT",    │ │
│  │  "NEARUSDT","DOTUSDT","ADAUSDT"]                          │ │
│  └───────────────────────────────────────────────────────────┘ │
│  TTL: 14400 seconds (4 hours)                                  │
│                                                                  │
│  karsa:state:risk_profile (String)                             │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │ "semi_aggressive"                                          │ │
│  └───────────────────────────────────────────────────────────┘ │
│  TTL: None (persistent)                                        │
│                                                                  │
│  karsa:cache:indicators:{ticker} (Hash)                        │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │ {                                                           │ │
│  │   "rsi": "58.3",                                          │ │
│  │   "macd": "1.25",                                         │ │
│  │   "ema_50": "142.50",                                     │ │
│  │   "atr": "5.20",                                          │ │
│  │   "volume_24h": "125000000"                               │ │
│  │ }                                                           │ │
│  └───────────────────────────────────────────────────────────┘ │
│  TTL: 3600 seconds (1 hour)                                    │
│                                                                  │
│  karsa:pubsub:signals (Pub/Sub Channel)                        │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │ {                                                           │ │
│  │   "ticker": "SOLUSDT",                                    │ │
│  │   "signal": "LONG",                                       │ │
│  │   "confidence": 0.65,                                     │ │
│  │   "timestamp": "2026-07-02T10:30:00Z"                     │ │
│  │ }                                                           │ │
│  └───────────────────────────────────────────────────────────┘ │
│                                                                  │
│  karsa:state:kill_switch (String)                              │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │ "false"                                                    │ │
│  └───────────────────────────────────────────────────────────┘ │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. PostgreSQL Schema Relationships

```text
┌─────────────────────────────────────────────────────────────────┐
│                    POSTGRESQL SCHEMA                             │
└─────────────────────────────────────────────────────────────────┘

┌───────────────────────┐
│   universe_history    │
├───────────────────────┤
│ id (PK)               │
│ timestamp             │
│ universe_json (JSONB) │◄─── Stores full coin list
│ selection_criteria    │
└───────────┬───────────┘
            │
            │ 1:M
            │
┌───────────▼───────────┐       ┌───────────────────────┐
│   trade_signals       │       │  risk_profile_audit   │
├───────────────────────┤       ├───────────────────────┤
│ id (PK)               │       │ id (PK)               │
│ ticker                │       │ timestamp             │
│ signal_type           │       │ previous_profile      │
│ confidence            │       │ new_profile           │
│ risk_profile_snapshot │       │ changed_by            │
│ status                │       │ reason                │
│ created_at            │       └───────────────────────┘
│ executed_at           │
└───────────┬───────────┘
            │
            │ 1:1
            │
┌───────────▼───────────┐
│   executed_trades     │
├───────────────────────┤
│ id (PK)               │
│ signal_id (FK)        │
│ ticker                │
│ side (LONG/SHORT)     │
│ quantity              │
│ entry_price           │
│ stop_loss             │
│ take_profit           │
│ exit_price            │
│ pnl                   │
│ pnl_pct               │
│ executed_at           │
└───────────────────────┘
```

---

## 6. Failure Modes & Recovery Strategies

```text
┌─────────────────────────────────────────────────────────────────┐
│              FAILURE MODES & RECOVERY STRATEGIES                 │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Scenario 1: Universe Generator Fails                        │
├─────────────────────────────────────────────────────────────┤
│ Symptom:  Redis key expired, no new universe                │
│ Impact:   Orchestrator can't find coins to analyze          │
│ Recovery:                                                   │
│   1. Check Redis TTL → if expired                           │
│   2. Trigger manual refresh via /refresh_universe           │
│   3. Fallback to hardcoded ["BTCUSDT", "ETHUSDT"]          │
│   4. Log alert to Telegram admin                            │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Scenario 2: Exchange API Rate Limited                       │
├─────────────────────────────────────────────────────────────┤
│ Symptom:  429 Too Many Requests from Binance/Bybit          │
│ Impact:   Universe generator can't fetch tickers            │
│ Recovery:                                                   │
│   1. Implement exponential backoff (retry after 2s, 4s...) │
│   2. Use cached data from Redis if available                │
│   3. Reduce universe refresh frequency (4h → 6h)            │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Scenario 3: LLM API Timeout                                 │
├─────────────────────────────────────────────────────────────┤
│ Symptom:  Claude/DeepSeek doesn't respond in 30s            │
│ Impact:   No signals generated for universe coins           │
│ Recovery:                                                   │
│   1. Timeout after 30s, retry once                          │
│   2. Process remaining coins, skip failed batch             │
│   3. Log to PostgreSQL for audit                            │
│   4. Send partial results to Telegram                       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Scenario 4: Redis Connection Lost                           │
├─────────────────────────────────────────────────────────────┤
│ Symptom:  Can't read/write universe or risk profile         │
│ Impact:   Complete system paralysis                         │
│ Recovery:                                                   │
│   1. Orchestrator detects connection error                  │
│   2. Activate kill switch (stop all trading)                │
│   3. Alert admin via Telegram                               │
│   4. Wait for Redis reconnection                            │
│   5. Manual restart required                                │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Scenario 5: PostgreSQL Write Fails                          │
├─────────────────────────────────────────────────────────────┤
│ Symptom:  Can't log signals or trades                       │
│ Impact:   Audit trail broken, compliance risk               │
│ Recovery:                                                   │
│   1. Queue failed writes in memory (max 100)                │
│   2. Retry every 30 seconds                                 │
│   3. If queue full → pause trading                          │
│   4. Alert admin immediately                                │
└─────────────────────────────────────────────────────────────┘
```

---

## 7. Monitoring Dashboard Layout

```text
┌─────────────────────────────────────────────────────────────────┐
│              GRAFANA DASHBOARD: CRYPTO UNIVERSE                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────┐ ┌─────────────────────────┐
│ Current Universe        │ │ Universe Refresh Status   │
│                         │ │                           │
│ 1. BTCUSDT   ✅         │ │ Last Refresh: 2h ago      │
│ 2. ETHUSDT   ✅         │ │ Next Refresh: 2h          │
│ 3. SOLUSDT   ✅         │ │ Success Rate: 98.5%       │
│ 4. AVAXUSDT  ✅         │ │                           │
│ 5. LINKUSDT  ✅         │ │ [██████████] 100%         │
│ 6. NEARUSDT  ✅         │ │                           │
│ 7. DOTUSDT   ✅         │ │                           │
│ 8. ADAUSDT   ✅         │ │                           │
└─────────────────────────┘ └─────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Signal Generation by Coin (24h)                              │
│                                                              │
│ BTCUSDT  ████████████████████ 12 signals (3 executed)       │
│ ETHUSDT  ██████████████████ 10 signals (2 executed)         │
│ SOLUSDT  ████████████████ 8 signals (4 executed)            │
│ AVAXUSDT ██████████ 5 signals (1 executed)                   │
│ LINKUSDT ████████ 4 signals (0 executed)                     │
│ Others   ██████ 3 signals (0 executed)                       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────┐ ┌─────────────────────────┐
│ Universe Turnover Rate  │ │ Coin Performance (24h)  │
│                         │ │                           │
│ Avg coins/day: 8.5      │ │ SOLUSDT   +5.2%  ✅      │
│ New coins/day: 2.3      │ │ AVAXUSDT  +3.1%  ✅      │
│ Retention: 73%          │ │ LINKUSDT  -1.2%  ⚠️      │
│                         │ │ NEARUSDT  +8.5%  ✅      │
│ [Top 8 coins stable]    │ │ DOTUSDT   -0.5%  ⚠️      │
└─────────────────────────┘ └─────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ API Health                                                   │
│                                                              │
│ Binance API    [██████████] 100%  Latency: 120ms            │
│ Redis          [██████████] 100%  Latency: 5ms              │
│ PostgreSQL     [██████████] 100%  Latency: 15ms             │
│ Claude API     [████████░░] 80%   Latency: 2.3s             │
│ Telegram Bot   [██████████] 100%  Latency: 200ms            │
└─────────────────────────────────────────────────────────────┘
```

---

## 8. Quick Start Integration Checklist

```bash
# Step 1: Verify Redis connectivity
redis-cli PING
# Expected: PONG

# Step 2: Initialize universe manually
python src/universe/generate_universe.py --force
# Expected: "Universe generated: 8 coins"

# Step 3: Verify Redis key exists
redis-cli GET karsa:state:crypto_universe
# Expected: JSON array of coins

# Step 4: Check orchestrator logs
docker logs karsa-orchestrator --tail 50
# Look for: "Reading universe from Redis..."

# Step 5: Test Telegram commands
# Send in Telegram: /universe
# Expected: List of 8 coins being scanned

# Step 6: Monitor first analysis cycle
docker logs karsa-orchestrator -f
# Look for: "Analyzing BTCUSDT...", "Analyzing ETHUSDT..."

# Step 7: Verify signals in PostgreSQL
docker exec karsa-postgres psql -U karsa -d karsa \
  -c "SELECT ticker, signal_type, confidence FROM trade_signals ORDER BY created_at DESC LIMIT 5;"

# Step 8: Check Telegram for pending signals
# Expected: 🔍 PENDING messages with coin tickers
```

---