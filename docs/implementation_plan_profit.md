# Risk Engine Optimization Plan

This plan implements the recommendations to tighten risk controls, prioritizing fast exits for stagnant or losing trades.

## Proposed Changes

### Component: Time Exits (Step 1)
Fixing the 48-hour hold limit which is too loose for momentum trades.

#### [MODIFY] [position_manager.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/risk/position_manager.py)
- Change `TIME_EXIT_MAX_HOURS` from `48` to `3`.
- Introduce `STAGNATION_EXIT_HOURS = 2` and `STAGNATION_MAX_ABS_PNL = 0.5`.
- Update `check_time_exits()` to evaluate two conditions:
  1. **Hard Time Exit:** If open > 3 hours and gain < `TIME_EXIT_MIN_GAIN_PCT` (1.0%), exit.
  2. **Stagnation Exit:** If open > 2 hours and `abs(gain_pct) < 0.5%`, exit.

---

### Component: Performance Gates (Step 2)
Adding aggressive early checkpoints to prevent early bleeding.

#### [MODIFY] [performance_gate.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/risk/performance_gate.py)
- Update `CHECKPOINTS` for `Bucket.STANDARD`:
  - Add `Checkpoint(after_minutes=15, min_gain_pct=-1.0, reason="std_15m_crash")`
  - Add `Checkpoint(after_minutes=30, min_gain_pct=-0.5, reason="std_30m_thesis_failed")`
- Update `CHECKPOINTS` for `Bucket.MEME`:
  - Adjust existing 15m checkpoint to `min_gain_pct=-1.0`
  - Adjust existing 30m checkpoint to `min_gain_pct=-0.5`

---

### Component: Context & Profit Lock (Step 3)
Refining bucket classification for high-beta assets and securing small wins faster.

#### [MODIFY] [performance_gate.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/risk/performance_gate.py)
- **Open Question**: `classify_bucket(signal_source)` currently only receives a string. To dynamically check 1h price change (>5%) or 24h volume for volatility, we need access to live data. The simplest approach without refactoring the caller is to check if the `ticker` has a `HIGH_VOL` flag in Redis (which is populated by the market data watcher), or we can pass the full `pos` object to `classify_bucket()` to infer volatility if available. *Recommendation: Pass `ticker` to `classify_bucket()` and query the `volatility_regime` from Redis (which the `evaluate()` method already fetches).*

#### [MODIFY] [profit_lock.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/risk/profit_lock.py)
- Update `PROFIT_TIERS`:
  - Lower the breakeven tier threshold: `{"min_r": 0.2, "atr_mult": None, "desc": "fast_breakeven"}`
  - Keep the tighter trail tiers but adjust down slightly to lock in profits earlier (e.g. +0.5R, +1.0R instead of +1.0R, +2.0R).

## Verification Plan
1. Restart the `karsa-crypto-orchestrator` service.
2. The orchestrator will immediately evaluate open positions.
3. Verify that ARB and T positions are closed instantly via the new Time Exit (Stagnation or Hard Time Exit).
4. Verify logs to confirm the new 15m/30m performance gates are triggering for new trades.
