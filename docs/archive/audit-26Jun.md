# Investment Portfolio Tracker & Analyst — Karsa Audit

**Date:** 26 June 2026
**Persona:** Long-term investment trader — tracks portfolio, monitors positions, receives market analysis on holdings
**Goal:** Manual portfolio entry → live market analysis per holding → portfolio-level insights (sector exposure, risk, rebalancing signals)
**Pivot:** Remove trade execution and Stockbit API. Karsa becomes a **portfolio-aware investment analyst**, not an auto-trader.
**Last updated:** 26 Jun 2026

---

## Production Status (26 Jun 2026 03:40 UTC)

| Container | Status | Uptime | Notes |
|---|---|---|---|
| `karsa-orchestrator` | ✅ Up (healthy) | 14 min | Scheduler running, 5 jobs registered (2 dead: scan IDX/US) |
| `karsa-telegram-bot` | ✅ Up (healthy) | 14 min | Polling mode, port 8443. `/scan` broken (JSON parse) |
| `karsa-postgres` | ✅ Up (healthy) | 13 hours | 7 tables, mostly empty |
| `karsa-redis` | ✅ Up (healthy) | 13 hours | Pub/sub wired, no listeners |
| `karsa-tradingview-mcp` | ✅ Up | 11 hours | Alpine stub — market data comes from Python `tradingview_ta` |

**All containers healthy.** Orchestrator scheduler only runs `Expire Stale Approvals` job on schedule (5-min interval). IDX and US scan jobs only fire during market hours (cron-restricted), so they're idle outside those windows. `/scan` command completes LLM analysis in ~14s but Telegram shows "0/100, No clear setup" due to JSON markdown-fence parse bug in `base.py:_extract_response()`.

---

## Pivot: What Changes

### REMOVING (dead weight for portfolio tracker)

| Component | Files | Why remove |
|---|---|---|
| **Stockbit API** | `src/data/idx_adapter.py` | Broken — returns `InvalidParameter` on HTTP 200. Foreign flow + ARA data unavailable. Not needed for portfolio tracking. |
| **IDX Broker** | `src/execution/idx_broker.py` | Scaffold — placeholder URLs, no real broker connection. Portfolio tracker doesn't execute trades. |
| **US Broker** | `src/execution/us_broker.py` | Same — Alpaca placeholder. Not needed. |
| **Broker Base** | `src/execution/base.py` | Abstract interface for removed brokers. |
| **Approval Manager** | `src/bot/approval.py` | HITL trade approval flow — approve/reject buttons. No trades to approve. |
| **Trade signals pipeline** | `signals` table, `pending_approvals` table, `trades` table | Signal → approval → execution flow. Replaced by portfolio analysis. |
| **Risk Manager agent** | `src/agents/risk_manager.py` | Validates trade signals for execution. Not relevant for portfolio-only analysis. |
| **Redis signal pub/sub** | Signal listener in `bot/main.py` | No signals to publish. |

### KEEPING (useful for portfolio tracker)

| Component | Files | Repurpose |
|---|---|---|
| **Market data** | `src/data/mcp_client.py` | Live quotes, RSI, BB, EMA — used to analyze portfolio holdings |
| **IDX Analyst** | `src/agents/idx_analyst.py` | Repurpose: analyze IDX holdings instead of scanning for trades |
| **US Analyst** | `src/agents/us_analyst.py` | Repurpose: analyze US holdings |
| **ETF Analyst** | `src/agents/etf_analyst.py` | Repurpose: analyze ETF holdings |
| **Base Agent** | `src/agents/base.py` | LLM tool-use loop — core engine. Fix JSON parse bug. |
| **Orchestrator** | `src/agents/orchestrator.py` | Repurpose: orchestrate portfolio analysis instead of market scans |
| **Redis cache** | `src/data/cache.py` | Cache market data (quotes, indicators) |
| **Telegram bot** | `src/bot/handlers.py`, `src/bot/main.py` | Commands: `/portfolio`, `/add`, `/analyze`, `/remove` |
| **DB schema** | `portfolio_state` table, `audit_logs` table, `ohlcv_cache` table, `market_holidays` table | Core portfolio storage |

### ADDING

| New feature | Description |
|---|---|
| **Portfolio management commands** | `/add IDX BBCA 500 8500` — add stock, qty, avg price. `/add cash 50000000 IDR` — set cash balance. `/remove BBCA` — remove position. `/edit BBCA qty 600` — update. |
| **Portfolio analysis** | `/analyze` — LLM agent analyzes all holdings against current market: price vs entry, unrealized P&L, sector concentration, risk signals. `/analyze BBCA` — single stock deep dive. |
| **Portfolio summary** | `/portfolio` enhanced — total value, cash, allocation breakdown, day P&L, total unrealized P&L. |
| **Cash tracking** | New `cash_balance` table — track cash per currency (IDR, USD). Included in total portfolio value. |
| **Portfolio analysis agent** | New `PortfolioAnalyst` agent — system prompt designed for investment analysis (not trade signals). Takes portfolio state + market data → returns insights. |

---

## New Architecture

```
┌──────────────────────────────────────────────────────┐
│                   Telegram Bot                        │
│  /portfolio · /add · /remove · /edit · /analyze       │
└──────────┬──────────────┬────────────────────────────┘
           │              │
    ┌──────▼──────┐ ┌─────▼──────┐
    │  Portfolio   │ │  Market    │
    │  Manager     │ │  Data      │
    │  (CRUD)      │ │  (TV API)  │
    └──────┬──────┘ └─────┬──────┘
           │              │
    ┌──────▼──────────────▼──────┐
    │    Portfolio Analyst Agent   │
    │  (LLM: analyze holdings)    │
    └──────┬──────────────────────┘
           │
    ┌──────▼──────┐    ┌─────────┐
    │  PostgreSQL  │    │  Redis   │
    │  portfolio   │    │  cache   │
    │  cash        │    │          │
    └─────────────┘    └─────────┘
```

### New DB Tables

```sql
-- Cash balance per currency
CREATE TABLE IF NOT EXISTS cash_balance (
    id SERIAL PRIMARY KEY,
    currency VARCHAR(5) NOT NULL CHECK (currency IN ('IDR', 'USD')),
    balance DECIMAL(18, 2) NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(currency)
);

-- Portfolio positions (already exists, reuse portfolio_state)
-- Add columns:
ALTER TABLE portfolio_state ADD COLUMN IF NOT EXISTS notes TEXT;
ALTER TABLE portfolio_state ADD COLUMN IF NOT EXISTS added_at TIMESTAMP DEFAULT NOW();
```

### New Telegram Commands

| Command | Example | Description |
|---|---|---|
| `/portfolio` | — | Full portfolio view: positions, cash, total value, P&L |
| `/add` | `/add IDX BBCA 500 8500` | Add position: market, ticker, qty, avg price |
| `/add cash` | `/add cash 50000000 IDR` | Set/add cash balance |
| `/remove` | `/remove BBCA` | Remove position from portfolio |
| `/edit` | `/edit BBCA qty 600` | Update quantity or avg price |
| `/analyze` | `/analyze` | Full portfolio analysis (all holdings vs market) |
| `/analyze` | `/analyze BBCA` | Deep analysis on single holding |
| `/scan` | `/scan IDX BBCA` | Keep — quick market readout on any ticker |
| `/status` | — | Real system health (fix hardcoded green) |

### New Portfolio Analyst Agent

System prompt (investment trader persona):

```
You are the Portfolio Analyst for an investment trader.
Your job is to analyze the trader's CURRENT holdings against live market data.

RESPONSIBILITIES:
1. For each holding: compare current price vs avg cost → unrealized P&L %
2. Check technical health: RSI overbought/oversold, BB position, trend (EMA)
3. Flag risks: positions down >10%, overconcentration (>20% in one stock/sector)
4. Suggest actions: hold, add on dip, trim on strength, cut loss
5. Portfolio-level: total value, cash ratio, sector/asset allocation

You do NOT execute trades. You provide analysis and recommendations.
The trader makes all decisions.

RESPOND WITH a structured analysis:
{
  "portfolio_value": float,
  "cash_pct": float,
  "total_unrealized_pnl_pct": float,
  "holdings": [
    {
      "ticker": str, "market": str,
      "qty": float, "avg_cost": float, "current_price": float,
      "unrealized_pnl_pct": float,
      "technical_health": "bullish" | "neutral" | "bearish",
      "risk_flags": [str],
      "recommendation": "HOLD" | "ADD" | "TRIM" | "CUT",
      "reasoning": str
    }
  ],
  "portfolio_risks": [str],
  "top_actions": [str]
}
```

---

## Current Bugs (carried from original audit)

### P0 — Must fix before portfolio tracker works

| # | Bug | Location | Fix |
|---|---|---|---|
| 1 | **JSON markdown-fence parse** — LLM wraps response in ` ```json ``` `, `json.loads()` fails → 0/100 output | `base.py:136` `_extract_response()` | Strip markdown fences before `json.loads()` with regex: `` re.sub(r'^```(?:json)?\s*\|\s*```$', '', text, flags=re.MULTILINE) `` |
| 2 | **`/status` hardcoded** — always shows "all green" | `handlers.py:91` `status_cmd()` | Probe Redis ping, DB query, 9Router health |
| 3 | **IDXAnalyst overrides `_handle_tool_call` without logging** — tool calls invisible in container logs | `idx_analyst.py:110` | Add `logger.info("tool_call", ...)` |

### P1 — Improve analysis quality

| # | Issue | Location | Fix |
|---|---|---|---|
| 4 | **OHLCV returns 1 candle** — `get_ohlcv()` returns current indicators, not historical series | `mcp_client.py:91` | Document limitation; work with available data |
| 5 | **Agent output not logged** — `confidence_score`, `direction`, `reasoning` never logged | `handlers.py:63` | Log parsed values at INFO level |

---

## File Map (post-pivot)

| File | Status | Action |
|---|---|---|
| `src/agents/base.py` | Keep | Fix JSON parse bug |
| `src/agents/idx_analyst.py` | Repurpose | Remove foreign_flow + ARA tools. Keep quote, OHLCV, BB tools. Add logging. |
| `src/agents/us_analyst.py` | Keep | Remove trade signal format → portfolio analysis format |
| `src/agents/etf_analyst.py` | Keep | Same |
| `src/agents/orchestrator.py` | Repurpose | Market scan → portfolio analysis orchestration |
| `src/agents/risk_manager.py` | **Delete** | Trade risk validation not needed |
| `src/agents/portfolio_analyst.py` | **New** | Investment analysis agent |
| `src/data/mcp_client.py` | Keep | Market data (working) |
| `src/data/idx_adapter.py` | **Delete** | Stockbit API broken, not needed |
| `src/data/cache.py` | Keep | Redis cache |
| `src/execution/` | **Delete entirely** | No broker execution |
| `src/bot/handlers.py` | Repurpose | Add `/add`, `/remove`, `/edit`, `/analyze` commands |
| `src/bot/approval.py` | **Delete** | No HITL approval flow |
| `src/bot/main.py` | Simplify | Remove broker imports, signal listener, approval manager |
| `src/main.py` | Simplify | Remove IDX adapter, broker refs. Add portfolio analysis scheduler job. |
| `src/models/tables.py` | Modify | Add `CashBalance` table. Remove `Signal`, `Trade`, `PendingApproval` (or keep read-only). |
| `src/models/schemas.py` | Repurpose | Portfolio analysis response schema |
| `db/init.sql` | Modify | Add `cash_balance` table, remove `signals`/`trades`/`pending_approvals` (or keep for history) |
| `src/config.py` | Simplify | Remove broker API config. Keep LLM, DB, Redis, Telegram. |

---

## Migration Plan

### Phase 1 — Strip dead code (quick wins)
1. Delete `src/execution/` directory
2. Delete `src/data/idx_adapter.py`
3. Delete `src/bot/approval.py`
4. Remove broker imports from `bot/main.py` and `main.py`
5. Remove signal listener from `bot/main.py`
6. Fix JSON parse bug in `base.py`

### Phase 2 — Portfolio CRUD
1. Add `cash_balance` table to `db/init.sql`
2. Add `/add`, `/remove`, `/edit` Telegram commands
3. Enhance `/portfolio` with cash balance, total value, P&L
4. Wire `PortfolioState` table for manual inserts (not broker sync)

### Phase 3 — Portfolio Analysis Agent
1. Create `src/agents/portfolio_analyst.py`
2. Repurpose IDX/US/ETF analysts to return portfolio-relevant analysis
3. Add `/analyze` command — fetch portfolio from DB, get market data, run analysis
4. Add daily portfolio summary job to scheduler

### Phase 4 — Polish
1. Fix `/status` to real health checks
2. Add agent output logging
3. Add `/analyze` per-stock deep dive

---

## Bottom Line (post-pivot)

| Question | Answer |
|---|---|
| Track portfolio (manual entry)? | **Phase 2** — add `/add`, `/remove`, `/portfolio` with cash |
| Analyze holdings vs market? | **Phase 3** — PortfolioAnalyst agent + `/analyze` |
| Execute trades? | **Removed** — not the goal |
| Stockbit API? | **Removed** — broken, not needed |
| Broker integration? | **Removed** — not needed |

Karsa pivots from "AI auto-trader" to "AI investment portfolio analyst". The trader enters positions manually; Karsa watches the market, analyzes holdings, and surfaces insights via Telegram. No execution risk, no broken broker scaffolding.

---

## 9Router / LLM Issue (26 Jun 2026 07:10 UTC)

During validation, `/scan` returns "Empty response from LLM". Root cause is external to Karsa:

| Finding | Detail |
|---|---|
| `my-combo` resolves to | `gemma-4-31b-it` (Google model, not Claude) |
| Claude direct models | `404: No active credentials for provider: anthropic` |
| `gemma-4-31b-it` response | `stop_reason=None`, `content=None` — empty |

**Impact:** All LLM-dependent features (`/scan`, `/analyze`, scheduled scans) are non-functional until 9Router is configured to route to a working model. This is a **9Router configuration issue**, not a Karsa code issue.

**To fix:** Verify 9Router credentials for Anthropic provider are active, or configure `my-combo` to route to a working model. Check `9ROUTER_AUTH_TOKEN` and provider credentials in 9Router config.

**Code fix applied:** `LLM_BASE_URL` now strips trailing `/v1` to avoid double `/v1/v1/messages` path.

---

## Related Documents

- `docs/AUDIT-RESULT.md` — Original architecture design
- `docs/DESIGN.md` — Original Claude Code-native design (outdated)
- `docs/PROGRESS.md` — Implementation tracker (will need post-pivot update)
- `CLAUDE.md` — Engineering standards (update after pivot)
