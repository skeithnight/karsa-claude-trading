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
docker compose up --build   # starts all 5 services

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

1. **karsa-orchestrator** (`src/main.py`) — APScheduler runs 5 cron jobs. Each job dispatches agents via `Orchestrator.scan_all_markets()` which runs IDX/US/ETF analysts in parallel (`asyncio.gather`). Signals ≥60 confidence get risk-checked, then published to Redis.

2. **karsa-telegram-bot** (`src/bot/main.py`) — FastAPI webhook + python-telegram-bot polling. Commands: `/start`, `/status`, `/scan <market> <ticker>`, `/portfolio`, `/trades`. The bot creates its own `Orchestrator` instance for ad-hoc `/scan` commands.

**Agent loop** (`src/agents/base.py`): Each agent is a `BaseAgent` subclass with a system prompt, tool definitions, and an `_handle_tool_call` override. The `run()` method implements the Anthropic SDK tool-use loop — call LLM, process tool calls, repeat until `end_turn`.

**Data flow for tools**: Agent calls tool → `BaseAgent._handle_tool_call()` → specific agent override → `MCPClient` method → `tradingview_ta` (direct Python import, no MCP protocol).

**Market data** (`src/data/mcp_client.py`): Uses `tradingview_ta.TA_Handler` directly (not MCP protocol). IDX uses `screener='indonesia', exchange='IDX'`. US/ETF tries NASDAQ → NYSE → AMEX fallback. Data cached in Redis (60s quotes, 1h OHLCV).

**HITL flow**: Signal → `signals` table (PENDING) → Redis pub/sub → Telegram alert with APPROVE/REJECT buttons → `ApprovalManager.process_approval()` → broker execution → `trades` table + `audit_logs`.

## Key Config

- **9Router**: Agents use `settings.LLM_BASE_URL` / `LLM_AUTH_TOKEN` / `LLM_MODEL` (resolved from `9ROUTER_*` env vars, falling back to `ANTHROPIC_*`).
- **Combo override**: `Orchestrator` sets `combo_name = settings.NROUTER_MODEL` on all agents, so a single 9Router combo handles all LLM routing.
- **Telegram**: Polling mode (no domain needed). Set `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.
- **Database**: PostgreSQL via asyncpg + SQLAlchemy async. Schema in `db/init.sql` auto-applied on first start.

## File Map (non-obvious)

- `src/agents/orchestrator.py` — universe lists (IDX_UNIVERSE, US_UNIVERSE, ETF_UNIVERSE), combo name assignment, parallel market scan
- `src/agents/base.py` — Anthropic SDK tool-use loop, `getattr()` guards for 9Router response quirks
- `src/bot/approval.py` — HITL lifecycle: PendingApproval → Trade → AuditLog, expiry via scheduler
- `src/bot/handlers.py` — Telegram command handlers, `format_trade_alert()` builds inline keyboard
- `src/data/cache.py` — Redis wrapper with pub/sub for signal/approval channels
- `src/utils/rate_limit.py` — Lua-based token bucket in Redis
- `src/backtest/engine.py` — RSI + Bollinger mean reversion backtester (Sharpe > 1.2 gate)
- `graphify-out/` — committed knowledge graph; query before reading source files

## Gotchas

- Dockerfiles `COPY src/` — must `--build` after code changes, `restart` alone won't pick up new code.
- `tradingview_ta` is imported lazily inside the container (not at module level) to avoid startup failures if the package isn't installed yet.
- IDX lot size is always 100 shares. `IDXBroker` enforces this.
- The `karsa-9router` service was removed from docker-compose — the system uses the user's existing 9Router instance via `host.docker.internal:20128`.
- rtk hook only applies to Bash tool calls — Claude Code built-in Read/Grep/Glob bypass it.
- graphify code extraction is fully local (tree-sitter, no API calls); docs/PDFs use the active model session.