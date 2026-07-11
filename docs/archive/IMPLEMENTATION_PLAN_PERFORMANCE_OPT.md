# Implementation Plan: Performance Optimization & Robustness

## Overview

This plan covers 9 optimization areas from the QWEN_RESPONSE_2.md review. Each section maps to specific files and diffs, with priority and dependency ordering.

**Key Finding**: Many proposed features already exist in the codebase. This plan focuses on the **gaps** — what's truly missing or incomplete.

---

## Phase 1: Foundation (No Dependencies)

### 1.1 Fix `evaluate()` Exception Handling in `performance_gate.py`
**File**: `src/risk/performance_gate.py:259`
**Priority**: P0 (safety)
**Effort**: ~10 lines

**Current**: `evaluate()` returns `None` on missing data, `evaluate_all()` silently skips.
**Fix**: Make `evaluate()` never return `None` — always return a `GateResult` with `action=SKIP`.

```python
# In evaluate(), replace bare returns of None with:
return GateResult(
    position_id=pos.id, ticker=ticker, bucket=bucket.value,
    zone=Zone.NOT_YET, action=GateAction.SKIP,
    gain_pct=0.0, hours_held=0.0,
    reason="missing data: entry/current/opened_at"
)
```

In `evaluate_all()`, remove the `if result and result.action != GateAction.SKIP` filter — log all results for observability.

---

### 1.2 Add `_cache.clear()` + `force=False` to `crypto_regime.py`
**File**: `src/advisory/crypto_regime.py:30-48, 299`
**Priority**: P0 (waste reduction)
**Effort**: ~5 lines

**Current**: `_set_cached()` has no `force` parameter. No `force` override on `get_current_regime()`.
**Fix**:
- Add `force: bool = False` parameter to `get_current_regime()`
- Skip cache check when `force=True` (for regime transitions after trades)
- Add `_regime_cache.clear()` method for explicit invalidation

```python
async def get_current_regime(self, force: bool = False) -> dict:
    if not force:
        cached = _get_cached()
        if cached:
            return cached
    # ... rest of method
```

---

### 1.3 Add Cooldown Map + `/half`/`/freeze` Commands
**File**: `src/risk/crypto_risk_manager.py` (new `cooldown_after_losses()`)
**File**: `src/bot/crypto_handlers.py` (new commands)
**Priority**: P0 (risk management)
**Effort**: ~60 lines

**Current**: No per-lost-coin cooldown map. No `/half` or `/freeze` commands.
**Fix**:

In `crypto_risk_manager.py`, add:
```python
# Cooldown map — lost coins blocked for N seconds
_cooldown_map: dict[str, float] = {}
COOLDOWN_SEC = 1800  # 30 min after loss

def mark_cooldown(self, ticker: str) -> None:
    _cooldown_map[ticker] = time.time() + COOLDOWN_SEC

def is_on_cooldown(self, ticker: str) -> tuple[bool, int]:
    expiry = _cooldown_map.get(ticker, 0)
    remaining = int(expiry - time.time())
    return (remaining > 0, remaining)
```

In `evaluate()`, add as Gate 0.5 (before other checks):
```python
# Cooldown check
on_cd, remaining = self.is_on_cooldown(ticker)
if on_cd:
    return self._reject(f"{ticker} on cooldown ({remaining}s remaining after loss)")
```

In `bot/crypto_handlers.py`, add:
- `/half` — set all position risk to 50% of current
- `/freeze` — halt new entries only (don't close existing)

---

### 1.4 Add `session_id` Prefix to Redis Keys in `autonomous_session.py`
**File**: `src/agents/autonomous_session.py`
**Priority**: P1 (data hygiene)
**Effort**: ~30 lines

**Current**: Redis keys like `karsa:auto:config` are global — stale data from crashed sessions pollutes new ones.
**Fix**: Generate a `session_id` on `start()`, prefix all keys with it.

```python
import uuid
session_id = str(uuid.uuid4())[:8]
REDIS_CONFIG = f"karsa:auto:{session_id}:config"
# ... etc
```

Store `session_id` in Redis as `karsa:auto:current_session_id` for lookup.

---

### 1.5 DB Persistence for `session_id` + `config_snapshot`
**File**: `src/agents/autonomous_session.py` + `src/models/tables.py`
**Priority**: P1 (audit trail)
**Effort**: ~20 lines

**Current**: Session DB record exists (`CryptoAutoSession`) but doesn't store `session_id` or full config snapshot.
**Fix**: Add `session_id` column to `CryptoAutoSession`, save config snapshot on `start()`.

---

## Phase 2: Observability & Guard Rails

### 2.1 Prometheus Metric for AI Judge Latency
**File**: `src/metrics/crypto_metrics.py` + `src/main_crypto.py`
**Priority**: P1 (observability)
**Effort**: ~15 lines

**Current**: No metric for AI judge latency.
**Fix**: Add histogram metric, wrap `judge.cheap_pass()` and `judge.escalated_pass()` calls.

```python
# In crypto_metrics.py
AI_JUDGE_LATENCY = Histogram(
    "karsa_ai_judge_latency_seconds",
    "AI judge evaluation latency",
    ["tier"],  # cheap vs escalated
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)
```

In `main_crypto.py:_job_check_performance_gate()`:
```python
import time
t0 = time.monotonic()
judgment = await judge.cheap_pass(position_data)
AI_JUDGE_LATENCY.labels(tier="cheap").observe(time.monotonic() - t0)
```

---

### 2.2 Grafana Panel: `rate(karsa_ai_judge_latency_seconds_sum)`
**File**: `monitoring/grafana/dashboards/karsa-crypto-ops.json`
**Priority**: P2 (dashboard)
**Effort**: ~10 lines JSON

Add panel for AI judge latency visualization.

---

### 2.3 Regime → Performance Gate Integration
**File**: `src/risk/performance_gate.py`
**Priority**: P1 (already partially done)
**Effort**: ~5 lines verification

**Current**: `get_adaptive_checkpoints()` already reads `karsa:volatility_regime` from Redis. `crypto_regime.py` already writes it.
**Status**: ✅ **Already implemented**. Just verify the Redis key is being read correctly.

---

## Phase 3: Signal Processing Optimization

### 3.1 Scan Dedup Window — Verify 4-hour TTL
**File**: `src/agents/orchestrator.py:76-77`
**Priority**: P1 (waste reduction)
**Effort**: Verification only

**Current**: `_signal_cache` is an in-memory dict with 4-hour TTL. `_execute_signal()` in ASM also sets `asm:signal_dedup:{hash}` in Redis with 4-hour expiry.
**Status**: ✅ **Already implemented**. The in-memory cache + Redis TTL dedup is in place.

---

### 3.2 Regime Filter on `orchestrator.scan_all_markets()`
**File**: `src/agents/orchestrator.py`
**Priority**: P1 (waste reduction)
**Effort**: ~10 lines

**Current**: `scan_all_markets()` calls `_scan_crypto_parallel()` which accepts a `crypto_regime` parameter. ASM already passes regime.
**Status**: ✅ **Already implemented** in ASM path. Verify the standalone `_job_scan_crypto` also passes regime.

**Fix**: In `main_crypto.py:_job_scan_crypto()`, pass regime:
```python
async def _job_scan_crypto(self):
    from src.advisory.crypto_regime import CryptoRegimeFilter
    crf = CryptoRegimeFilter(self.mcp, self.redis_client)
    regime = await crf.get_current_regime()
    signals = await self.orchestrator.scan_all_markets("CRYPTO", regime=regime)
```

---

### 3.3 `_scan_crypto_parallel()` Direct Call in ASM
**File**: `src/agents/autonomous_session.py:526-530`
**Priority**: P1 (already done)
**Effort**: Verification only

**Current**: ASM already calls `_scan_crypto_parallel()` directly, skipping `scan_all_markets()`.
**Status**: ✅ **Already implemented**.

---

## Phase 4: Advanced Risk Management

### 4.1 Circuit Breaker in `risk_manager.evaluate()`
**File**: `src/risk/crypto_risk_manager.py:567-595`
**Priority**: P0 (already done)
**Effort**: Verification only

**Current**: `evaluate()` already checks:
- Global cooldown (`karsa:crypto_cooldown`)
- Per-symbol cooldown via `CircuitBreakerManager.check_symbol_cooldown()`
- Trade frequency via `CircuitBreakerManager.check_trade_frequency()`
- Fail-closed on Redis unavailability
**Status**: ✅ **Already implemented**.

---

### 4.2 Trailing Stop Tightening on Regime Shift
**File**: `src/risk/trailing_stop.py` + `src/main_crypto.py`
**Priority**: P2 (enhancement)
**Effort**: ~30 lines

**Current**: No automatic trailing stop tightening when regime shifts from bull to bear.
**Fix**: Add regime transition handler in `_job_update_trailing_stops()`:

```python
# In _job_update_trailing_stops(), after fetching positions:
current_regime = await self.redis_client.get("karsa:crypto_regime_state")
if current_regime == "MACRO_BEAR_MICRO_PULLBACK":
    # Tighten trailing stops by 30% for all open positions
    for pos in active_positions:
        if pos.trailing_stop_price:
            # Move stop closer to current price
            current = float(pos.current_price or 0)
            entry = float(pos.entry_price or 0)
            if pos.side == "Buy" and current > entry:
                tighter = current - (current - entry) * 0.3
                pos.trailing_stop_price = max(float(pos.trailing_stop_price), tighter)
```

---

### 4.3 AI Judge /position Command
**File**: `src/bot/crypto_handlers.py`
**Priority**: P2 (usability)
**Effort**: ~40 lines

**Current**: No `/position` command for detailed position analysis with AI judge.
**Fix**: Add command that fetches open positions, runs performance gate evaluation, and returns formatted results.

---

## Phase 5: Connection & Monitoring

### 5.1 Connection Health Monitoring
**File**: `src/main_crypto.py` + `src/bot/crypto_handlers.py`
**Priority**: P2 (monitoring)
**Effort**: ~20 lines

**Current**: Health endpoint exists at `/health`. Bot health at port 8444.
**Status**: ✅ **Already implemented**. Add more detailed health metrics if needed.

---

### 5.2 Anomaly Detection for Profits
**File**: `src/monitoring/anomaly_detector.py`
**Priority**: P2 (monitoring)
**Effort**: ~15 lines

**Current**: Anomaly detector already checks `daily_pnl`, `drawdown_velocity`, `win_rate`.
**Status**: ✅ **Already implemented**. Add specific profit anomaly checks if needed.

---

## Implementation Order

```
Phase 1 (Foundation) — No dependencies, do first:
├── 1.1 Fix evaluate() exception handling     [P0, ~10 lines]
├── 1.2 Add force=False to regime cache       [P0, ~5 lines]
├── 1.3 Add cooldown map + /half /freeze      [P0, ~60 lines]
├── 1.4 Session ID prefix for Redis keys      [P1, ~30 lines]
└── 1.5 DB persistence for session config     [P1, ~20 lines]

Phase 2 (Observability) — After Phase 1:
├── 2.1 AI Judge latency metric               [P1, ~15 lines]
├── 2.2 Grafana panel for judge latency       [P2, ~10 lines]
└── 2.3 Verify regime → perf gate integration [P1, verify only]

Phase 3 (Signal Processing) — After Phase 1:
├── 3.1 Verify 4-hour scan dedup              [P1, verify only]
├── 3.2 Regime filter on standalone scan      [P1, ~10 lines]
└── 3.3 Verify ASM direct scan call           [P1, verify only]

Phase 4 (Advanced Risk) — After Phase 1:
├── 4.1 Verify circuit breaker in evaluate()  [P0, verify only]
├── 4.2 Trailing stop tightening on regime    [P2, ~30 lines]
└── 4.3 AI Judge /position command            [P2, ~40 lines]

Phase 5 (Monitoring) — After Phase 2:
├── 5.1 Verify connection health monitoring   [P2, verify only]
└── 5.2 Verify anomaly detection              [P2, verify only]
```

---

## Summary: What's Already Done vs What's New

| Feature | Status | Action |
|---------|--------|--------|
| Adaptive checkpoints (volatility regime) | ✅ Done | Verify Redis key flow |
| Scan dedup (4h TTL in-memory + Redis) | ✅ Done | Verify |
| ASM direct scan call | ✅ Done | Verify |
| Circuit breaker in risk evaluate() | ✅ Done | Verify |
| Regime filter on scan | ✅ Done in ASM | Add to standalone scan |
| Connection health monitoring | ✅ Done | Verify |
| Anomaly detection | ✅ Done | Verify |
| AI Judge latency metric | ❌ Missing | Implement |
| Cooldown map for lost coins | ❌ Missing | Implement |
| /half and /freeze commands | ❌ Missing | Implement |
| Session ID prefix for Redis | ❌ Missing | Implement |
| evaluate() exception handling | ⚠️ Partial | Fix |
| force=False on regime cache | ❌ Missing | Implement |
| Trailing stop tightening on regime | ❌ Missing | Implement |
| /position AI judge command | ❌ Missing | Implement |

---

## Risk Assessment

- **Phase 1**: Low risk — defensive changes, no behavioral impact on existing flows
- **Phase 2**: Low risk — additive metrics, no logic changes
- **Phase 3**: Low risk — verification only, minor standalone scan fix
- **Phase 4**: Medium risk — trailing stop tightening changes exit behavior; needs careful testing
- **Phase 5**: Low risk — verification only

## Testing Strategy

1. **Unit tests**: Add tests for `evaluate()` exception paths, cooldown map logic
2. **Integration tests**: Test regime cache force refresh, session ID isolation
3. **Manual tests**: Run ASM in paper mode, verify `/half` and `/freeze` commands
4. **Monitoring**: Deploy AI judge latency metric first, then optimize based on data
