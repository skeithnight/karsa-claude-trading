# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Karsa** — AI-driven multi-market trading system for IDX (Indonesia), US Equities, Global ETFs, and Crypto (Bybit perpetuals). Uses Anthropic SDK tool-use agents routed through 9Router for cost-optimized LLM calls with fallback. Crypto node auto-executes trades via Smart Order Router.

## Dev Tooling

### rtk (Rust Token Killer)
CLI proxy that filters and compresses shell command output before it reaches LLM context — 60-90% token savings. Installed globally; auto-rewrites Bash tool calls via PreToolUse hook.

```bash
# Setup (one-time)
rtk init -g          # installs Claude Code hook + RTK.md

# rtk is transparent after setup — these run automatically:
docker compose ps    # -> rtk docker compose ps  (compact)
docker logs karsa-orchestrator --tail 20  # -> deduplicated
git status           # -> compact
pytest               # -> failures only

# Explicit calls when needed
rtk docker ps                          # compact container list
rtk docker logs karsa-orchestrator    # deduplicated logs
rtk docker compose ps                  # compose services
rtk pytest                             # Python tests, -90% output
rtk git diff                           # condensed diff
rtk gain                               # token savings stats
rtk gain --graph                       # ASCII graph last 30 days
```

> Note: rtk only intercepts Bash tool calls. Claude Code built-in tools (Read, Grep, Glob) bypass the hook — use shell commands (`cat`, `rg`, `find`) or `rtk read`/`rtk grep` explicitly when you want filtering there.

### graphify
Turns the Karsa codebase into a queryable knowledge graph — code, SQL schema (`db/init.sql`), docs, all in one graph. Use it to navigate agent relationships and data flows without reading every file.

```bash
# Setup (one-time)
uv tool install graphifyy
graphify install          # registers Claude Code skill
graphify claude install   # writes CLAUDE.md hook + always-on graph reminder
```

```
# In Claude Code sessions
/graphify .                                   # build/rebuild the graph
/graphify . --update                          # re-extract only changed files
/graphify query "how does signal flow from analyst to Telegram?"
/graphify query "what connects BaseAgent to MCPClient?"
/graphify path "Orchestrator" "ApprovalManager"
/graphify explain "BaseAgent"
graphify export callflow-html                 # Mermaid architecture page
```

Graph output lives in `graphify-out/` (commit this):
- `graph.html` — interactive browser view
- `GRAPH_REPORT.md` — key concepts, surprising connections, suggested questions
- `graph.json` — queryable via `graphify query` anytime

> Use `/graphify query` before grepping files for architecture questions. The graph already knows how `src/agents/`, `src/bot/`, `src/data/`, and `db/init.sql` connect.

## Build & Run

### Quick Start (first time)
```bash
cp .env.example .env        # fill in API keys
# Required in .env:
#   DB_PASSWORD=<12+ chars, no placeholders>
#   REDIS_PASSWORD=<any>
#   TELEGRAM_TOKEN=<from @BotFather>
#   TELEGRAM_CHAT_ID=<your chat ID>
#   9ROUTER_URL, 9ROUTER_AUTH_TOKEN, 9ROUTER_MODEL (or ANTHROPIC_API_KEY)
docker compose up --build   # starts all services
```

### Development Commands
```bash
# Start all
docker compose up -d --build

# Rebuild single service (after code changes)
docker compose up -d --build karsa-orchestrator
docker compose up -d --build karsa-telegram-bot

# Restart without rebuild (config changes only)
docker compose restart karsa-orchestrator karsa-telegram-bot

# Stop all
docker compose down

# Check status
docker compose ps

# Logs (follow)
docker logs -f karsa-orchestrator
docker logs -f karsa-telegram-bot
```

### Health Checks
```bash
# Orchestrator health (scheduler status)
curl http://localhost:8000/health
curl http://localhost:8000/health/scheduler

# Inside container — quick config check
docker exec karsa-orchestrator python3 -c "from src.config import settings; print(settings.TRADING_MODE)"
```

### Testing IDX Intelligence
```bash
# Check composite score
docker exec karsa-orchestrator python3 -c "
from src.config import settings
from src.data.mcp_client import MCPClient
from src.advisory.idx_intelligence import IDXMarketIntelligence
import asyncio

async def test():
    mcp = MCPClient()
    intel = IDXMarketIntelligence(mcp)
    result = await intel.get_regime_composite()
    print(f'Score: {result[\"score\"]} ({result[\"regime\"]})')
    print(f'Components: {result[\"components\"]}')

asyncio.run(test())
"

# Check earnings calendar
docker exec karsa-orchestrator python3 -c "
from src.advisory.idx_intelligence import EarningsCalendar
cal = EarningsCalendar()
universe = cal.get_blackout_universe()
print(f'Blackout tickers: {universe if universe else \"None\"}')
"
```

## Architecture

**Three main containers** share the same Python package (`src/`):

1. **karsa-orchestrator** (`src/main.py`) — APScheduler runs 23+ cron jobs (IDX pre-open/morning/afternoon/pre-close scans, US+ETF scans, 2 EOD reviews, pre-market battle plan, paper position updates, kill switch, cache flush, crypto scan 24/7, crypto position monitor, crypto funding sync, crypto PnL snapshot, crypto position reconciliation, trailing stop updates, partial/time-based exits, circuit breaker checks, funding limit enforcement, liquidity checks, OMS cleanup). Each scan job dispatches agents via `Orchestrator.scan_all_markets()` which runs IDX/US/ETF analysts in parallel (`asyncio.gather`). Crypto scans use batched prompting (5 coins/LLM call). Confidence calibration applied to all crypto signals before risk gate.

2. **karsa-crypto-orchestrator** (`src/main_crypto.py`) — Dedicated crypto-only orchestrator. Runs only crypto-related APScheduler jobs (no IDX/US/ETF). Health endpoint on port 8001. Shares Redis, Postgres, and 9router with main orchestrator. `CRYPTO_ONLY_MODE=true` env var. IDX scans are gated by composite score (≤-50 skips, ≤-20 reduces sizing). Signals ≥50 confidence get validated, persisted to DB, and risk-checked. Kill switch activates emergency stop via Redis at `CRYPTO_DAILY_LOSS_LIMIT_PCT`. Health check via FastAPI on port 8000 (`/health`, `/health/scheduler`).

2. **karsa-telegram-bot** (`src/bot/main.py`) — FastAPI webhook + python-telegram-bot polling (default). Commands: `/start`, `/status`, `/scan`, `/portfolio`, `/trades`, `/add`, `/remove`, `/edit`, `/analyze`, `/audit`, `/briefing`, `/regime`, `/pnl`, `/idx`, `/stop`, `/resume`. `/idx` shows IDX Intelligence dashboard (composite score, sector rotation, breadth, flow, earnings). The bot reuses the orchestrator's `idx_intel` instance for cached intelligence data. Inline keyboard buttons provide navigation between views. Auth enforced — all commands require `TELEGRAM_CHAT_ID` to be set.

3. **karsa-crypto-bot** (`src/bot/crypto_main.py`) — Separate Telegram bot for crypto trading on Bybit. 15 commands: `/start`, `/status`, `/portfolio`, `/scan`, `/pnl`, `/risk`, `/kill`, `/sellall`, `/resume`, `/activity`, `/audit_agent`, `/guide`, `/regime`, `/funding`, `/trades`. Auto-execute pipeline: scan → risk gate (8 gates) → SOR → save → notify. Shares orchestrator + Redis via `bot_data`. Inline keyboard navigation on all commands. `/kill` sets Redis global halt, flattens all positions. `/sellall` flattens + 15min cooldown.

**Agent loop** (`src/agents/base.py`): Each agent is a `BaseAgent` subclass with a system prompt, tool definitions, and an `_handle_tool_call` override. The `run()` method implements the Anthropic SDK tool-use loop — call LLM, process tool calls, repeat until `end_turn`.

**Data flow for tools**: Agent calls tool → `BaseAgent._handle_tool_call()` → specific agent override → `MCPClient` method → `tradingview_ta` (direct Python import, no MCP protocol).

**Market data** (`src/data/mcp_client.py`): Uses `tradingview_ta.TA_Handler` directly (not MCP protocol). IDX uses `screener='indonesia', exchange='IDX'`. US/ETF tries NASDAQ → NYSE → AMEX fallback. CRYPTO delegates to `BybitClient` (pybit REST API). Data cached in Redis (60s quotes, 1h OHLCV). Rate-limited with Semaphore and `asyncio.to_thread()` for non-blocking sleep.

**Advisory layer** (`src/advisory/`): `USRegimeFilter`/`IDXRegimeFilter` check VIX/SPY/200-SMA to classify BULL/BEAR/NEUTRAL. Regime hard veto: ETF mean reversion disabled in BEAR regime. `PositionSizer` calculates volatility-target sizing using ATR. **IDX Intelligence** (`idx_intelligence.py`): composite regime scoring (breadth 30% + sector rotation 25% + foreign flow 20% + price structure 25%), `FlowTracker` (volume-based proxy for foreign activity), `EarningsCalendar` with blackout windows. Composite gate: score ≤-50 skips IDX scan, ≤-20 reduces sizing.

**Risk module** (`src/risk/`): `emergency.py` — Redis-backed kill switch (`activate()`/`deactivate()`/`is_active()`), global halt for crypto (`activate_global_halt()`). `idx_limits.py` — IDX tick sizes (Fraksi Harga), ARA/ARB validation (dynamic per-ticker), `validate_order()` with ADV liquidity gate (`max_lots_by_adv`), T+2 settlement, IHSG circuit breaker (±5%→30min halt, ±10%→halted), forced sell triggers (3x lower limit, 10x ADV volume, T+2 failure, IDX suspension). `crypto_risk_manager.py` — 8 risk gates for crypto, correlation tiers, liquidation proximity, tier-based leverage. `sor.py` — Smart Order Router for Bybit (limit → reprice → market fallback). `funding_tracker.py` — per-position funding cost tracking. `circuit_breaker.py` — `CircuitBreakerManager`: automated circuit breakers (daily DD, volatility spike 5%/15min, correlation cascade), Redis-backed with 30min TTL. `liquidity.py` — pre-trade orderbook depth/spread checks, slippage estimation, used by SOR before market orders. `position_manager.py` — post-entry lifecycle: partial exit at +1R target (50%), time-based exits for stale positions (48h, <1% gain). `position_sync.py` — bidirectional reconciliation between Bybit and local DB (phantom/missing/size drift for positions, orphaned/unknown for orders, balance drift), runs every 60s. `trailing_stop.py` — `TrailingStopManager`: ATR-based trailing with regime-aware multipliers (TREND_BULL/BEAR=2.0x, MEAN_REVERSION=1.5x, CHOP=disabled). `profile_manager.py` — `RiskProfileManager`: 3-tier risk profiles (conservative/semi_aggressive/aggressive), Redis-backed, 5min cooldown, publishes to `karsa:events:profile_changed` on switch. `calibration_engine.py` — `ConfidenceCalibrator`: tracks LLM confidence vs actual win rate, applies multiplier [0.5, 1.5] to future signals. `portfolio_allocator.py` — `PortfolioAllocator`: cross-market capital limits (Crypto 30%, US 40%, ETF 20%, IDX 10%), global 5% drawdown kill switch.

**Execution engine** (`src/execution/`): `websocket_manager.py` — `WebSocketManager`: persistent Bybit WS connection for open positions, updates `karsa:realtime:price:{ticker}` in Redis, auto-subscribe/unsubscribe. `sl_engine.py` — `StopLossEngine`: WS-driven stop-loss trigger, fires market close via SOR when price breaches SL, sub-second reaction bypassing LLM loop. `oms.py` — `OrderManagementSystem`: order lifecycle tracking (NEW→SUBMITTED→PARTIAL→FILLED/CANCELLED/REJECTED), stuck order cleanup (>15min unfilled), Redis-backed state machine.

**HITL flow**: `/scan` → agent generates signal → saved to `signals` table (PENDING) → if confidence >= 60, Telegram alert with APPROVE/REJECT buttons → APPROVE creates `PaperPosition`, REJECT marks rejected. Implementation in `src/bot/_approval.py`.

## Key Config

- **9Router**: Agents use `settings.LLM_BASE_URL` / `LLM_AUTH_TOKEN` / `LLM_MODEL` (resolved from `9ROUTER_*` env vars). Combo names: `karsa-critical` (orchestrator+risk), `karsa-routine` (analysts), `karsa-emergency` (kill switch, 8s timeout).
- **Telegram**: Polling mode by default (no domain needed). Set `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`. Set `TELEGRAM_WEBHOOK_URL` + `TELEGRAM_WEBHOOK_SECRET` for webhook mode.
- **Database**: PostgreSQL via asyncpg + SQLAlchemy async. Schema in `db/init.sql` auto-applied on first start. Append-only rules on `audit_logs` and `closed_paper_trades`.
- **Redis**: Authenticated via `REDIS_PASSWORD`. Emergency stop key: `karsa:emergency_stop`.
- **Trading safety**: `TRADING_MODE` must be `paper` or `live`. `DB_PASSWORD` validated at startup (≥12 chars, no placeholders).
- **Trading params**: `MAX_PORTFOLIO_RISK_PCT` (2%), `MAX_POSITION_SIZE_PCT` (15%), `DAILY_LOSS_LIMIT_PCT` (5%).
- **Crypto Separation**: `CRYPTO_ONLY_MODE=true` skips IDX/US/ETF jobs in main orchestrator. Use `karsa-crypto-orchestrator` Docker service for dedicated crypto trading.
- **Bybit (Crypto)**: `BYBIT_API_KEY`, `BYBIT_API_SECRET`, `BYBIT_TESTNET` (default True). `CRYPTO_TELEGRAM_TOKEN` for separate crypto bot. Risk params: `CRYPTO_MAX_RISK_PER_TRADE_PCT` (1%), `CRYPTO_MAX_POSITION_PCT` (10%), `CRYPTO_MAX_CONCURRENT_POSITIONS` (5), `CRYPTO_DAILY_LOSS_LIMIT_PCT` (3%), `CRYPTO_MAX_LEVERAGE` (10). Liquidation thresholds: `CRYPTO_LIQUIDATION_WARN_PCT` (20%), `CRYPTO_LIQUIDATION_ALERT_PCT` (10%), `CRYPTO_LIQUIDATION_FORCE_CLOSE_PCT` (5%). Funding: `CRYPTO_FUNDING_ALERT_THRESHOLD` (0.05%).

## File Map (non-obvious)

- `src/agents/orchestrator.py` — universe lists (IDX_UNIVERSE 30 stocks, US_UNIVERSE 15, ETF_UNIVERSE 12), combo name assignment, parallel market scan with IDX composite gate, signal validation (`_validate_signal`), emergency stop gate, IDX order validation with forced sell triggers, signal persistence to DB, shared `idx_intel` instance
- `src/agents/base.py` — Anthropic SDK tool-use loop, `getattr()` guards for 9Router response quirks
- `src/bot/handlers.py` — Telegram command handlers (16 commands), composable HTML formatting via `src/utils/format.py`, approval flow via `src/bot/_approval.py`, inline keyboard routing via `button_callback()`, fail-closed auth check
- `src/bot/_approval.py` — HITL approval flow: `send_signal_alert()` sends APPROVE/REJECT buttons, `handle_approval()` creates PaperPosition on approve
- `src/utils/format.py` — Composable Telegram HTML formatters: `HTML` marker, `bold()`, `italic()`, `code()`, `pre()`, `fmt()`, `join()`. Auto-escapes.
- `src/utils/validation.py` — Shared input validation: `validate_ticker()`, `validate_market()`, `sanitize_for_prompt()`
- `src/data/cache.py` — Redis wrapper with quote/OHLCV caching
- `src/data/mcp_client.py` — `tradingview_ta.TA_Handler` wrapper with circuit breaker, 3-tier fallback (TradingView → Massive → Finnhub), `asyncio.to_thread()` for non-blocking I/O, `get_volume_profile()` for flow proxy
- `src/models/tables.py` — SQLAlchemy ORM: PortfolioState, CashBalance, Signal, PaperPosition, ClosedPaperTrade, AuditLog, OHLCVCache, MarketHoliday, PendingApproval, CryptoPosition, CryptoFundingPayment, CryptoRegimeHistory, CryptoPnLSnapshot
- `src/models/database.py` — async engine + session factory, `init_db()` creates tables
- `src/risk/emergency.py` — Redis-backed emergency stop: `activate(reason, operator)`, `deactivate(operator)`, `is_active()`, `get_status()`
- `src/risk/idx_limits.py` — IDX Fraksi Harga tick sizes, `validate_order()`, dynamic ARA/ARB ceiling/floor, `settlement_date()` T+2, IHSG circuit breaker (`ihsg_circuit_breaker_level`), forced sell triggers (`check_forced_sell_triggers`)
- `src/advisory/regime.py` — `USRegimeFilter`/`IDXRegimeFilter`: VIX/SPY/200-SMA regime classification
- `src/advisory/idx_intelligence.py` — `IDXMarketIntelligence` (composite scoring), `FlowTracker` (volume-based foreign flow proxy), `EarningsCalendar` (blackout windows). Sector universe: BANKING/TELCO/CONSUMER/AUTO/ENERGY/TECH/INFRA/MINING
- `src/advisory/earnings_calendar.json` — static IDX earnings dates, updated quarterly
- `src/advisory/sizing.py` — `PositionSizer`: volatility-target sizing using ATR
- `src/utils/rate_limit.py` — Lua-based token bucket in Redis
- `src/utils/telegram_helpers.py` — `format_pre_table()` for aligned ASCII tables, `send_long_message()` with 4096-char chunking, `build_nav_keyboard()` for inline keyboards
- `src/utils/market_hours.py` — `is_idx_open()`, `is_us_open()` market hours checks
- `src/agents/portfolio_analyst.py` — Analyzes holdings vs market data, suggests actions (no execution)
- `src/backtest/engine.py` — RSI + Bollinger mean reversion backtester (Sharpe > 1.2 gate)
- `src/agents/crypto_analyst.py` — Crypto Trend+Sentiment agent. Tools: deterministic TA (RSI, BB, MACD, ATR) via `crypto_technicals.py`. 10 Bybit perpetual pairs.
- `src/agents/crypto_auditor.py` — Performance review agent. Pre-filter rejects extreme RSI/crowded funding before LLM call.
- `src/bot/crypto_handlers.py` — 15 crypto Telegram commands, inline keyboards, `_get_bybit()`/`_get_redis()` shared connection helpers, activity/audit/guide/regime/funding/trades views.
- `src/main_crypto.py` — Crypto-only orchestrator entry point. Runs only crypto APScheduler jobs (no IDX/US/ETF). Health on port 8001. Separate Docker service `karsa-crypto-orchestrator`.
- `src/execution/__init__.py` — Execution engine package.
- `src/execution/websocket_manager.py` — `WebSocketManager`: persistent Bybit WS for open positions, Redis price cache.
- `src/execution/sl_engine.py` — `StopLossEngine`: WS-driven stop-loss, sub-second reaction.
- `src/execution/oms.py` — `OrderManagementSystem`: order lifecycle state machine, stuck order cleanup.
- `src/risk/calibration_engine.py` — `ConfidenceCalibrator`: LLM confidence vs win rate tracking, multiplier adjustment.
- `src/risk/portfolio_allocator.py` — `PortfolioAllocator`: cross-market capital limits, global drawdown guard.
- `src/agents/memory_retriever.py` — RAG memory retriever using pgvector. `get_relevant_trade_memory()` queries nearest past trades. `store_trade_memory()` saves outcomes. Gracefully degrades without `sentence-transformers`.
- `src/backtest/perp_simulator.py` — `PerpSimulator`: event-driven perpetual backtester with funding fee simulation (8h), maker/taker fees, slippage model, liquidation check.
- `src/bot/crypto_main.py` — Separate FastAPI app + polling for crypto bot. Wires orchestrator + shared Redis into `bot_data`.
- `src/data/bybit_client.py` — Bybit REST API client (pybit). Data + execution methods. Exponential backoff retry. Semaphore(5) + 100ms throttle. Proxy support via `BYBIT_PROXY`.
- `src/advisory/crypto_regime.py` — Hurst Exponent + ADX regime classifier on BTC. BTC dominance via CoinGecko. 5-min cache.
- `src/advisory/crypto_technicals.py` — Pure Python RSI, BB, EMA, MACD, ATR. Self-test in `__main__`.
- `src/advisory/crypto_universe.py` — Single source of truth for 10 crypto pairs + per-pair config.
- `src/advisory/crypto_audit.py` — `CryptoAuditMetrics.gather()` queries DB for deterministic performance metrics.
- `src/risk/crypto_risk_manager.py` — 8 risk gates, correlation tiers, liquidation proximity, Redis-backed kill switch, tier-based leverage.
- `src/risk/sor.py` — Smart Order Router: limit→reprice→market fallback, `flatten_all()`.
- `src/risk/funding_tracker.py` — Funding rate tracking, annualized cost, alert thresholds.
- `src/risk/circuit_breaker.py` — Automated circuit breakers: daily DD, volatility spike (5%/15min→30min halt), correlation cascade (>60% correlated positions losing). Redis-backed with 30min TTL.
- `src/risk/liquidity.py` — `LiquidityMonitor` checks orderbook depth/spread, `SlippageEstimator` simulates fills through orderbook levels. Used by SOR before market orders.
- `src/risk/position_manager.py` — Post-entry lifecycle: partial exits at +1R/+2R (33% each), time-based exits for stale positions (72h, <1% gain).
- `src/risk/position_sync.py` — Bidirectional reconciliation: position drift (phantom/missing/size), order drift (orphaned/unknown), balance drift. Runs every 5min.
- `src/risk/trailing_stop.py` — `TrailingStopManager`: ATR-based trailing with regime-aware multipliers (TREND_BULL/BEAR=2.0x, MEAN_REVERSION=1.5x, CHOP=disabled).
- `src/advisory/crypto_market_watch.py` — `CryptoMarketWatchEngine`: top movers, full scan summary, funding alerts across universe.
- `src/advisory/performance_tracker.py` — `PerformanceTracker`: equity curve from daily snapshots, drawdown stats, trade statistics. Persists to CryptoPnLSnapshot.
- `src/advisory/strategy_selector.py` — `StrategySelector`: regime→strategy config mapping (confidence boost, max positions, size multiplier, preferred pairs). Used by CryptoAnalyst for dynamic prompts.
- `src/utils/trader_format.py` — Rich Telegram formatters for crypto: `funding_gauge()`, `regime_banner()`, `signal_card()`.
- `src/utils/logging.py` — Structured logging via structlog (JSON output, log level config).
- `db/migrations/add_crypto_market.sql` — Migration adding CRYPTO to all CHECK constraints.
- `docs/AUDIT_KARSA_3.md` — Initial crypto audit (June 30, 2026).
- `docs/AUDIT_KARSA_CRYPTO_BOT.md` — Post-implementation audit (July 1, 2026). 18 findings, 5 critical.
- `docs/KARSA_CRYPTO_DESIGN_TEXT.md` — Crypto bot UI design system.
- `monitoring/` — Prometheus + Grafana configs. `prometheus.yml` scrapes `/metrics` on orchestrator (8000) and crypto-orchestrator (8001). `grafana-dashboard.json` — Karsa trading metrics dashboard.
- `docs/` — Design docs, audit results, feature roadmap
- `docs/AUDIT_REVIEW_QWEN_2JUL.md` — REVIEW_QWEN implementation audit (10 steps, 3 bugs fixed)
- `docs/AUDIT_REVIEW_GROK_2JUL.md` — REVIEW_GROK implementation audit (6 steps, 1 bug fixed)
- `db/migrations/add_pgvector_memory.sql` — pgvector extension + trade_memory table for RAG
- `db/migrations/add_risk_profile.sql` — risk_profile_audit table + signal columns
- `db/migrations/add_universe_history.sql` — universe_history table
- `graphify-out/` — committed knowledge graph; query before reading source files

## Gotchas

- Dockerfiles `COPY src/` — must `--build` after code changes, `restart` alone won't pick up new code.
- `tradingview_ta` is imported lazily inside the container (not at module level) to avoid startup failures if the package isn't installed yet.
- Containers run as non-root user `karsa`. If volume permissions break, check file ownership.
- Redis requires authentication (`REDIS_PASSWORD`). All containers must use `redis://:${REDIS_PASSWORD}@redis:6379`.
- `DB_PASSWORD` must be ≥12 chars and not a placeholder. The config validator rejects common weak values at startup.
- IDX lot size is always 100 shares. `IDXBroker` enforces this.
- The `karsa-9router` service exists in `docker-compose.yml` but the system expects the user's own 9Router instance via `host.docker.internal:20128` for local dev. The compose 9router is on port 20129→20128.
- Kill switch threshold uses `CRYPTO_DAILY_LOSS_LIMIT_PCT` (default 3%) for crypto, checked against unrealized PnL from positions. Also checks Redis emergency stop (survives restarts). `/kill` sets both `karsa:global_halt` and `karsa:emergency_stop` Redis keys.
- APScheduler uses `MemoryJobStore` — jobs are stateless and don't survive container restarts.
- `_VALID_DIRECTIONS` in orchestrator is `{"LONG", "SHORT", "CLOSE"}` — matches DB CHECK constraint. Agents returning BUY/SELL/HOLD/WATCH are rejected by validation.
- Postgres uses `pgvector/pgvector:pg15` image (not `postgres:15-alpine`). Required for `trade_memory` table with vector embeddings. `CREATE EXTENSION IF NOT EXISTS vector` in init.sql.
- `src/metrics/crypto_metrics.py` must be imported at startup for Prometheus metrics to register. Both `main.py` and `main_crypto.py` import it in `startup()`.
- `CircuitBreakerManager` (not `CircuitBreaker`) is the correct class name in `src/risk/circuit_breaker.py`.
- `FundingTracker.__init__` takes `(bybit_client)` — not `(bybit, redis)`. Methods: `get_current_rates()`, `get_cumulative_costs()`. No `check_limits()` or `sync_all()`.
- `TrailingStopManager.update_trailing_stops(positions)` requires a `list[CryptoPosition]` argument.
- `BaseAgent.run()` returns `dict` — if LLM returns JSON array, it's parsed as single object. Batch prompt in orchestrator handles this with fallback `batch_result.get("signals", [batch_result])`.
- `sentence-transformers` not in Dockerfile — RAG memory gracefully degrades (returns empty string). Add `pip install sentence-transformers` to Dockerfile for full RAG support.
