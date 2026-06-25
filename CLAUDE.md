# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**karsa-claude-trading** — A Claude Code-native, multi-market AI trading system targeting IDX (Indonesia), US Equities, and Global ETFs. It utilizes Claude Agent Teams for parallel analysis, 9Router for LLM cost optimization/fallback, and TradingView MCP for unified market data.

## Status

Repository is freshly initialized with no source code, build system, or dependencies. `DESIGN.md` contains the comprehensive architectural blueprint.

## Git

- Remote: `git@github.com-personal:skeithnight/karsa-claude-trading.git`
- Branching: `main` (production), `develop` (integration), `feature/*`, `fix/*`.
- Commits: Strictly follow [Conventional Commits](https://www.conventionalcommits.org/) (e.g., `feat: add IDX foreign flow strategy`, `fix: handle ARA limit edge case`, `chore: update 9router config`).

---

## Governance & Security

As a financial trading system, security and compliance are paramount. Claude must strictly adhere to these rules:

1. **Zero Hardcoded Secrets:** NEVER commit API keys, broker tokens, Telegram tokens, or database passwords to the repository. All secrets must be injected via `.env` files (excluded in `.gitignore`) or Docker Secrets.
2. **Human-in-the-Loop (HITL) Mandate:** No trade execution logic should bypass the Telegram HITL approval flow in live environments. Paper trading modes must be explicitly flagged via environment variables (`TRADING_MODE=paper`).
3. **Immutable Audit Logs:** Database tables for `trade_history` and `audit_logs` are strictly append-only. NEVER write `UPDATE` or `DELETE` SQL queries for these tables.
4. **Market Compliance Hard Limits:** 
   - **IDX:** Always enforce lot size conversions (1 lot = 100 shares). Always check Auto-Rejection Upper (ARA) limits before generating limit orders.
   - **US:** Enforce Pattern Day Trader (PDT) rules if account equity < $25,000.
5. **Secret Isolation:** The `claude-agent` container must never know the actual Anthropic/DeepSeek API keys. It must only communicate with the local `9router` gateway.

---

## Engineering Standards

### Code Quality & Style
- **Language:** Python 3.11+.
- **Formatting & Linting:** Use `ruff` for all formatting and linting. 
- **Type Checking:** Use `mypy` with strict mode enabled. All function signatures must be fully typed.
- **Async-First:** All I/O bound operations (Broker APIs, Telegram bot, Redis, Postgres, MCP calls) MUST be asynchronous using `asyncio`, `aiohttp`, `asyncpg`, and `redis.asyncio`.

### Architecture & Design Patterns
- **MCP-First Data:** NEVER write custom web scrapers for Yahoo Finance or TradingView. All market data must be fetched via the `tradingview-mcp` tools.
- **Pydantic Validation:** All data models, environment variables, and Agent JSON outputs must be validated using `pydantic` (v2+).
- **Agent Design:** Sub-agents (defined in `.claude/agents/`) must be instructed to return strictly formatted JSON. Use Pydantic models in the orchestrator to parse and validate their outputs.
- **Circuit Breakers:** Implement circuit breakers for all external API calls (Brokers, 9Router). If 3 consecutive failures occur, halt the specific subsystem and trigger a Telegram alert.

---

## Workflow & Development

### Adding a New Sub-Agent
1. Create the agent prompt in `.claude/agents/[name].md` with strict JSON output instructions.
2. Define the expected output schema as a Pydantic model in `src/models/agents.py`.
3. Update the Lead Orchestrator's routing logic in `src/orchestrator/` to dispatch tasks to the new agent.
4. Assign the appropriate 9Router combo (`karsa-critical` or `karsa-routine`) based on the agent's cognitive load.

### Testing
- Use `pytest` with `pytest-asyncio` for all tests.
- **Mocking:** NEVER make real API calls to Brokers, 9Router, or the MCP server during unit tests. Use `unittest.mock` or `pytest-mock` to simulate MCP tool results and Broker responses.
- Test edge cases for market rules (e.g., passing a 50-share order for an IDX stock should raise a validation error).

### Local Development & Deployment
- Always test infrastructure changes locally using `docker-compose up --build` before pushing.
- Ensure the `9router-config.yaml` and `.env` files are correctly mapped in `docker-compose.yml`.
- Check container logs (`docker-compose logs -f claude-agent`) to verify 9Router fallback events and MCP connectivity.

---

## Architecture Context

When writing code, keep the following stack in mind:

- **LLM Gateway:** `9Router` (Port 20128). Handles token compression (RTK) and 3-tier fallbacks. Agents point to `http://9router:20128/v1`.
- **Market Data:** `tradingview-mcp` (Port 8080). Provides unified data for IDX (.JK), US, and ETFs.
- **State & Cache:** `Redis` (Port 6379) for fast caching and agent pub/sub.
- **Persistence:** `PostgreSQL` (Port 5432) for immutable audit logs and portfolio state.
- **Execution:** IDX Broker API (IPOT/Mirae) and US Broker API (Alpaca/IBKR).
- **Interface:** `python-telegram-bot` for HITL alerts and approvals.