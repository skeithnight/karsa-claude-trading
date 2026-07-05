# Profit Protection & Dynamic Exit Engine

## Problem Statement

Current ASM has 3 critical exit gaps:
1. **No SL verification** — positions can exist without stop-loss (TAOUSDT case: no SL, no trailing stop, no partial exits, no SL engine coverage)
2. **Trailing stops are passive** — only amend existing stops, won't create missing ones
3. **Partial exits depend on SL** — can't calculate R-multiple without a stop, so the +1R exit never fires for positions missing SL
4. **No profit lock mechanism** — once in profit, nothing protects gains beyond the fixed TP

## Architecture

```
Entry → Risk Manager (SL/TP calc) → SOR (place orders) → Verify SL Exists
                                                                    ↓
                                                          ┌─ SL Engine (WS-driven, sub-second)
                                                          ├─ Trailing Stop Manager (5min, regime-aware)
                                                          ├─ Partial Exit Manager (1R = 50%)
                                                          └─ Profit Lock Engine (new: tiered profit protection)
```

## Implementation Plan

### Phase 1: SL Verification & Recovery (Critical — Fixes TAOUSDT Case)

**File:** `src/risk/position_manager.py` — new method `verify_and_recover_sl()`

**What it does:**
- After every ASM entry, verify Bybit actually has an active SL order
- If SL is missing (Bybit rejected it, API timeout, etc.), recalculate and place it
- Log discrepancy for monitoring

**Changes:**
1. Add `verify_and_recover_sl(positions)` to `PositionManager`
2. Call it in `main_crypto.py` after each scan cycle (alongside trailing stops)
3. Query Bybit for open stop orders on each position's symbol
4. If no SL found: recalculate using current ATR, place via `bybit.set_stop_loss()`
5. Update DB `CryptoPosition.trailing_stop_price` to match

```python
async def verify_and_recover_sl(self, positions: list[CryptoPosition]) -> list[dict]:
    """Verify all positions have active SL orders. Recover missing ones."""
    recoveries = []
    for pos in positions:
        if pos.status != "OPEN":
            continue
        # Check Bybit for active stop orders
        active_stops = await self.bybit.get_open_orders(pos.ticker, stop_order=True)
        has_sl = any(o.get("stopOrderType") == "StopLoss" for o in active_stops)
        
        if not has_sl:
            # Recalculate SL from ATR
            atr = await self._get_current_atr(pos.ticker)
            sl_price = self._calculate_sl(pos, atr)
            await self.bybit.set_stop_loss(pos.ticker, sl_price, pos.side)
            # Update DB
            pos.trailing_stop_price = sl_price
            await session.commit()
            recoveries.append({"ticker": pos.ticker, "recovered_sl": sl_price})
            logger.warning("sl_recovered", ticker=pos.ticker, sl=sl_price)
    return recoveries
```

**Scheduler:** Add `_job_verify_sl` running every 5min alongside trailing stops.

---

### Phase 2: Dynamic Profit Lock Engine (New File)

**File:** `src/risk/profit_lock.py` — new module

**What it does:**
- Tiered profit protection that tightens stops as unrealized gain increases
- Moves stop to breakeven at +1R, then trails at tighter ATR as profit grows

**Tiers:**
| Gain Zone | Action | Stop Placement |
|---|---|---|
| < +0.5R | Hold original SL | ATR-based (unchanged) |
| +0.5R to +1R | Move SL to breakeven | Entry price |
| +1R to +2R | Tight trail | Current price - 1.0x ATR |
| +2R to +3R | Medium trail | Current price - 0.75x ATR |
| > +3R | Tight trail | Current price - 0.5x ATR |

```python
class ProfitLockManager:
    """Tiered profit protection — tightens stops as gain increases."""
    
    PROFIT_TIERS = [
        {"min_r": 0.5, "stop_formula": "breakeven", "desc": "move to entry"},
        {"min_r": 1.0, "stop_formula": "trail_1_0_atr", "desc": "tight trail 1.0x ATR"},
        {"min_r": 2.0, "stop_formula": "trail_0_75_atr", "desc": "medium trail 0.75x ATR"},
        {"min_r": 3.0, "stop_formula": "trail_0_5_atr", "desc": "tight trail 0.5x ATR"},
    ]
    
    async def check_profit_locks(self, positions: list[CryptoPosition]) -> list[dict]:
        """Check all positions for profit lock triggers."""
        ...
    
    def _calculate_lock_stop(self, pos, r_multiple: float, atr: float) -> float:
        """Calculate stop price based on profit tier."""
        ...
```

**Scheduler:** Runs inside `_job_update_trailing_stops` — profit lock is a trailing stop variant, not a separate job.

---

### Phase 3: Enhanced Trailing Stop (Modify Existing)

**File:** `src/risk/trailing_stop.py` — modify `update_trailing_stops()`

**What it does:**
- If position has no trailing stop but has a Bybit SL, adopt it as the trailing baseline
- If position has neither, create one from ATR (recovery path)
- Add regime-aware tightening (current: only adjusts multiplier, doesn't adapt distance)

**Changes:**
1. In `update_trailing_stops()`: if `pos.trailing_stop_price` is None, try to read from Bybit active orders
2. If still None, calculate from ATR and place via Bybit
3. Add "high water mark" tracking — only trail upward for longs, downward for shorts
4. Integrate profit lock tiers as the trailing distance formula

---

### Phase 4: Partial Exit Fix (Modify Existing)

**File:** `src/risk/position_manager.py` — modify `check_partial_exits()`

**What it does:**
- If SL is missing, calculate R-multiple from the *recovered* SL (from Phase 1)
- This unblocks partial exits for positions like TAOUSDT

**Changes:**
1. In `check_partial_exits()`: if `pos.stop_loss` is None but `pos.trailing_stop_price` is set, use trailing stop as the risk reference
2. If both are None, skip (Phase 1 should have recovered it by now)

---

### Phase 5: Position Entry SL Audit (Safety Net)

**File:** `src/main_crypto.py` — add `_job_verify_sl`

**What it does:**
- Dedicated scheduler job that runs every 5min
- Checks all open positions have active Bybit SL orders
- Auto-recovers missing ones
- Alerts via Telegram if recovery was needed

**Scheduler entry:**
```python
self._scheduler.add_job(
    self._job_verify_sl, "interval", minutes=5,
    id="sl_verification", name="SL Verification",
    replace_existing=True,
)
```

---

## Files Modified

| File | Change |
|---|---|
| `src/risk/position_manager.py` | Add `verify_and_recover_sl()`, fix partial exit SL fallback |
| `src/risk/profit_lock.py` | **New** — tiered profit lock engine |
| `src/risk/trailing_stop.py` | Adopt orphaned stops, integrate profit lock tiers |
| `src/main_crypto.py` | Add `_job_verify_sl` scheduler, call profit lock in trailing stop job |
| `src/models/tables.py` | Add `profit_lock_tier` column to `CryptoPosition` (optional) |

## What This Fixes

- **TAOUSDT case**: SL would be recovered within 5min of being detected missing
- **Profit protection**: Once in profit, stops automatically tighten — no more open-ended positions
- **Partial exits**: Work for all positions (with or without original SL)
- **SL engine coverage**: Every position always has an active SL order on Bybit

## What This Does NOT Do (YAGNI)

- ML-based reversal prediction (from ASM_Enhancements.md #5) — defer until live data proves the need
- Kelly Criterion dynamic sizing (from ASM_Enhancements.md #1) — separate concern, existing sizing works
- TWAP/VWAP execution (from ASM_Enhancements.md #4) — current market orders are fine for small size
- HMM regime detection (from ASM_Enhancements.md #3) — current Hurst+ADX works, improve later

## Implementation Order

1. **Phase 1** (SL verification) — highest impact, fixes the actual bug
2. **Phase 5** (scheduler job) — pairs with Phase 1
3. **Phase 4** (partial exit fix) — quick win, unblocks existing logic
4. **Phase 2** (profit lock) — new capability, most value
5. **Phase 3** (trailing stop enhancement) — integrates profit lock

**Estimated effort:** 2-3 sessions for Phases 1+5+4, 1-2 sessions for Phase 2+3.
