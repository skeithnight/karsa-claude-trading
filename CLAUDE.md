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

### Dashboards
- **ASM & Trading Operations** — legacy ops view
- **Trading Operations v2** — full metrics dashboard
- **ASM - Core Operations** (`monitoring/asm-core-operations.json`) — new 9-panel dashboard with live tables and AI Judge analytics

### Prometheus Metrics

80+ metrics defined in `src/metrics/crypto_metrics.py` across 11 domains:

| Domain | Key Metrics | Wired In |
|--------|-------------|----------|
| Performance Gate v2 | `karsa_perf_gate_zone`, `karsa_perf_gate_exit`, `karsa_perf_gate_dynamic_stop_active` | `performance_gate.py` |
| AI Judge | `karsa_ai_judge_decisions_total`, `karsa_ai_judge_tier_used_total`, `karsa_ai_judge_confidence_score`, `karsa_ai_judge_latency_seconds` | `position_judge.py` |
| Regime/Intelligence | `karsa_scan_duration_seconds`, `karsa_crypto_regime`, `karsa_btc_dominance_pct` | `orchestrator.py` |
| Session Performance | `karsa_session_return_pct`, `karsa_profit_factor`, `karsa_total_trades_count` | `autonomous_session.py` |
| Position-Level | `karsa_position_age_hours`, `karsa_funding_cost_8h_usd` | `autonomous_session.py` |
| Risk Safety | `karsa_kill_switch_active`, `karsa_circuit_breaker_active`, `karsa_daily_loss_pct` | `emergency.py`, `circuit_breaker.py` |
| Order Execution | `karsa_order_fill_total`, `karsa_order_slippage_bps`, `karsa_order_fill_latency_seconds` | `sor.py` |
| Infrastructure | `karsa_job_duration_seconds`, `karsa_bybit_api_latency_seconds`, `karsa_redis_connected` | `main.py` |
| LLM & Tokens | `karsa_llm_tokens_input_total`, `karsa_llm_tokens_output_total` | Defined, call `record_llm_tokens()` |
| Signal Outcomes | `karsa_signal_outcome_total` | Defined, call `record_signal_outcome()` |
| Daily Trade Count | `karsa_daily_trade_count` | Defined, call `update_daily_trade_count()` |

Metrics endpoint: `curl http://localhost:8001/metrics` (crypto) or `http://localhost:8000/metrics` (main).

Full wiring status: `docs/METRIC_WIRED.md`. Implementation summary: `docs/METRICS_IMPLEMENTATION_SUMMARY.md`.