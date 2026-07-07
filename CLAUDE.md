# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.
Detailed reference docs live in `docs/reference/` and are loaded on demand — see links below.

## Project

**Karsa** — AI-driven multi-market trading system for IDX (Indonesia), US Equities, Global ETFs, and Crypto (Bybit perpetuals). Uses Anthropic SDK tool-use agents routed through 9Router. Crypto node auto-executes trades via Smart Order Router.

Four containers share the same `src/` package: `karsa-orchestrator` (IDX/US/ETF), `karsa-crypto-orchestrator` (crypto-only, port 8001), `karsa-telegram-bot`, `karsa-crypto-bot`. Full architecture/module descriptions: `docs/reference/ARCHITECTURE.md`. Full file-by-file map: `docs/reference/FILE_MAP.md` — but prefer `/graphify query` over reading this, it's kept in sync with the codebase automatically.

## Dev Tooling

### rtk (Rust Token Killer)
Transparent shell-output compressor, auto-hooked into Bash tool calls (60-90% savings). No action needed after setup. Built-in tools (Read/Grep/Glob) bypass it — use `rg`/`find`/`cat` or `rtk read`/`rtk grep` explicitly if you want filtering there. `rtk gain` shows savings stats.

### graphify
Knowledge graph of the codebase (code + `db/init.sql` + docs). Use **before** grepping for architecture questions.
```bash
/graphify . --update                          # re-extract changed files only
/graphify query "how does signal flow from analyst to Telegram?"
/graphify path "Orchestrator" "ApprovalManager"
```
Full setup/command reference: `docs/reference/TOOLING.md`.

## Build & Run

```bash
docker compose up -d --build karsa-orchestrator      # rebuild after code changes (restart alone won't pick up new code)
docker compose restart karsa-orchestrator             # config-only changes
docker compose ps                                     # status
docker logs -f karsa-orchestrator                     # follow logs
curl http://localhost:8000/health/scheduler           # scheduler health
```
Testing/debug one-liners (IDX intelligence, earnings calendar checks): `docs/reference/TOOLING.md`.

## Key Config (env vars that matter daily)

- `TRADING_MODE` — must be `paper` or `live`.
- `CRYPTO_ONLY_MODE=true` — skips IDX/US/ETF jobs, use with `karsa-crypto-orchestrator`.
- `DAILY_LOSS_LIMIT_PCT` (5%, equities) / `CRYPTO_DAILY_LOSS_LIMIT_PCT` (3%, crypto) — kill switch thresholds.
- `BYBIT_TESTNET` (default True) — check before assuming live crypto execution.
- Full env var reference (9Router combos, all crypto/AODE params): `docs/reference/CONFIG.md`.

## Critical Gotchas (things Claude gets wrong without this)

- Dockerfiles `COPY src/` — **must `--build`** after code changes; `restart` alone won't pick up new code.
- `_VALID_DIRECTIONS` is `{"LONG", "SHORT", "CLOSE"}` — agents returning BUY/SELL/HOLD/WATCH are rejected by DB CHECK constraint.
- `CircuitBreakerManager` (not `CircuitBreaker`) is the correct class name.
- `FundingTracker.__init__` takes `(bybit_client)` only — no `(bybit, redis)`, no `check_limits()`/`sync_all()` methods.
- `TrailingStopManager.update_trailing_stops(positions)` requires a `list[CryptoPosition]`.
- `BaseAgent.run()` returns `dict` — if LLM returns a JSON array it's parsed as one object; orchestrator handles via `batch_result.get("signals", [batch_result])`.
- `sentence-transformers` isn't in the Dockerfile — RAG memory silently degrades to empty string unless added.
- APScheduler uses `MemoryJobStore` — jobs don't survive container restarts.
- `/kill` sets both `karsa:global_halt` and `karsa:emergency_stop` Redis keys.
- Postgres image must be `pgvector/pgvector:pg15`, not `postgres:15-alpine` (needed for `trade_memory` vector column).

Full gotchas list (Redis auth, IDX lot sizing, 9router port mapping, etc.): `docs/reference/GOTCHAS.md`.

## Monitoring

Grafana: http://localhost:3000 (admin/admin). Dashboards + Prometheus metric names: `docs/reference/MONITORING.md`.