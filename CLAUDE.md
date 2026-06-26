# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Karsa** — AI-driven multi-market trading system for IDX (Indonesia), US Equities, and Global ETFs. Uses Anthropic SDK tool-use agents routed through 9Router for cost-optimized LLM calls with fallback.

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

```bash
# Development
cp .env.example .env        # fill in API keys
docker compose up --build   # starts all 5 services (9router, redis, postgres, orchestrator, bot)

# Rebuild single service
docker compose up -d --build karsa-orchestrator
docker compose up -d --build karsa-telegram-bot

# Restart without rebuild
docker compose restart karsa-orchestrator karsa-telegram-bot

# Check status
docker compose ps

# Logs
docker logs karsa-orchestrator --tail 20
docker logs karsa-telegram-bot --tail 20

# Test inside container
docker exec karsa-orchestrator python3 -c "from src.config import settings; print(settings.LLM_BASE_URL)"
```

## Architecture

**Two main containers** share the same Python package (`src/`):

1. **karsa-orchestrator** (`src/main.py`) — APScheduler runs 9 cron jobs (IDX morning/afternoon scans, US+ETF scans, 2 EOD reviews, pre-market battle plan, paper position updates, kill switch, cache flush). Each scan job dispatches agents via `Orchestrator.scan_all_markets()` which runs IDX/US/ETF analysts in parallel (`asyncio.gather`). Signals ≥50 confidence get persisted; ≥60 get risk-checked and published to Redis. Includes a health check HTTP server on port 8080 (`/health`, `/health/scheduler`).

2. **karsa-telegram-bot** (`src/bot/main.py`) — FastAPI webhook + python-telegram-bot polling (default). Commands: `/start`, `/status`, `/scan`, `/portfolio`, `/trades`, `/add`, `/remove`, `/edit`, `/analyze`, `/audit`, `/briefing`, `/regime`, `/pnl`. The bot creates its own `Orchestrator` instance for ad-hoc commands. Inline keyboard buttons provide navigation between views.

**Agent loop** (`src/agents/base.py`): Each agent is a `BaseAgent` subclass with a system prompt, tool definitions, and an `_handle_tool_call` override. The `run()` method implements the Anthropic SDK tool-use loop — call LLM, process tool calls, repeat until `end_turn`.

**Data flow for tools**: Agent calls tool → `BaseAgent._handle_tool_call()` → specific agent override → `MCPClient` method → `tradingview_ta` (direct Python import, no MCP protocol).

**Market data** (`src/data/mcp_client.py`): Uses `tradingview_ta.TA_Handler` directly (not MCP protocol). IDX uses `screener='indonesia', exchange='IDX'`. US/ETF tries NASDAQ → NYSE → AMEX fallback. Data cached in Redis (60s quotes, 1h OHLCV).

**Advisory layer** (`src/advisory/`): `MacroRegimeFilter` checks VIX/SPY/200-SMA to classify BULL/BEAR/NEUTRAL. `PositionSizer` calculates volatility-target sizing using ATR.

**HITL flow**: Signal → `signals` table (PENDING) → Redis pub/sub → Telegram alert with APPROVE/REJECT buttons → `ApprovalManager.process_approval()` → broker execution → `trades` table + `audit_logs`.

## Key Config

- **9Router**: Agents use `settings.LLM_BASE_URL` / `LLM_AUTH_TOKEN` / `LLM_MODEL` (resolved from `9ROUTER_*` env vars, falling back to `ANTHROPIC_*`). `NROUTER_ENABLED` flag controls routing.
- **Combo override**: `Orchestrator` sets `combo_name = settings.NROUTER_MODEL` on all agents, so a single 9Router combo handles all LLM routing.
- **Telegram**: Polling mode by default (no domain needed). Set `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`. Set `TELEGRAM_WEBHOOK_URL` for webhook mode.
- **Database**: PostgreSQL via asyncpg + SQLAlchemy async. Schema in `db/init.sql` auto-applied on first start.
- **Trading params**: `MAX_PORTFOLIO_RISK_PCT` (2%), `MAX_POSITION_SIZE_PCT` (15%), `DAILY_LOSS_LIMIT_PCT` (5%).

## File Map (non-obvious)

- `src/agents/orchestrator.py` — universe lists (IDX_UNIVERSE, US_UNIVERSE, ETF_UNIVERSE), combo name assignment, parallel market scan, signal persistence to DB
- `src/agents/base.py` — Anthropic SDK tool-use loop, `getattr()` guards for 9Router response quirks
- `src/bot/handlers.py` — Telegram command handlers, `parse_decimal()` for locale-safe number parsing, `_reply()` with auto-timestamps, inline keyboard routing via `button_callback()`
- `src/data/cache.py` — Redis wrapper with pub/sub for signal/approval channels
- `src/data/mcp_client.py` — `tradingview_ta.TA_Handler` wrapper, get_quote/get_ohlcv/get_indicators methods
- `src/models/tables.py` — SQLAlchemy ORM: PortfolioState, CashBalance, Signal, PaperPosition, ClosedPaperTrade, AuditLog, OHLCVCache, MarketHoliday, PendingApproval
- `src/models/database.py` — async engine + session factory, `init_db()` creates tables
- `src/advisory/regime.py` — `MacroRegimeFilter`: VIX/SPY/200-SMA regime classification (BULL/BEAR/NEUTRAL)
- `src/advisory/sizing.py` — `PositionSizer`: volatility-target sizing using ATR
- `src/utils/rate_limit.py` — Lua-based token bucket in Redis
- `src/utils/telegram_helpers.py` — `format_pre_table()` for aligned ASCII tables, `send_long_message()` with 4096-char chunking, `build_nav_keyboard()` for inline keyboards
- `src/utils/market_hours.py` — `is_idx_open()`, `is_us_open()` market hours checks
- `src/agents/portfolio_analyst.py` — Analyzes holdings vs market data, suggests actions (no execution)
- `src/backtest/engine.py` — RSI + Bollinger mean reversion backtester (Sharpe > 1.2 gate)
- `monitoring/` — Prometheus + Grafana configs
- `docs/` — Design docs, audit results, feature roadmap
- `graphify-out/` — committed knowledge graph; query before reading source files

## Gotchas

- Dockerfiles `COPY src/` — must `--build` after code changes, `restart` alone won't pick up new code.
- `tradingview_ta` is imported lazily inside the container (not at module level) to avoid startup failures if the package isn't installed yet.
- IDX lot size is always 100 shares. `IDXBroker` enforces this.
- The `karsa-9router` service exists in `docker-compose.yml` but the system expects the user's own 9Router instance via `host.docker.internal:20128` for local dev. The compose 9router is on port 20129→20128.
- rtk hook only applies to Bash tool calls — Claude Code built-in Read/Grep/Glob bypass it.
- graphify code extraction is fully local (tree-sitter, no API calls); docs/PDFs use the active model session.
- Kill switch threshold is -1.5% daily P&L (not the `DAILY_LOSS_LIMIT_PCT` setting of 5% — different values).
- APScheduler uses `MemoryJobStore` — jobs are stateless and don't survive container restarts.
