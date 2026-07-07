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

## Deterministic Modules (not LLM agents)

**Risk** (`src/risk/`): `emergency.py` (kill switch), `idx_limits.py` (IDX compliance/ARA-ARB), `crypto_risk_manager.py` (8 gates), `sor.py` (Smart Order Router), `funding_tracker.py`, `circuit_breaker.py`, `liquidity.py`, `position_manager.py` (partial/time exits), `position_sync.py` (reconciliation), `trailing_stop.py`, `profit_lock.py`, `distributed_lock.py`.

**Advisory** (`src/advisory/`): `regime.py` (BULL/BEAR/NEUTRAL), `idx_intelligence.py` (composite scoring), `sizing.py` (ATR position sizing), `crypto_regime.py` (Hurst+ADX macro regime), `coin_regime.py` (per-coin regime), `crypto_technicals.py` (RSI/BB/EMA/MACD/ATR), `crypto_universe.py` (pair config source of truth), `crypto_audit.py`, `crypto_market_watch.py`, `performance_tracker.py`, `strategy_selector.py`.

**Utilities**: `mcp_client.py` (market data, 3-tier fallback), `bybit_client.py` (Bybit REST), `_approval.py` (HITL Telegram flow), `format.py`, `validation.py`, `market_hours.py`, `feature_flags.py`.

Full detail: `docs/reference/AGENTS_DETAIL.md`