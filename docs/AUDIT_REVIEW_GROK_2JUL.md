# QA Report: REVIEW_GROK_2JUL.md Implementation

**Date:** 2026-07-02  
**Scope:** 6 steps from `docs/REVIEW_GROK_2JUL.md`  
**Verdict:** PASS — 1 bug found and fixed during QA

---

## Changes Summary

### New Files (5)
| File | Lines | Purpose |
|------|:---:|---------|
| `src/main_crypto.py` | ~300 | Crypto-only orchestrator (port 8001, 19 jobs) |
| `src/risk/calibration_engine.py` | ~85 | Confidence calibration multiplier [0.5, 1.5] |
| `src/backtest/perp_simulator.py` | ~200 | Perp backtester (funding, slippage, fees, liquidation) |
| `db/migrations/add_pgvector_memory.sql` | ~20 | pgvector extension + trade_memory table |
| `src/agents/memory_retriever.py` | ~85 | RAG memory retriever (pgvector cosine search) |

### Modified Files (7)
| File | Changes |
|------|---------|
| `src/config.py` | `CRYPTO_ONLY_MODE` flag |
| `docker-compose.yml` | `karsa-crypto-orchestrator`, `prometheus`, `grafana`, pgvector image |
| `src/agents/orchestrator.py` | `calibrator` attr + calibration at 2 confidence gates |
| `src/agents/crypto_analyst.py` | `run()` override with memory injection |
| `src/main.py` | Calibrator instantiation |
| `src/data/bybit_client.py` | `get_open_orders()` (from QWEN audit) |
| `src/backtest/perp_simulator.py` | `from __future__ import annotations` (QA fix) |

---

## Bug Fixed

### ⚠️ F1: Python 3.9 type syntax — FIXED
- `list[float] | None` in `perp_simulator.py` and `memory_retriever.py`
- Fix: `from __future__ import annotations`

---

## Test Results

### Perp Backtester (8/8 passed)
- ✅ Empty data → 0% return
- ✅ No signals → 0 trades
- ✅ Winning long → PnL=445.36, fees=3.54
- ✅ Stop loss → exit_reason=stop_loss
- ✅ Funding costs → total=1.0190
- ✅ Liquidation detection
- ✅ Short trade profit → PnL=557.29
- ✅ Stats (win_rate, max_dd, sharpe)

### Calibrator (7/7 passed)
- ✅ No data → multiplier=1.0
- ✅ Default calibrate 80% → 80.0
- ✅ Bounds: floor=0.5, ceil=1.5
- ✅ Cache invalidation
- ✅ Penalty: 80% × 0.5 = 40.0
- ✅ Upper clamp: 90% × 1.5 = 100.0
- ✅ Zero confidence → 0.0

---

## Wiring Verification

| Check | Status |
|-------|:------:|
| Calibrator in main.py + main_crypto.py | ✅ |
| Calibrator at 2 confidence gates in orchestrator | ✅ |
| Memory injection in crypto_analyst.run() | ✅ |
| All BybitClient method calls exist | ✅ |
| All imports resolve | ✅ |
| Docker compose: 8 services | ✅ |
| pgvector image for postgres | ✅ |
| Prometheus + Grafana volumes | ✅ |
| CRYPTO_ONLY_MODE in config | ✅ |

---

## Remaining Considerations

1. **`sentence-transformers` not in Dockerfile**: Add to `Dockerfile.orchestrator` for RAG. Gracefully degrades without it.
2. **Grafana datasource**: Add `monitoring/grafana/provisioning/datasources/prometheus.yml` for auto-provisioning.
3. **Calibrator needs `confidence_score` column**: On `closed_paper_trades` table. Verify exists or add migration.
4. **`CRYPTO_ONLY_MODE` unused in main.py**: Separation via `main_crypto.py` entry point. Add check in `_register_jobs()` if needed.
