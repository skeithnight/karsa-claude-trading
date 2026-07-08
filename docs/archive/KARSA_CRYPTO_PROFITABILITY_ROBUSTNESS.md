# Karsa Crypto Perpetuals — Profitability & Robustness Design

**Status:** Draft for review
**Scope:** Bybit USDT-M perpetuals subsystem (`karsa-claude-trading`)
**Depends on:** `KARSA_CRYPTO_AUDIT_2026.md`, `KARSA_CRYPTO_IMPROVEMENT_DESIGN.md`
**Principle:** Nothing in Phase 1+ is trusted until Phase 0 is verified live in code, not just documented as live.

---

## 0. Framing

The system currently cannot answer "is this profitable?" because P&L is not reliably recorded, the kill switch is disconnected, and reconciliation/close-verification status is unconfirmed. Profitability work built on top of unverified plumbing produces numbers that look good and mean nothing. This document is ordered so that trustworthiness is a hard prerequisite for every optimization that follows.

Target outcome is **consistent, risk-adjusted, compounding edge** — not maximum return. Leverage and sizing decisions throughout this design are deliberately conservative, because a system optimized for "get rich fast" tends to raise concentration/leverage to compensate for a thin edge, which is the direct path to a blown account.

---

## 1. Phase 0 — Instrumentation & Safety (Blocking)

No Phase 1+ item should be merged or trusted until every item here is confirmed live with a test, not just present in code.

| # | Item | Confirmed Status | Root Cause | Required Fix | Verification |
|---|------|-------------------|------------|---------------|---------------|
| 0.1 | PnL recording | 🔴 BROKEN | `ClosedPaperTrade` never instantiated — fees/funding/leverage untracked | Patch post-trade hook in OMS: compute realized PnL incl. fees + funding, instantiate `ClosedPaperTrade`, commit before state reset | Unit test: simulate a close, assert row exists in DB with correct PnL math |
| 0.2 | Kill switch | 🔴 BROKEN | Dead code, not wired into main loop | Inject kill-switch evaluation into OMS execution loop + 1-min APScheduler job; on trigger, cancel all open orders and flatten all positions via REST | Integration test: force-trigger switch, assert all positions flattened within one cycle |
| 0.3 | Startup reconciliation | 🔴 BROKEN (stub) | `_job_reconcile_positions` only checks that a position ID exists on the exchange — does not diff `size`, `avg_price`, or `liq_price`. On restart mid-trade, the bot blindly trusts local DB, causing silent state drift | Rewrite reconciliation with **tiered response by drift severity** — see below | Test: manually desync DB from a testnet position, restart bot, assert correct tier response |
| 0.4 | Position close verification | 🔴 BROKEN | No confirmation a close order actually filled before recording PnL | Add explicit `verify_close_filled()` step between order-close and PnL-record steps | Test: simulate partial/failed close, assert PnL is NOT recorded until fill confirmed |
| 0.5 | TP-side partial fill (scale-out) | 🔴 BROKEN (state drift) | OMS sends the partial close order to Bybit successfully, but fails to update `size`/`unrealized_pnl` on the local `OpenPosition` SQLAlchemy model. DB believes the position is still 100% open, which silently corrupts the correlation gate (2.4) and vol-based sizing (2.3) since both read position size from the DB | Update `OpenPosition.size` and `unrealized_pnl` synchronously on partial-fill confirmation, in the same transaction as the fill event | Test: simulate 50% TP fill, assert DB position size updates immediately and correlation/sizing models read the corrected value |

**Exit criterion for Phase 0:** All five items pass their verification test in a staging/testnet environment before any Phase 1 signal work is deployed live.

**Note on 0.3 and 0.5:** both are confirmed *state-integrity* bugs, not just missing features — they cause the DB to silently disagree with the exchange. This makes them higher priority than their original numbering suggests, since Phase 1's correlation gate (2.4) and vol-scaled sizing (2.3) both depend on `OpenPosition.size` being correct in real time. Recommend fixing 0.5 before 0.3 in implementation order, since it's the more actively corrupting bug during normal (non-restart) operation.

**0.3 detail — tiered drift response.** Blind auto-correction of active position state is dangerous: it can double a position or close the wrong side if the exchange API glitches or an OMS bug is the actual root cause. Response tier depends on what kind of drift is found:

| Drift type | Response |
|---|---|
| Exchange shows closed, DB shows open, no pending close-order | **Auto-correct.** Close the DB record, log it. Safe because it only removes stale state. |
| `unrealized_pnl`, `mark_price`, `liq_price` differ (read-only fields) | **Auto-sync.** These don't affect position identity, safe to overwrite from exchange truth. |
| `size`, `side`, or `avg_price` differ | **Halt-and-alert.** Implies a missed fill, manual intervention on the exchange, or an OMS bug. Halt new entries, cancel open orders for that symbol, alert operator. Do not auto-resize. |
| DB shows closed, exchange shows open | **Halt-and-alert.** Same handling as above. |
| **Exchange shows an open position with no matching DB record at all (open or closed)** | **Halt-and-alert — treat as highest severity.** This is an untracked live position with real liquidation exposure the bot doesn't know to monitor (e.g. a manual trade placed on the exchange UI, or leftover state from a DB migration/wipe). Halt all new entries account-wide, alert immediately, do not touch the position pending operator review. |

This introduces an accepted design constraint: critical drift resolution requires a human in the loop. "Autonomous" trading still has a manual-intervention failure mode by design — this is treated as correct behavior given the stakes, not a gap to engineer away.

---

## 2. Phase 1 — Structural Edge in Perpetuals

These are levers specific to perp mechanics, not just "better entries."

### 2.1 Funding rate as a first-class strategy input
Currently funding only feeds a scorer bonus for short squeezes. Funding is a mechanical, quasi-mean-reverting cost/income stream — one of the few repeatable edges in perp markets because it isn't predictive, it's structural.

- Add a **funding-capture sub-strategy**: identify tokens with persistently extreme funding (crowded positioning) and take the funding-favorable side, sized independently of the momentum book.
- Track funding P&L separately from price P&L in the ledger, so the two edges can be evaluated independently.

**Capital allocation — dynamic extremity scaling:**

| Condition | Allocation of total risk budget |
|---|---|
| Base allocation | 10% |
| Annualized funding rate > +15% or < -15% | Scale up to 25% |
| Hard cap | 3 concurrent funding-book positions, regardless of allocation % |

The concurrency cap exists independently of the allocation % to prevent over-concentration in a single funding regime (e.g. one sector-wide funding squeeze looking like three "diversified" opportunities).

**Leverage cap — strictly separate from the momentum book.** Funding-capture is a carry trade: it often requires holding through adverse price action while waiting for funding payout or crowd unwind, unlike momentum trades which rely on tight ATR stops to cut losses fast. At momentum-book leverage (3-5x), a routine 20% altcoin wick liquidates the position before the funding edge ever pays out.

- **Cap: 2x maximum** for the funding-capture book, independent of whatever leverage the momentum book uses.
- If the strategy needs higher leverage to be profitable at 2x, it must be restructured as delta-neutral spot-perp basis arbitrage — a different execution engine, out of scope for this phase.

### 2.2 Cost-aware risk gate
No explicit gate currently models total cost of a trade before entry.

- Add a gate: `expected_edge > (taker_fee_roundtrip + expected_slippage + expected_funding_drag_over_holding_period)`
- Slippage estimate should scale with order size vs. current order-book depth/24h volume, not be a flat constant.
- This gate sits **before** position sizing — a signal that fails it should never reach the OMS.

### 2.3 Volatility-scaled position sizing
If leverage/size is currently static per trade, replace with:

- ATR-based (or realized-vol-based) position sizing so each trade risks a constant **% of equity**, not a constant notional or fixed leverage.
- Feed the ML Regime Detector's output into the sizing function directly (e.g. reduce size in "volatile/choppy" regime, allow full size in clean trend regime) — currently regime only gates entry, not size.

### 2.4 Portfolio-level correlation risk
The 9-layer gate checks trades individually. Add a portfolio-level check:

- Before approving a new position, compute correlation of the candidate token's recent returns against currently-open positions.
- **Metric: downside (tail) correlation, not standard Pearson.** Standard Pearson understates crash risk in crypto — assets can show ~0.4 correlation on green days but ~0.95 on red days, since alts dump together. Compute correlation using only the return hours where BTC dropped > 1.5%, over a 30-day rolling window of 1h returns.
- **Threshold:** downside correlation > 0.75 counts as "highly correlated."
- **Rule:** if adding the candidate causes total notional exposure across assets correlated > 0.75 to exceed 35% of total equity, block the trade.
- **Backtest validation protocol before trusting these numbers in production:** run the 0.75/35% thresholds against the 3 worst market crashes of the last 2 years. If the 35% cap keeps portfolio drawdown under 20% in those events, the thresholds hold. If not, tighten the equity cap to 25% and re-test.
- Depends on 0.5 being fixed first — this gate reads `OpenPosition.size`, which is currently stale on partial fills.

---

## 3. Phase 2 — Validation Rigor

The ML regime detector and AI Judge are only as trustworthy as their validation methodology.

| Item | Why | Action |
|------|-----|--------|
| Walk-forward validation | Single in-sample backtest overfits to one regime (e.g. an entire bull run) | Re-fit and test on rolling windows; report performance per-window, not just aggregate |
| Purged cross-validation | Lookback features can leak across train/test boundaries | Purge overlapping windows between train and test sets for any ML component using historical features |
| Paper-trade parity check | Research code and production code can silently drift | Periodically replay live signal inputs through the backtest engine; diff outputs; alert on divergence |

---

## 4. Phase 3 — Risk Constraints (Non-Negotiable)

These are guardrails, not suggestions, and should be enforced in code, not just policy:

- **Max drawdown circuit breaker**: hard stop (not just alert) at a pre-defined equity drawdown %, tied to the now-fixed kill switch (0.2).
- **Kelly-fraction-capped sizing**: even if the edge estimate suggests larger size, cap at a fraction (e.g. quarter-Kelly) of the theoretical optimal to survive edge-estimation error.
- **Liquidation buffer monitoring**: 5-minute job checks mark price vs. liquidation price. **Dynamic buffer, not flat %** — a flat threshold is mathematically wrong across assets (5% on BTC is a black-swan wick; 5% on a mid-cap alt is routine noise). Since Phase 1 already introduces vol-scaled sizing, the buffer scales with the same volatility metric:
  - `Safe_Buffer_% = 3% (base floor) + 1.5 × 1h_ATR_%`
  - Example: BTC at 0.5% 1h ATR → buffer = 3.75%. A volatile alt at 4.0% 1h ATR → buffer = 9.0%.
  - Hard floor: 3%. Hard ceiling: 15% — if the formula demands a buffer above 15%, the asset is too volatile to trade and should be rejected by the risk gate outright, not traded with a wide buffer.
  - **HARD BLOCK on Phase 1, not parallel.** Phase 1 introduces ATR-based dynamic sizing and the funding-capture book; if sizing gets more aggressive during a black-swan move, the liq buffer monitor is the only mechanical safeguard against a wipeout. It must land in the same PR as the Phase 1 sizing changes (2.3), not as a follow-up.
- **Entry-side partial fill handling**: if a limit order partially fills, explicitly cancel-remainder or resize-position-in-DB — do not leave state ambiguous.

---

## 5. Sequencing Summary

```
Phase 0 (blocking)        Phase 1 (edge, w/ hard-blocking guardrail)      Phase 2 (validation)      Phase 3 (remaining guardrails)
─────────────────────     ────────────────────────────────────────────   ────────────────────      ──────────────────────────────
PnL recording        ┐    Funding-capture strategy (10-25%, cap 3)  ┐    Walk-forward val.    ┐    Max drawdown breaker
Kill switch wired     ├──▶ Cost-aware risk gate                      ├──▶ Purged CV            ├──▶ Kelly-capped sizing
TP scale-out (0.5)*   │    Vol-scaled sizing  ──────┐                │   Paper/live parity     │    Entry partial fills
Reconciliation (0.3)* │    Correlation gate (30d/0.75/35%)           │                          │
Close verification   ┘    Liq. buffer monitor ──────┘ SAME PR, HARD BLOCK                      ┘
```
*Implementation order within Phase 0: fix 0.5 before 0.3 — it corrupts state during normal operation, not just on restart.

Nothing in Phase 1 ships to live capital until Phase 0's verification tests pass. The liquidation buffer monitor is not a Phase 3 nice-to-have — it merges in the same PR as vol-scaled sizing (2.3), full stop.

---

## 6. Confirmed Answers (resolved)

1. **Reconciliation (0.3) / TP scale-out (0.5):** Both confirmed 🔴 BROKEN, not LIVE. 0.3 is a stub (checks existence only, not size/price/liq diff) — now specified as tiered auto-correct vs. halt-and-alert by drift severity, including the orphaned-position case. 0.5 silently desyncs local position size from the exchange on partial TP fills, which corrupts both the correlation gate and vol-scaled sizing downstream. Fix order: 0.5 first, then 0.3.
2. **Funding-capture allocation & leverage:** Dynamic allocation — 10% base, scaling to 25% when annualized funding exceeds ±15%, hard-capped at 3 concurrent positions. Leverage capped independently at **2x max** for this book, since it's a carry trade that must survive adverse price action rather than cut it short.
3. **Correlation gate (2.4):** Shifted from standard Pearson to **downside (tail) correlation** — computed only on hours where BTC drops > 1.5% — since crypto assets decouple on green days and converge on red days. 30-day window, 1h returns, 0.75 threshold, 35% equity cap, with a defined backtest protocol against the 3 worst crashes of the last 2 years to validate the threshold before trusting it live.
4. **Liquidation buffer:** Dynamic, not flat — `3% + 1.5 × 1h_ATR%`, floored at 3%, capped at 15% (above which the asset is rejected outright as untradeable). Hard-blocks Phase 1 sizing changes; must ship in the same PR.

## 7. Remaining Open Items

1. Backtest the correlation gate's 0.75/35% thresholds (and the fallback 25% cap) against the 3 worst crashes of the last 2 years using the downside-correlation methodology — current numbers are reasoned, not yet empirically validated.
2. Decide the concrete alerting mechanism for halt-and-alert reconciliation events (Telegram bridge already exists in `karsa-agents` — likely the right channel, but needs an explicit severity-tagged message format so critical drift alerts aren't lost among routine bot chatter).
3. If the funding-capture book later needs to scale beyond 2x leverage to be worthwhile, scope the delta-neutral spot-perp basis engine as a separate design doc — explicitly out of scope here.
4. Confirm the dynamic liq-buffer formula's constants (3% floor, 1.5× multiplier, 15% ceiling) against historical liquidation-wick data per asset tier (majors vs. mid-caps vs. low-cap alts) rather than treating them as universal.