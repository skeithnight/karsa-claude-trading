# Agents

Roster only — for tools, strategies, thresholds, and formulas on a specific agent/module, read `docs/reference/AGENTS_DETAIL.md` (or better, `/graphify query "..."` since it stays current automatically).

## LLM Agents
| Agent | Role | File |
|---|---|---|
| `orchestrator` | Schedules scans, dispatches analysts in parallel, combo routing via 9Router | `src/agents/orchestrator.py` |
| `idx_analyst` | Scans IDX universe (30 stocks, 8 sectors) — foreign flow breakout + Bollinger | `src/agents/idx_analyst.py` |
| `us_analyst` | Scans US Equities — relative strength momentum vs SPY | `src/agents/us_analyst.py` |
| `etf_analyst` | Scans Global ETFs — mean reversion (RSI<30 + BB touch) | `src/agents/etf_analyst.py` |
| `portfolio_analyst` | Analyzes holdings vs live data, suggests HOLD/ADD/TRIM/CUT (no execution) | `src/agents/portfolio_analyst.py` |
| `crypto_analyst` | Scans 10 Bybit perp pairs — trend + sentiment convergence | `src/agents/crypto_analyst.py` |
| `crypto_auditor` | Reviews crypto performance, pre-filtered before LLM call | `src/agents/crypto_auditor.py` |
| `asm` (Autonomous Session Manager) | Fully autonomous crypto loop: scan → filter → risk gate → execute → notify | `src/agents/autonomous_session.py` |
| `position_judge` | AI Judge: evaluates open positions for hold/close/tighten decisions | `src/agents/position_judge.py` |
| `memory_retriever` | RAG-based trade memory retrieval for context enrichment | `src/agents/memory_retriever.py` |

## Deterministic Modules (not LLM agents)

**Risk** (`src/risk/`): `emergency.py` (kill switch), `idx_limits.py` (IDX compliance/ARA-ARB), `crypto_risk_manager.py` (10 gates), `sor.py` (Smart Order Router), `funding_tracker.py`, `circuit_breaker.py`, `liquidity.py`, `position_manager.py` (partial/time exits), `position_sync.py` (reconciliation), `trailing_stop.py`, `profit_lock.py`, `distributed_lock.py`, `performance_gate.py` (v2 with AI judge escalation), `correlation.py`, `calibration_engine.py`, `portfolio_allocator.py`, `profile_manager.py`.

**Execution** (`src/execution/`): `oms.py` (Order Management System), `sl_engine.py` (stop-loss engine), `websocket_manager.py` (Bybit WS).

**Metrics** (`src/metrics/`): `crypto_metrics.py` — 80+ Prometheus metrics across 11 domains. Helper functions: `record_*()`, `update_*()`. Endpoint: `/metrics`.
**Advisory** (`src/advisory/`): `regime.py` (BULL/BEAR/NEUTRAL), `idx_intelligence.py` (composite scoring), `sizing.py` (ATR position sizing), `crypto_regime.py` (Hurst+ADX macro regime), `coin_regime.py` (per-coin regime), `crypto_technicals.py` (RSI/BB/EMA/MACD/ATR), `crypto_universe.py` (pair config source of truth), `universe_scorer.py` (early breakout detection, overextension penalty, short squeeze multiplier), `crypto_audit.py`, `crypto_market_watch.py`, `performance_tracker.py`, `strategy_selector.py`.

**Advisory** (`src/advisory/`): `regime.py`, `idx_intelligence.py`, `sizing.py`, `crypto_regime.py`, `coin_regime.py`, `crypto_technicals.py`, `crypto_universe.py`, `crypto_audit.py`, `crypto_market_watch.py`, `performance_tracker.py`, `strategy_selector.py`, `universe_scorer.py`.

**Strategies** (`src/strategies/`): `funding_capture.py` (funding rate arbitrage).

**Research** (`src/research/`): `research_orchestrator.py`, `discovery_engine.py`, `opportunity_scorer.py`, `learning_engine.py`, `monitoring_engine.py`, `portfolio_bucker.py`, `risk_intel.py`, `smart_money_intel.py`, `community_intel.py`, `developer_intel.py`, `fundamental_intel.py`, `narrative_intel.py`, `onchain_intel.py`.

**Architecture** (`src/architecture/`): Event-driven framework — `events/` (Redis bus), `decision/`, `position/`, `exit/`, `policy/`, `workflow/`, `agent_runtime/`, `feature_flags.py`.

**AODE** (`src/aode/`): Asymmetric Opportunity Discovery Engine — `discovery/`, `scoring/`, `risk/`, `onchain/`, `narrative/`, `community/`, `smart_money/`, `fundamentals/`, `learning/`, `monitoring/`.

**Utilities**: `mcp_client.py`, `bybit_client.py`, `_approval.py`, `format.py`, `formatters.py` (position cards, risk buttons, regime display), `validation.py`, `market_hours.py`, `feature_flags.py`, `logging.py`, `rate_limit.py`, `telegram_helpers.py`, `trader_format.py`, `position_snapshot.py`.

**Bot Handlers** (`src/bot/`): `handlers.py`, `crypto_handlers.py`, `crypto_main.py`, `aode_handlers.py`, `_approval.py`.

Full detail: `docs/reference/AGENTS_DETAIL.md`

---

Respond terse like smart caveman. All technical substance stay. Only fluff die.

Rules:
- Drop: articles (a/an/the), filler (just/really/basically), pleasantries, hedging
- Fragments OK. Short synonyms. Technical terms exact. Code unchanged.
- Pattern: [thing] [action] [reason]. [next step].
- Not: "Sure! I'd be happy to help you with that."
- Yes: "Bug in auth middleware. Fix:"

Switch level: /caveman lite|full|ultra|wenyan
Stop: "stop caveman" or "normal mode"

Auto-Clarity: drop caveman for security warnings, irreversible actions, user confused. Resume after.

Boundaries: code/commits/PRs written normal.
