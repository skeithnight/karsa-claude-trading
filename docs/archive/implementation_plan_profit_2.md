# Win Rate Enhancement Plan — Round 2

## Problem Summary

Current system has strong infrastructure but four critical architecture gaps that directly cause losses:

1. **4H EMA confirmation is LLM-advisory, not deterministic** — LLM can ignore it
2. **Time exits (3h) conflict with TP targets (3×ATR, ~6%)** — positions die before they can win
3. **Unknown regime + aggressive profile admits low-quality entries at full 1.0x size**
4. **Trailing Stop Bug (Widens Loss)** — Trailing stop does not enforce a strict one-way ratchet. Volatility spikes cause the stop to move backwards.

---

## P0 — Critical (Do First, Max Impact)

### P0.1: Deterministic 4H EMA Gate in `evaluate()`
**File**: `src/risk/crypto_risk_manager.py`

Currently only enforced as a prompt instruction — LLM can override. Already partially implemented for MACRO_BULL_MICRO_PULLBACK. Extend to ALL LONG signals.

```python
# After Gate 3b (direction check), before confidence gate:
if direction == "LONG" and self.mcp and "Mock" not in self.mcp.__class__.__name__:
    try:
        ohlcv_4h = await self.mcp.get_ohlcv(ticker, "CRYPTO", timeframe="4h", limit=60)
        if ohlcv_4h and len(ohlcv_4h) >= 50:
            ema20 = calculate_ema(ohlcv_4h, period=20)["ema"]
            ema50 = calculate_ema(ohlcv_4h, period=50)["ema"]
            if ema20 and ema50 and ema20 < ema50:
                if confidence < 82:
                    return self._reject(f"4H EMA bearish ({ema20:.2f} < {ema50:.2f}). Need >=82 confidence")
    except Exception as e:
        logger.warning("4h_ema_gate_failed", ticker=ticker, error=str(e))
```

Expected impact: **eliminates counter-trend longs** — single largest win rate driver.

---

### P0.2: RSI Overbought/Oversold Gate at Entry
**File**: `src/risk/crypto_risk_manager.py`

Crypto auditor already filters RSI > 85 pre-LLM. Lower threshold at risk gate level (no extra API call — reuse 1H OHLCV already fetched for ATR):

```python
if ohlcv_1h and len(ohlcv_1h) >= 14:
    rsi_result = calculate_rsi(ohlcv_1h, period=14)
    current_rsi = rsi_result.get("rsi")
    if current_rsi:
        if direction == "LONG" and current_rsi > 72:
            return self._reject(f"RSI overbought ({current_rsi:.1f} > 72). Chasing exhausted move")
        if direction == "SHORT" and current_rsi < 28:
            return self._reject(f"RSI oversold ({current_rsi:.1f} < 28). Chasing oversold bounce")
```

Expected impact: **blocks exhaustion entries** — high false positive source.

---

### P0.3: Fix Unknown Regime — 0.5x Size, Not 1.0x
**File**: `src/risk/crypto_risk_manager.py` (2 lines)

```diff
- size_multiplier = regime.get("size_multiplier", 1.0)
+ size_multiplier = regime.get("size_multiplier", 0.5)
```

Plus add gate:
```python
if regime_state in ("UNKNOWN", None) and confidence < 80:
    return self._reject("Unknown regime: confidence >= 80 required")
```

Expected impact: **eliminates full-size unknown regime trades**.

---

### P0.4: Fix Time Exit vs TP Target Mismatch
**File**: `src/risk/position_manager.py`

Current exits kill at 3h with < 1% gain, but TP target is 6% (3×ATR). They work against each other.

| Setting | Current | New |
|---|---|---|
| Stagnation exit | 2h / abs < 0.5% | 4h / abs < 0.3% |
| Hard time exit | 3h / gain < 1% | 6h / gain < 0.5% |
| FULL_ALIGNMENT bonus | — | 12h hard exit |
| MICRO_BREAKOUT | — | 4h hard exit |

Expected impact: **lets 4H setups develop** — resolves the core TP/time exit conflict.

---

### P0.5: Trailing Stop One-Way Ratchet (Bug Fix)
**File**: `src/risk/trailing_stop.py`

*Found during deeper audit loop*: The `new_trail_stop` is calculated dynamically via `new_highest - trail_distance`. If ATR expands (volatility spikes) or regime changes to one with a wider multiplier, the stop loss can actually move **downwards**, widening the loss. Trailing stops should only ever move in the direction of profit.

```python
# Before amending stop on Bybit:
if old_stop:
    if pos.side == "Buy":
        new_trail_stop = max(new_trail_stop, Decimal(str(old_stop)))
    else:
        new_trail_stop = min(new_trail_stop, Decimal(str(old_stop)))
```

Expected impact: **Secures profits from being given back** — stops the trailing stop from moving backwards.

---

## P1 — High Priority

### P1.1: Volume Surge Gate for Breakout Signals
**File**: `src/risk/crypto_risk_manager.py`

Reuse 1H OHLCV already fetched — zero extra API cost:

```python
BREAKOUT_STRATEGIES = {"SQUEEZE_BREAKOUT", "MOMENTUM_BURST", "MICRO_BREAKOUT_NO_MACRO"}
if signal.get("_signal_source") in BREAKOUT_STRATEGIES and ohlcv_1h and len(ohlcv_1h) >= 21:
    volumes = [c.get("volume", 0) for c in ohlcv_1h[-21:-1]]
    avg_vol = sum(volumes) / len(volumes) if volumes else 0
    current_vol = ohlcv_1h[-1].get("volume", 0)
    if avg_vol > 0 and current_vol < avg_vol * 1.3:
        return self._reject(f"Breakout without volume: {current_vol:.0f} < 1.3x avg {avg_vol:.0f}")
```

Expected impact: **filters fakeouts** — confirms real participation.

---

### P1.2: Raise Universe Scorer `min_score` 45 → 55
**File**: `src/advisory/universe_scorer.py`

```diff
- async def rank_candidates(self, tickers, min_score: float = 45.0)
+ async def rank_candidates(self, tickers, min_score: float = 55.0)
```

Expected impact: **reduces LLM scan pool from ~12 to ~6-8 higher-quality candidates per cycle**.

---

### P1.3: Tighten Meme `HARD_FAIL` Threshold to -3%
**File**: `src/risk/performance_gate.py`

```python
BUCKET_HARD_FAIL = {
    "MEME": -3.0,     # was inheriting global -8%
    "MICRO": -5.0,
    "STANDARD": -7.0,
    "CORE": -8.0,
}
bucket = signal.get("bucket", "STANDARD")
hard_fail_threshold = BUCKET_HARD_FAIL.get(bucket, -8.0)
```

Expected impact: **stops AI judge loop bleeding meme losses from -1% checkpoint to -8% before force exit**.

---

### P1.4: MACD Histogram Polarity Check
**File**: `src/risk/crypto_risk_manager.py`

Reuse 1H OHLCV (no cost):

```python
if ohlcv_1h and len(ohlcv_1h) >= 26:
    macd_result = calculate_macd(ohlcv_1h)
    histogram = macd_result.get("histogram", 0)
    if direction == "LONG" and histogram < 0 and confidence < 78:
        return self._reject(f"MACD bearish momentum (histogram={histogram:.4f})")
    if direction == "SHORT" and histogram > 0 and confidence < 78:
        return self._reject(f"MACD bullish momentum (histogram={histogram:.4f})")
```

Expected impact: **filters entries on wrong side of momentum turn**.

---

### P1.5: Restore Live BTC Dominance
**File**: `src/advisory/crypto_regime.py`

Currently hardcoded 58.0 — alt season sizing multiplier is dead code.

```python
async def _get_btc_dominance(self) -> dict:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get("https://api.alternative.me/v2/global/")
            dom = float(r.json()["data"]["bitcoin_percentage_of_market_cap"])
            return {"btc_dominance": dom}
    except Exception:
        return {"btc_dominance": 58.0}  # fallback
```

Expected impact: **re-activates alt season sizing multiplier** (currently dead code).

---

## P2 — Medium Priority

| Enhancement | File | Change |
|---|---|---|
| Min trailing in CHOP | `trailing_stop.py` | CHOP multiplier 0 → 0.5 (never fully disabled) |
| Fast-trail on reversal | `trailing_stop.py` | If price < peak by 2%, halve trail distance (0.75×ATR) |
| SQUEEZE_ALERT trigger confirm | `coin_regime.py` | Require price close > upper BB 15m before LONG |
| Cache TTL 900→180 in HIGH_VOL | `coin_regime.py` | Prevent stale TREND_BULL during fast reversals |
| Signal dedup TTL 4h → 6h | `autonomous_session.py` | Prevent same failed setup in same session |

---

## P3 — Nice To Have

| Enhancement | File | Change |
|---|---|---|
| CLEAR_WIN ratio 0.3 → 0.4 | `performance_gate.py` | Lock 40% of peak gain (was 30%) |
| Sector cap max_per_sector 2 → 1 | `universe_scorer.py` | Diversify signal pool across sectors |
| OI rising check before scale-in | `position_manager.py` | Only pyramid if OI increasing |

---

## Summary Table

| Category | Issue | Severity |
|---|---|---|
| Entry | 4H EMA not deterministically enforced | 🔴 Critical |
| Entry | Unknown regime gets 1.0x size | 🔴 Critical |
| Exit | Time exit (3h) vs TP (6%) conflict | 🔴 Critical |
| Exit | Trailing Stop moves backwards | 🔴 Critical |
| Entry | RSI overbought gate missing | 🟠 High |
| Entry | Volume surge not gated | 🟠 High |
| Entry | BTC dominance hardcoded stale | 🟠 High |
| Exit | Meme hard_fail too loose (-8%) | 🟠 High |
| Filter | min_score too low (45) | 🟠 High |
| Entry | MACD direction not checked | 🟡 Medium |
| Exit | Trailing disabled in CHOP | 🟡 Medium |
| Filter | SQUEEZE_ALERT no trigger confirm | 🟡 Medium |

---

## Open Questions

> [!IMPORTANT]
> 1. **Time exit policy**: Flat 6h for all regimes, or regime-aware (FULL_ALIGNMENT=12h, MICRO=4h, TREND_BULL=8h)?
> 2. **4H EMA gate hardness**: Hard reject outright, or soft require confidence>=82 (allows high-conviction counter-trend)?
> 3. **Volume gate scope**: All entries, or only breakout strategies? (Dip buying doesn't need volume surge)
> 4. **BTC dominance source**: Alternative.me free (no key), or just hardcode current actual ~54%?
