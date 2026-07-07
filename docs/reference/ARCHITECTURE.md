# Architecture Reference

Read this when working on cross-module wiring or reviewing a subsystem in depth. For quick "how does X connect to Y" questions, prefer `/graphify query` — it's kept current automatically; this doc may drift.

## Containers

1. **karsa-orchestrator** (`src/main.py`) — APScheduler runs 25+ cron jobs (IDX pre-open/morning/afternoon/pre-close scans, US+ETF scans, 2 EOD reviews, pre-market battle plan, paper position updates, kill switch, cache flush, crypto scan 24/7, crypto position monitor, crypto funding sync, crypto PnL snapshot, crypto position reconciliation, trailing stop updates, partial/time-based exits, circuit breaker checks, funding limit enforcement, liquidity checks, OMS cleanup). Each scan job dispatches agents via `Orchestrator.scan_all_markets()` which runs IDX/US/ETF analysts in parallel (`asyncio.gather`). Crypto scans use batched prompting (5 coins/LLM call). Confidence calibration applied to all crypto signals before risk gate.

2. **karsa-crypto-orchestrator** (`src/main_crypto.py`) — Dedicated crypto-only orchestrator. Runs only crypto-related APScheduler jobs (no IDX/US/ETF). Health endpoint on port 8001. Shares Redis, Postgres, and 9router with main orchestrator. `CRYPTO_ONLY_MODE=true` env var. IDX scans are gated by composite score (≤-50 skips, ≤-20 reduces sizing). Signals ≥50 confidence get validated, persisted to DB, and risk-checked. Kill switch activates emergency stop via Redis at `CRYPTO_DAILY_LOSS_LIMIT_PCT`. Health check via FastAPI on port 8000 (`/health`, `/health/scheduler`). **ASM (Autonomous Session Manager)** runs inside this container — Telegram `/start` and `/stop` control it. ASM loop: regime check → scan crypto pairs → filter by confidence → execute through risk gates → SOR → notify. Prometheus metrics on port 8444 (`/metrics`).

3. **karsa-telegram-bot** (`src/bot/main.py`) — FastAPI webhook + python-telegram-bot polling (default). Commands: `/start`, `/status`, `/scan`, `/portfolio`, `/trades`, `/add`, `/remove`, `/edit`, `/analyze`, `/audit`, `/briefing`, `/regime`, `/pnl`, `/idx`, `/stop`, `/resume`. `/idx` shows IDX Intelligence dashboard (composite score, sector rotation, breadth, flow, earnings). The bot reuses the orchestrator's `idx_intel` instance for cached intelligence data. Inline keyboard buttons provide navigation between views. Auth enforced — all commands require `TELEGRAM_CHAT_ID` to be set.

4. **karsa-crypto-bot** (`src/bot/crypto_main.py`) — Separate Telegram bot for crypto trading on Bybit. 15 commands: `/start`, `/status`, `/portfolio`, `/scan`, `/pnl`, `/risk`, `/kill`, `/sellall`, `/resume`, `/activity`, `/audit_agent`, `/guide`, `/regime`, `/funding`, `/trades`. Auto-execute pipeline: scan → risk gate (8 gates) → SOR → save → notify. Shares orchestrator + Redis via `bot_data`. Inline keyboard navigation on all commands. `/kill` sets Redis global halt, flattens all positions. `/sellall` flattens + 15min cooldown.

## Core Loops

**Agent loop** (`src/agents/base.py`): Each agent is a `BaseAgent` subclass with a system prompt, tool definitions, and an `_handle_tool_call` override. The `run()` method implements the Anthropic SDK tool-use loop — call LLM, process tool calls, repeat until `end_turn`.

**Data flow for tools**: Agent calls tool → `BaseAgent._handle_tool_call()` → specific agent override → `MCPClient` method → `tradingview_ta` (direct Python import, no MCP protocol).

**Market data** (`src/data/mcp_client.py`): Uses `tradingview_ta.TA_Handler` directly (not MCP protocol). IDX uses `screener='indonesia', exchange='IDX'`. US/ETF tries NASDAQ → NYSE → AMEX fallback. CRYPTO delegates to `BybitClient` (pybit REST API). Data cached in Redis (60s quotes, 1h OHLCV). Rate-limited with Semaphore and `asyncio.to_thread()` for non-blocking sleep.

**HITL flow**: `/scan` → agent generates signal → saved to `signals` table (PENDING) → if confidence >= 60, Telegram alert with APPROVE/REJECT buttons → APPROVE creates `PaperPosition`, REJECT marks rejected. Implementation in `src/bot/_approval.py`.

## Advisory Layer (`src/advisory/`)

`USRegimeFilter`/`IDXRegimeFilter` check VIX/SPY/200-SMA to classify BULL/BEAR/NEUTRAL. Regime hard veto: ETF mean reversion disabled in BEAR regime. `PositionSizer` calculates volatility-target sizing using ATR.

**IDX Intelligence** (`idx_intelligence.py`): composite regime scoring (breadth 30% + sector rotation 25% + foreign flow 20% + price structure 25%), `FlowTracker` (volume-based proxy for foreign activity), `EarningsCalendar` with blackout windows. Composite gate: score ≤-50 skips IDX scan, ≤-20 reduces sizing.

## Risk Module (`src/risk/`)

- `emergency.py` — Redis-backed kill switch (`activate()`/`deactivate()`/`is_active()`), global halt for crypto (`activate_global_halt()`).
- `idx_limits.py` — IDX tick sizes (Fraksi Harga), ARA/ARB validation (dynamic per-ticker), `validate_order()` with ADV liquidity gate (`max_lots_by_adv`), T+2 settlement, IHSG circuit breaker (±5%→30min halt, ±10%→halted), forced sell triggers (3x lower limit, 10x ADV volume, T+2 failure, IDX suspension).
- `crypto_risk_manager.py` — 8 risk gates for crypto, correlation tiers, liquidation proximity, tier-based leverage.
- `sor.py` — Smart Order Router for Bybit (limit → reprice → market fallback).
- `funding_tracker.py` — per-position funding cost tracking.
- `circuit_breaker.py` — `CircuitBreakerManager`: automated circuit breakers (daily DD, volatility spike 5%/15min, correlation cascade), Redis-backed with 30min TTL.
- `liquidity.py` — pre-trade orderbook depth/spread checks, slippage estimation, used by SOR before market orders.
- `position_manager.py` — post-entry lifecycle: partial exit at +1R target (50%), time-based exits for stale positions (48h, <1% gain).
- `position_sync.py` — bidirectional reconciliation between Bybit and local DB (phantom/missing/size drift for positions, orphaned/unknown for orders, balance drift), runs every 5 min.
- `trailing_stop.py` — `TrailingStopManager`: ATR-based trailing with regime-aware multipliers (TREND_BULL/BEAR=2.0x, MEAN_REVERSION=1.5x, CHOP=disabled).
- `profile_manager.py` — `RiskProfileManager`: 3-tier risk profiles (conservative/semi_aggressive/aggressive), Redis-backed, 5min cooldown, publishes to `karsa:events:profile_changed` on switch.
- `calibration_engine.py` — `ConfidenceCalibrator`: tracks LLM confidence vs actual win rate, applies multiplier [0.5, 1.5] to future signals.
- `portfolio_allocator.py` — `PortfolioAllocator`: cross-market capital limits (Crypto 30%, US 40%, ETF 20%, IDX 10%), global 5% drawdown kill switch.
- `profit_lock.py` — R-multiple profit lock engine: +1.0R→tight trail (1.0x ATR), +2.0R→medium (0.75x), +3.0R→tight (0.5x).
- `distributed_lock.py` — Redis `SET NX EX` one-line distributed lock for concurrent job safety.

## Execution Engine (`src/execution/`)

`websocket_manager.py` — persistent Bybit WS connection for open positions, updates `karsa:realtime:price:{ticker}` in Redis, auto-subscribe/unsubscribe. `sl_engine.py` — WS-driven stop-loss trigger, fires market close via SOR when price breaches SL, sub-second reaction bypassing LLM loop. `oms.py` — order lifecycle tracking (NEW→SUBMITTED→PARTIAL→FILLED/CANCELLED/REJECTED), stuck order cleanup (>15min unfilled), Redis-backed state machine.

## Event-Driven Migration (`src/architecture/`)

9-phase migration to an event-driven trading OS, each phase feature-flagged via `src/architecture/feature_flags.py` (Redis-backed `is_enabled(flag)`/`enable(flag)`/`disable(flag)`). Subpackages: `events/` (Redis + in-process bus), `position/` (commands, manager, aggregate), `exit/` (engine, strategies), `decision/` (engine, sources), `policy/` (engine, rules), `replay/` (engine), `workflow/` (engine, scanner, checkpoint), `agent_runtime/` (registry, runtime), `common/` (base, interfaces).

## AODE Research Platform

Master switch `AODE_ENABLED=false`. Multi-source discovery (CoinGecko + DeFiLlama + DexScreener + Bybit) → scoring (Fundamental 25%, Narrative 15%, Smart Money 15%, On-chain 15%, Developer 10%, Community 8%, Market 7%, Technical 5%) → buckets (Core >80, Growth 60-80, Speculative 40-60, Moonshot <40). Feature flags default off via Redis `karsa:feature_flags:<name>`.