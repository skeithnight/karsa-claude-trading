# Karsa AI Trading System — Master Architecture & Design

**Version:** 2.0 (Unified Phase 1 & Phase 2)  
**Date:** 2026-07-02  
**Status:** Production-Ready Design  

> **⚠️ Scope Boundary Note:** 
> This document covers the complete system lifecycle. 
> **Phase 1** focuses on Signal Generation, Risk Profiles, and Entry Execution (Universe, LLM Agents, Position Sizing). 
> **Phase 2** focuses on the Trade Lifecycle, Active Position Management, Order Management System (OMS), and Cross-Market Capital Allocation.

---

## Table of Contents
1. [Phase 1: Signal Generation & Entry Logic](#phase-1-signal-generation--entry-logic)
   - [Risk Profile Manager](#1-risk-profile-manager)
   - [Dynamic Crypto Universe](#2-dynamic-crypto-universe)
   - [LLM Batching & Prompt Injection](#3-llm-batching--prompt-injection)
   - [Telegram & REST API](#4-telegram--rest-api)
   - [Observability (Metrics & Alerts)](#5-observability-metrics--alerts)
2. [Phase 2: Trade Lifecycle & Execution Engine](#phase-2-trade-lifecycle--execution-engine)
   - [Real-Time Websocket Data Layer](#6-real-time-websocket-data-layer)
   - [Active Position Management](#7-active-position-management)
   - [Order Management System (OMS)](#8-order-management-system-oms)
   - [Cross-Market Capital Allocation](#9-cross-market-capital-allocation)
3. [System Integration & Data Flow](#system-integration--data-flow)
4. [Infrastructure & Deployment](#infrastructure--deployment)

---

# Phase 1: Signal Generation & Entry Logic

## 1. Risk Profile Manager

A 3-tier risk profile system that dynamically controls confidence thresholds, position sizing, and universe size.

### Profile Parameters

| Parameter | Conservative 🛡️ | Semi-Aggressive ⚖️ | Aggressive 🔥 |
|-----------|:---:|:---:|:---:|
| Min Confidence | 70% | 50% | 35% |
| Max Position Size | 1% | 2.5% | 5% |
| Stop Loss (ATR) | 1.0x | 1.5x | 2.5x |
| Take Profit (ATR) | 2.0x | 3.0x | 4.0x |
| Max Open Positions | 2 | 4 | 6 |
| Max Daily Trades | 3 | 8 | 15 |
| **Min 24h Volume (Universe)** | **$100M** | **$50M** | **$20M** |
| Universe Target Size | 8 coins | 12 coins | 15 coins |
| Size Multiplier | 0.8x | 1.0x | 1.3x |

**Hard Limits (Immutable across all profiles):**
- Max absolute position size: 10% of equity
- Daily loss limit: 5%
- Kill switch auto-trigger: 1.5% daily loss
- Cooldown: 5 minutes between profile changes per user.

### Auto-Refresh Mechanism
When a profile is changed via `/setmode`, the `RiskProfileManager` publishes an event to `karsa:events:profile_changed`. The `UniverseEngine` subscribes to this channel and **immediately regenerates the universe** to match the new profile's target size and volume requirements, bypassing the 4-hour TTL.

---

## 2. Dynamic Crypto Universe

Replaces the static 14-coin list with a scored, ranked selection refreshed every 4 hours (or on profile change).

### Pipeline & Profile-Aware Filtering
*Crucial Fix: The universe fetch is now profile-aware to prevent fetching hundreds of illiquid coins only to reject them later.*

1. **Fetch:** Bybit API → Fetch all USDT Perpetuals.
2. **Profile-Aware Liquidity Filter:** 
   - Reads `min_volume_24h_usd` from the active Risk Profile (e.g., $100M for Conservative).
   - *Fallback:* If the strict filter returns < 5 coins, fallback to an absolute floor of **$5M** to ensure the bot has assets to scan.
3. **Score (0-100):**
   - Volume (40%): log-scale, $100M+ = full score.
   - Momentum (30%): absolute 24h price change, 5%+ = full score.
   - Trend/Turnover (30%): volume/OI ratio, >1.0 = full score.
4. **Rank & Cache:** Select top $N$ coins (based on profile), cache in Redis (`karsa:state:crypto_universe`, 4h TTL).
5. **Core Universe:** BTC, ETH, SOL, BNB, XRP are *always* included regardless of score.

---

## 3. LLM Batching & Prompt Injection

To prevent API cost explosion in Aggressive mode (15 coins), the `CryptoAnalyst` agent uses **Batched Prompting**.

- **Grouping:** The Orchestrator groups the universe into batches of 5 coins.
- **Execution:** 15 coins = 3 LLM API calls (not 15).
- **Prompt Injection:** The system prompt includes profile-specific guidance:
  - *Conservative:* "Require multiple confirming indicators. Be highly skeptical. Reject if uncertain."
  - *Aggressive:* "Accept lower probability setups if risk/reward is asymmetric. ⚠️ Do NOT artificially inflate confidence scores."
- **Output:** The LLM returns a JSON array of 5 structured analyses.

---

## 4. Telegram & REST API

### Telegram Commands
- `/dashboard` — System vitals, market state, risk profile, universe summary.
- `/control` — Inline buttons for Risk Profile switching, Universe Refresh, Emergency Kill/Sell All.
- `/mode` — Shows current profile parameters and current universe.
- `/setmode <profile>` — Switch profile (enforces 5m cooldown).
- `/universe` — Paginated table of current universe (coin | score | volume | 24h change).
- `/refresh_universe` — Force regenerates the universe immediately.

### REST API
**Base URL:** `http://localhost:8000`  
**Security Note:** *Requires Bearer Token authentication if exposed outside localhost via reverse proxy.*

```
GET  /api/v1/risk-profile          → current profile config
PUT  /api/v1/risk-profile          → switch profile (body: {profile, reason})
GET  /api/v1/risk-profile/history  → audit log (last 100 changes)
GET  /api/v1/universe              → current universe list
POST /api/v1/universe/refresh      → force regenerate
GET  /api/v1/universe/scores       → universe with scoring details
GET  /metrics                      → Prometheus metrics
```

---

## 5. Observability (Metrics & Alerts)

### Prometheus Metrics
| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `karsa_risk_profile_changes_total` | Counter | from, to | Profile change events |
| `karsa_active_risk_profile` | Gauge | — | Current profile (0/1/2) |
| `karsa_signal_rejections_total` | Counter | profile, reason | Signals rejected |
| `karsa_position_size_pct` | Histogram | profile | Position size distribution |
| `karsa_universe_size` | Gauge | — | Coins in universe |
| `karsa_universe_refresh_duration_seconds` | Histogram | — | Refresh duration |

### Prometheus Alerts
| Alert | Condition | Severity |
|-------|-----------|----------|
| RapidProfileSwitching | >10 changes in 5min | warning |
| AggressiveModeActive | >1 hour continuous | info |
| HighRejectionRate | >80% rejection for 30min | warning |
| **PositionSizeExceeded** | **Any single trade > 10%** | **critical** |
| UniverseRefreshFailed | Failure in 4h window | warning |
| UniverseTooSmall | <5 coins for 30min | warning |

---

# Phase 2: Trade Lifecycle & Execution Engine

*This section covers the systems required to manage trades AFTER they are opened.*

## 6. Real-Time Websocket Data Layer

The Orchestrator and LLM run on schedules (minutes/hours). Stop-losses require millisecond reactions.

- **Websocket Manager:** Dedicated Python service maintaining persistent connections to Bybit/Binance Websockets.
- **Scope:** Only subscribes to the `trade` and `kline` streams for **currently open positions** and **pending limit orders**.
- **Redis Real-Time Cache:** Updates `karsa:realtime:price:{ticker}` instantly.
- **Local Stop-Loss Engine:** A lightweight, ultra-fast loop that listens to the WS stream. If `realtime_price <= stop_loss`, it immediately fires a Market Close order, bypassing the slow LLM/Orchestrator loop.

## 7. Active Position Management

Dynamic trade management logic executed by the `TradeLifecycleEngine` (runs every 1 min or triggered by Websocket events).

1. **Scale-Out Logic:** 
   - If Price reaches Entry + 1R (1x risk), sell 50% of the position.
   - Move the Stop Loss for the remaining 50% to Breakeven (Entry Price).
2. **Trailing Stops:** 
   - Dynamically adjust the stop loss based on a Chandelier Exit or ATR multiplier as the price moves favorably.
3. **Time-Based Exits:** 
   - If a trade age > 48 hours and neither TP nor SL is hit, close at market to free up capital (prevents "zombie" positions).

## 8. Order Management System (OMS)

Handles the messy reality of exchange executions.

### Order State Machine
Tracks every order through its lifecycle:
`[NEW]` → `[SUBMITTED]` → `[PARTIAL]` → `[FILLED]` / `[CANCELLED]` / `[REJECTED]`

### Exchange Reconciliation Cron
Runs every 60 seconds to ensure internal state matches reality.
1. Fetch actual open orders & positions from Exchange REST API.
2. Compare with PostgreSQL internal state.
3. **If mismatch:** 
   - Log anomaly to PostgreSQL.
   - Alert Admin via Telegram.
   - Auto-sync internal state to match Exchange (Exchange is the ultimate Source of Truth).
4. **Stuck Order Cleanup:** Automatically cancel limit orders that have been open for > 15 minutes without filling.

## 9. Cross-Market Capital Allocation

The Karsa system trades IDX, US Equities, ETFs, and Crypto. A global allocator prevents one market from draining the entire account.

### Global Capital Guard
Before the Execution Engine sends *any* order, it queries the `PortfolioAllocator`.

- **Total Equity:** $100,000
- **Hard Sub-Account Limits:**
  - Crypto Allocation Limit: 30% ($30,000 max margin used)
  - US Equities Limit: 40% ($40,000)
  - ETF Limit: 20% ($20,000)
  - IDX Limit: 10% ($10,000)
- **Global Drawdown Limit:** If the *entire* portfolio drops by 5% across all markets, the global kill switch triggers, halting ALL markets regardless of individual performance.

---

# System Integration & Data Flow

```text
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           CROSS-MARKET PORTFOLIO ALLOCATOR                      │
│  Checks global equity limits (Crypto 30%, US 40%) before ANY order is sent.     │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │ (Approve/Reject Order)
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         PHASE 1: SIGNAL & ENTRY ENGINE                          │
│                                                                                 │
│  [Bybit API] ──> UniverseEngine (Profile-Aware Filter) ──> Redis (Universe)     │
│                                                         │                       │
│  [RiskProfile] ──> Orchestrator ──> CryptoAnalyst (LLM Batched Prompts)         │
│                       │                                                     │
│                       ▼                                                     │
│               CryptoRiskMgr (Position Sizing, SL/TP Calc) ──> Execution Engine│
└───────────────────────────────────────────┬─────────────────────────────────────┘
                                            │ (Send Order)
                                            ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         PHASE 2: TRADE LIFECYCLE ENGINE                         │
│                                                                                 │
│  ┌─────────────────────────┐      ┌─────────────────────────────────────────┐  │
│  │ Websocket Manager       │      │ Order Management System (OMS)           │  │
│  │ • Streams real-time     │      │ • State Machine (NEW->PARTIAL->FILLED)  │  │
│  │   price for open pos.   │      │ • Reconciliation Cron (60s sync)        │  │
│  │ • Local SL/TP Trigger   │      │ • Stuck Order Cleanup                   │  │
│  └───────────┬─────────────┘      └─────────────────────────────────────────┘  │
│              │                                                                  │
│              ▼                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ Active Position Management                                              │   │
│  │ • Scale-Out (Sell 50% at 1R, move SL to Breakeven)                      │   │
│  │ • Trailing Stops (Chandelier/ATR)                                       │   │
│  │ • Time Exits (Close if >48h old)                                        │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

# Infrastructure & Deployment

## Database Migrations (Phase 1)

```sql
-- 1. Risk Profile Audit
CREATE TABLE risk_profile_audit (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    previous_profile VARCHAR(20) NOT NULL,
    new_profile VARCHAR(20) NOT NULL,
    changed_by VARCHAR(100) NOT NULL,
    reason TEXT
);
CREATE INDEX idx_risk_profile_audit_ts ON risk_profile_audit(timestamp DESC);

-- 2. Universe History
CREATE TABLE universe_history (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    universe_json JSONB NOT NULL,
    selection_criteria JSONB
);
CREATE INDEX idx_universe_history_ts ON universe_history(timestamp DESC);

-- 3. Enhance Trade Signals
ALTER TABLE trade_signals 
ADD COLUMN IF NOT EXISTS risk_profile_at_generation VARCHAR(20) NOT NULL DEFAULT 'conservative',
ADD COLUMN IF NOT EXISTS position_size_calculated DECIMAL(18, 8);
```

## Deployment Steps

```bash
# 1. Rebuild containers
docker compose up -d --build

# 2. Run DB migrations
docker exec karsa-postgres psql -U karsa -d karsa -f /app/db/migrations/add_risk_profile.sql
docker exec karsa-postgres psql -U karsa -d karsa -f /app/db/migrations/add_universe_history.sql

# 3. Verify API & Metrics
curl localhost:8000/api/v1/risk-profile
curl localhost:8000/api/v1/universe
curl localhost:8000/metrics | grep karsa_

# 4. Telegram Smoke Test
# /mode → shows Conservative (default)
# /setmode aggressive → switches & triggers auto-refresh of universe
# /dashboard → shows profile + universe
```

## File Structure Additions

### New Files (Phase 1)
- `src/risk/profile_manager.py` — RiskProfileManager, validation, sizing
- `src/advisory/universe_scorer.py` — Scoring/ranking pure functions
- `src/metrics/crypto_metrics.py` — Prometheus metric definitions
- `src/api/routes.py` — REST API endpoints

### New Files (Phase 2)
- `src/execution/websocket_manager.py` — Real-time exchange streams
- `src/execution/position_lifecycle.py` — Trailing stops, scale-outs, time exits
- `src/execution/oms.py` — Order state machine & reconciliation cron
- `src/risk/portfolio_allocator.py` — Cross-market capital limits

### Modified Files
- `src/advisory/crypto_universe.py` — Made profile-aware, added auto-refresh listener
- `src/agents/crypto_analyst.py` — Implemented batched prompting (5 coins per call)
- `src/agents/orchestrator.py` — Wired profile_manager, universe_engine, and portfolio_allocator
- `src/bot/crypto_handlers.py` — Added /dashboard, /control, /universe pagination
