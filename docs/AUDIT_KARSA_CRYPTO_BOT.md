# Karsa Crypto Bot — Deep Audit (Post-Implementation)

**Date:** July 1, 2026
**Scope:** Full code audit of all 19 crypto-related source files after AUDIT_KARSA_3 implementation
**Baseline:** AUDIT_KARSA_3.md (June 30, 2026)

---

## Executive Summary

The Phase 1-3 fixes from AUDIT_KARSA_3 are **largely implemented** — deterministic TA, parallel scanning, signal dedup, liquidation proximity, correlation tiers, and the full UX redesign all landed. The codebase is significantly more mature.

However, **5 critical integration gaps** remain where components exist but aren't wired together, plus **3 safety regressions** introduced during implementation.

> [!CAUTION]
> **5 Critical Integration Gaps:**
> 1. `check_correlation_limits()` exists but is **never called** in `evaluate()` — correlation risk is decorative
> 2. `CryptoPosition` / `CryptoFundingPayment` tables exist in ORM but **nothing writes to them** — DB is dead
> 3. `FundingTracker` costs not included in P&L or kill switch — funding bleeds invisible
> 4. Crypto kill switch is **in-memory only** — container restart loses halt state
> 5. `is_global_halt()` never checked — `/kill` sets it but nobody reads it

**Maturity Score: 78/100** (up from 65/100 in AUDIT_KARSA_3)

---

## What Was Fixed (AUDIT_KARSA_3 Resolution Status)

| AUDIT_KARSA_3 Finding | Status | Notes |
|----------------------|--------|-------|
| Kill switch only realized PnL | ✅ FIXED | `check_kill_switch()` now sums `unrealized_pnl` from positions |
| No liquidation proximity | ✅ FIXED | `check_liquidation_proximity()` with warn/alert/force_close thresholds |
| No funding rate tracking | ⚠️ PARTIAL | `FundingTracker` exists but not integrated into PnL or risk |
| No deterministic TA | ✅ FIXED | `crypto_technicals.py` — RSI, BB, EMA, MACD, ATR all pure Python |
| Sequential pair scanning | ✅ FIXED | `_scan_crypto_parallel()` uses `asyncio.gather()` |
| No signal deduplication | ✅ FIXED | 4h dedup window via `_signal_cache` |
| BTC dominance stubbed | ✅ FIXED | CoinGecko free API, alt-season classification |
| No auditor pre-filter | ✅ FIXED | RSI + funding rate pre-filter before LLM call |
| No retry on Bybit errors | ✅ FIXED | `_retry_call()` with exponential backoff, fatal/retryable classification |
| No crypto DB tables | ✅ FIXED | 4 tables: `CryptoPosition`, `CryptoFundingPayment`, `CryptoRegimeHistory`, `CryptoPnLSnapshot` |
| Crypto universe duplicated | ✅ FIXED | `crypto_universe.py` single source of truth |
| No test coverage | ⚠️ PARTIAL | 2 test files exist, but gaps remain (see Section 12) |
| UX redesign | ✅ FIXED | `/guide`, `/regime`, `/funding`, `/trades` + inline keyboards |

---

## Audit Findings

### 1. CRITICAL — Correlation Limits Never Enforced

**File:** [crypto_risk_manager.py:221-405](src/risk/crypto_risk_manager.py#L221-L405)

`check_correlation_limits()` (L188) is fully implemented with tier-based position/exposure caps. **But `evaluate()` never calls it.** A trader can go LONG BTC + ETH + SOL + AVAX + LINK simultaneously — all highly correlated — with no limit enforcement.

```python
# evaluate() has gates 0-6 but NO correlation gate:
# Gate 0: Basic validation
# Gate 1: Daily loss limit
# Gate 2: Max concurrent positions
# Gate 3: Duplicate ticker
# Gate 4: Cooldown
# Gate 5: Max position cap
# Gate 6: Minimum order size
# MISSING: Gate 7: Correlation tier limits
```

**Impact:** Concentrated risk in correlated assets. If BTC drops 5%, all tier-1/2 positions drop together — portfolio blowup.

**Fix:** Add to `evaluate()` after Gate 3:
```python
corr = self.check_correlation_limits(ticker, open_positions, wallet_balance)
if not corr.get("allowed"):
    return self._reject(corr["reason"])
```

---

### 2. CRITICAL — Crypto DB Tables Are Empty (No Writers)

**Files:** [tables.py:210-293](src/models/tables.py#L210-L293), [orchestrator.py](src/agents/orchestrator.py), [main.py](src/main.py)

Four tables were added: `CryptoPosition`, `CryptoFundingPayment`, `CryptoRegimeHistory`, `CryptoPnLSnapshot`. **No code writes to any of them.**

- `_auto_execute_crypto()` saves to `Signal` table but never creates `CryptoPosition` rows
- `_job_monitor_crypto_positions()` updates `PaperPosition` but not `CryptoPosition`
- `FundingTracker.sync_funding_from_exchange()` fetches data but never persists to `CryptoFundingPayment`
- No code writes to `CryptoRegimeHistory` or `CryptoPnLSnapshot`

**Impact:** DB schema exists but is dead. If Bybit API goes down, all position data is lost. No historical funding analysis possible.

**Fix:** Wire writers into:
- `_auto_execute_crypto()` → insert `CryptoPosition` on execution
- `_job_monitor_crypto_positions()` → sync `CryptoPosition` + insert `CryptoFundingPayment`
- `_job_scan_crypto()` → insert `CryptoRegimeHistory` after regime check
- New `_job_daily_pnl_snapshot()` → insert `CryptoPnLSnapshot` at midnight UTC

---

### 3. CRITICAL — Funding Costs Invisible in P&L

**Files:** [crypto_risk_manager.py:79-118](src/risk/crypto_risk_manager.py#L79-L118), [funding_tracker.py](src/risk/funding_tracker.py)

`FundingTracker` calculates funding costs correctly. But:
- `check_kill_switch()` only sums `unrealized_pnl` — doesn't subtract cumulative funding
- `evaluate()` doesn't factor funding rate into risk assessment
- `/pnl` command doesn't show funding costs
- No scheduled job tracks funding payments

**Impact:** A position paying 0.05% funding every 8 hours (0.15%/day, 54.75%/year) appears profitable on uPnL while actually bleeding money. Kill switch won't trigger on funding-caused losses.

**Fix:**
1. `check_kill_switch()`: subtract cumulative funding from unrealized PnL
2. `evaluate()`: reject LONG if funding_rate > threshold (mirror auditor pre-filter)
3. Add `/funding` cost breakdown to `/pnl` command
4. Scheduled funding sync job (every 8h at 00:00/08:00/16:00 UTC)

---

### 4. CRITICAL — Crypto Kill Switch Is In-Memory Only

**File:** [crypto_risk_manager.py:58-77](src/risk/crypto_risk_manager.py#L58-L77)

```python
self._kill_switch_active = False  # Python instance variable
self._kill_switch_reason = ""
```

This lives in the `CryptoRiskManager` Python object. Container restart → reset to `False`. The IDX/US side uses Redis-backed `emergency.py` which survives restarts.

Meanwhile, `/kill` sets Redis keys via `activate_global_halt()`, but `check_kill_switch()` reads `self._kill_switch_active` (in-memory) and never checks Redis.

**Impact:** If the container restarts after a kill switch trigger (e.g., -3% daily loss), trading resumes automatically. The Redis-based global halt is set but the risk manager doesn't read it.

**Fix:** `check_kill_switch()` should check Redis first:
```python
from src.risk import emergency
if await emergency.is_active() or await emergency.is_global_halt():
    self._kill_switch_active = True
    return {"triggered": True, "reason": "Redis emergency stop active"}
```

---

### 5. CRITICAL — `is_global_halt()` Never Checked

**File:** [emergency.py:87-89](src/risk/emergency.py#L87-L89), [orchestrator.py](src/agents/orchestrator.py)

`/kill` calls `activate_global_halt()` which sets both `karsa:global_halt` and `karsa:emergency_stop` keys. But:
- `is_active()` only checks `karsa:emergency_stop` (the kill key)
- `is_global_halt()` exists but is **never called anywhere**
- `scan_all_markets()` calls `emergency.is_active()` which checks the kill key

This works *today* because `activate_global_halt` also sets the kill key. But `is_global_halt()` is dead code, and the architecture is confusing — two keys, one purpose.

**Impact:** Low today (both keys get set), but if someone calls `activate_global_halt` without the kill key fallback, crypto scans won't stop.

**Fix:** Either remove `global_halt` key entirely (use only `emergency_stop`), or have `is_active()` check both.

---

### 6. HIGH — SSL Verification Disabled for Bybit API

**File:** [bybit_client.py:63-75](src/data/bybit_client.py#L63-L75)

```python
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if hasattr(self._http_client, 'client'):
    self._http_client.client.verify = False
```

SSL verification is disabled globally with warnings suppressed. The comment says "Fix SSL issues in Docker" but this is a **man-in-the-middle vulnerability**. API keys and secrets travel over this connection.

**Impact:** If network traffic is intercepted (compromised DNS, rogue proxy, shared hosting), Bybit API credentials are exposed.

**Fix:** Fix the actual SSL issue (update CA certs in Docker image, use `REQUESTS_CA_BUNDLE` env var) instead of disabling verification:
```dockerfile
RUN apt-get update && apt-get install -y ca-certificates && update-ca-certificates
```

---

### 7. HIGH — Leverage Config Mismatch

**Files:** [config.py:43](src/config.py#L43), [crypto_risk_manager.py:382-389](src/risk/crypto_risk_manager.py#L382-L389)

- `CRYPTO_MAX_LEVERAGE = 10` in config
- `MAX_LEVERAGE_BY_TIER` in risk manager: tier1=10, tier2=5, tier3=3
- `evaluate()` hard-caps at 3x: `for candidate in [1, 2, 3]`

The config and tier-based system say 10x is allowed for BTC, but `evaluate()` never goes above 3x. The tier system is dead code in the leverage path.

**Impact:** Config is misleading. If someone changes `CRYPTO_MAX_LEVERAGE` expecting more leverage, nothing happens.

**Fix:** Use tier-based leverage in `evaluate()`:
```python
tier = _get_tier(ticker)
max_lev = min(MAX_LEVERAGE_BY_TIER.get(tier, 3), settings.CRYPTO_MAX_LEVERAGE)
for candidate in range(1, max_lev + 1):
    ...
```

---

### 8. HIGH — `_execute_pending_signals` Rescans Instead of Executing

**File:** [crypto_handlers.py:419-467](src/bot/crypto_handlers.py#L419-L467)

The `/activity` "Execute All Pending" button fetches pending signals from DB, then calls `orchestrator.scan_single("CRYPTO", s.ticker)` for each — which **re-runs the full agent analysis** instead of executing the existing signal.

```python
for s in pending:
    result = await orchestrator.scan_single("CRYPTO", s.ticker)  # re-scans!
```

**Impact:** Wastes LLM tokens, may generate different signals, doesn't execute the original signal. The pending signal in DB stays PENDING.

**Fix:** Execute the stored signal directly through `_auto_execute_crypto()` instead of re-scanning.

---

### 9. HIGH — Redis Connection Leaks in Handlers

**Files:** [crypto_handlers.py](src/bot/crypto_handlers.py) — `status_cmd`, `risk_cmd`, `kill_cmd`, `sellall_cmd`, `resume_cmd`, `regime_cmd`, `funding_cmd`

Multiple commands create standalone Redis connections with `redis.from_url()` and call `await r.close()`. But if an exception occurs between creation and close, the connection leaks:

```python
r = redis.from_url(settings.REDIS_URL, decode_responses=True)
# ... exception here = leak
await r.close()
```

7+ commands have this pattern. Each creates 1-3 Redis connections per invocation.

**Impact:** Connection exhaustion under error conditions. Each leaked connection holds a file descriptor.

**Fix:** Use the orchestrator's shared Redis client via `context.bot_data`, or use `async with` pattern.

---

### 10. MEDIUM — Inconsistent Daily Loss Limit Thresholds

**Files:** [main.py:412](src/main.py#L412), [config.py:42](src/config.py#L42), [crypto_risk_manager.py:51](src/risk/crypto_risk_manager.py#L51)

| Component | Threshold | Source |
|-----------|-----------|--------|
| `_job_kill_switch` (main.py) | -1.5% | Hardcoded |
| `CryptoRiskManager.daily_loss_limit` | -3.0% | `CRYPTO_DAILY_LOSS_LIMIT_PCT` config |
| `_job_kill_switch` | realized only | `ClosedPaperTrade` query |
| `check_kill_switch` | unrealized only | Position uPnL sum |

Three different loss calculations with two different thresholds. The main.py kill switch checks realized PnL at -1.5%, the crypto risk manager checks unrealized at -3.0%. Neither checks realized + unrealized + funding together.

**Impact:** Confusing behavior. Main kill switch may trigger at -1.5% realized while crypto risk manager allows -3% unrealized. Or vice versa.

**Fix:** Single unified loss calculation: `realized_today + unrealized + funding_costs`, single configurable threshold.

---

### 11. MEDIUM — `CryptoAuditMetrics` Uses `datetime.utcnow()`

**File:** [crypto_audit.py:25](src/advisory/crypto_audit.py#L25)

```python
cutoff = datetime.utcnow() - timedelta(days=days)  # naive datetime
```

Compared against `ClosedPaperTrade.exit_date` which is stored as `DateTime` (naive in DB). This works but is fragile — any timezone-aware datetime in the comparison chain will raise `TypeError`.

**Fix:** Use `datetime.now(timezone.utc)` consistently (already used in bybit_client.py).

---

### 12. MEDIUM — Audit `by_direction` Uses `side` Not `direction`

**File:** [crypto_audit.py:104-112](src/advisory/crypto_audit.py#L104-L112)

```python
d = t["side"]  # "Buy" or "Sell" from ClosedPaperTrade
```

But the rest of the system uses "LONG"/"SHORT". The `/audit_agent` display shows `Buy`/`Sell` instead of `LONG`/`SHORT`, inconsistent with every other command.

**Fix:** Map `"Buy" → "LONG"`, `"Sell" → "SHORT"` before grouping.

---

### 13. MEDIUM — `_get_bybit()` Fallback Creates Orphaned Connections

**File:** [crypto_handlers.py:14-23](src/bot/crypto_handlers.py#L14-L23)

```python
def _get_bybit(context):
    orch = context.bot_data.get("orchestrator")
    if orch:
        return orch.mcp._get_bybit()
    # Fallback: create new
    r = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return MCPClient(CacheManager(r))._get_bybit()
```

Fallback path creates a new Redis connection + MCPClient + BybitClient that are **never closed**. Also, the Redis `r` variable goes out of scope immediately but the `CacheManager` holds a reference.

**Impact:** Connection leak on every call when orchestrator is unavailable.

**Fix:** Remove fallback. If orchestrator isn't available, return error.

---

### 14. MEDIUM — `BybitClient._throttle()` Uses Blocking `time.sleep()`

**File:** [bybit_client.py:107-111](src/data/bybit_client.py#L107-L111)

```python
def _throttle(self):
    elapsed = time.time() - self._last_request
    if elapsed < self._min_interval:
        time.sleep(self._min_interval - elapsed)  # blocks event loop!
```

This is called from `async` methods via `_retry_call()` and `get_ticker()`. `time.sleep()` blocks the entire asyncio event loop for up to 100ms per call.

**Impact:** Under load (scanning 10 pairs in parallel), sequential throttle sleeps can block other coroutines. With 10 pairs × 3 API calls × 100ms = 3 seconds of blocked event loop.

**Fix:** Use `await asyncio.sleep()` in async context. Move throttle into `_retry_call()` which is already async.

---

### 15. MEDIUM — No Scheduled Funding/PnL Snapshot Jobs

**File:** [main.py:214-228](src/main.py#L214-L228)

AUDIT_KARSA_3 Phase 3 planned:
- Position Health Check — every 15 min ✅ (`_job_monitor_crypto_positions`)
- Funding Rate Monitor — 3x/day ❌ NOT IMPLEMENTED
- Daily PnL Snapshot — midnight UTC ❌ NOT IMPLEMENTED
- Position Sync — every 5 min ❌ NOT IMPLEMENTED

Only 1 of 4 planned crypto scheduled jobs exists.

---

### 16. MEDIUM — `_job_monitor_crypto_positions` Alert Threshold Too Low

**File:** [main.py:310-313](src/main.py#L310-L313)

```python
if pnl_pct <= -0.5:  # -0.5%
    alerts.append(f"⚠️ {symbol}: ...")
elif pnl_pct >= 2.0:
    alerts.append(f"🟢 {symbol}: ...")
```

-0.5% is normal intraday noise for crypto. At 3x leverage, a 0.5% move is routine. This will generate spam alerts.

**Fix:** Use configurable thresholds, default to -2% warn / -5% alert.

---

### 17. LOW — `MCPClient._get_bybit()` Attribute Access

**File:** [orchestrator.py:112](src/agents/orchestrator.py#L112)

```python
bybit = self.mcp._get_bybit()  # accessing private method
```

`_get_bybit()` is prefixed with `_` (private convention) but accessed from 4+ external modules. Should be a public method.

---

### 18. LOW — `wipe_memory()` Is a No-Op

**File:** [crypto_analyst.py:198-201](src/agents/crypto_analyst.py#L198-L201)

```python
def wipe_memory(self):
    """Clear conversation history — used by /sellall to prevent zombie trades."""
    get_logger("crypto_analyst").info("crypto_memory_wiped", agent=self.name)
```

Only logs. Doesn't clear any state. The `BaseAgent.run()` creates fresh messages per call anyway (no persistent memory), so this is correct in practice but misleading in name.

---

## Comparison: Previous Audit vs Current State

| Finding (AUDIT_KARSA_3) | Previous | Current | Delta |
|-------------------------|----------|---------|-------|
| Unrealized PnL in kill switch | ❌ | ✅ | +Fixed |
| Liquidation proximity | ❌ | ✅ | +Fixed |
| Funding rate tracking | ❌ | ⚠️ Partial | +Improved |
| Deterministic TA tools | ❌ | ✅ | +Fixed |
| Parallel scanning | ❌ | ✅ | +Fixed |
| Signal dedup | ❌ | ✅ | +Fixed |
| BTC dominance | ❌ | ✅ | +Fixed |
| Auditor pre-filter | ❌ | ✅ | +Fixed |
| BybitClient retry | ❌ | ✅ | +Fixed |
| Correlation limits | ❌ | ⚠️ Implemented, not wired | +Partial |
| Crypto DB tables | ❌ | ⚠️ Schema only, no writers | +Partial |
| Test coverage | ❌ | ⚠️ 2 files, gaps remain | +Partial |
| UX redesign | ❌ | ✅ | +Fixed |
| Inline keyboards | ❌ | ✅ | +Fixed |
| HITL approval | ❌ | ❌ | No change |

---

## Priority Fix List

### P0 — Must Fix Before Live Trading

1. **Wire correlation limits into `evaluate()`** — 1 line, prevents concentrated risk
2. **Write to `CryptoPosition` table** — prevents data loss on API outage
3. **Unify kill switch to use Redis** — prevents auto-resume after restart
4. **Include funding in P&L calculation** — prevents invisible cost bleed
5. **Fix SSL verification** — prevents credential exposure

### P1 — Should Fix This Week

6. **Fix leverage config mismatch** — align config/tier/evaluate
7. **Fix `_execute_pending_signals`** — stop re-scanning, execute stored signals
8. **Fix Redis connection leaks** — use shared client or async with
9. **Unify daily loss thresholds** — single calculation, single threshold
10. **Fix blocking `time.sleep` in throttle** — use `asyncio.sleep`

### P2 — Fix This Sprint

11. **Add funding sync scheduled job** — every 8h
12. **Add daily PnL snapshot job** — midnight UTC
13. **Add position sync job** — every 5 min
14. **Fix audit `by_direction` mapping** — Buy→LONG, Sell→SHORT
15. **Fix `_get_bybit()` fallback** — remove orphaned connection creation
16. **Adjust monitor alert thresholds** — -0.5% too noisy
17. **Make `_get_bybit()` public** — rename to `get_bybit()`

---

## Test Coverage Gap Analysis

| Component | Test File | Coverage |
|-----------|-----------|----------|
| `crypto_technicals.py` | ✅ `test_crypto_technicals.py` | RSI, BB, EMA, ATR, full_analysis |
| `crypto_risk_manager.py` | ✅ `test_crypto_risk_manager.py` | Kill switch, liq proximity, correlation, basic evaluate |
| `bybit_client.py` | ❌ No tests | Retry logic, error handling, caching |
| `crypto_regime.py` | ❌ No tests | Hurst, ADX, regime classification |
| `crypto_auditor.py` | ❌ No tests | Pre-filter rules, audit prompt |
| `crypto_audit.py` | ❌ No tests | Metrics aggregation |
| `funding_tracker.py` | ❌ No tests | Cost calculation, alert thresholds |
| `sor.py` | ❌ No tests | Order routing, re-price loop, flatten |
| `crypto_handlers.py` | ❌ No tests | All 15 commands, auth, callbacks |
| `crypto_universe.py` | ❌ No tests | Pair config, leverage lookup |

**Missing test scenarios in existing tests:**
- `evaluate()` with regime (CHOP gate, size_multiplier)
- `evaluate()` with daily PnL near limit
- `check_kill_switch()` with multiple positions
- Correlation limits with mixed tiers

---

## Architecture Observations

### What's Good

1. **Deterministic TA separation** — clean `crypto_technicals.py` with self-test. LLM calls tools, never does math.
2. **Tiered risk architecture** — correlation tiers + per-tier leverage caps is well-designed (just not wired).
3. **Parallel scanning** — `_scan_crypto_parallel()` + `asyncio.gather()` matches IDX/US pattern.
4. **Pre-filter before LLM** — saves cost by rejecting obviously bad signals before auditor call.
5. **UX redesign** — `/guide`, `/regime`, `/funding`, `/trades` with inline keyboards. Professional.
6. **DB schema design** — `CryptoPosition`, `CryptoFundingPayment`, `CryptoRegimeHistory`, `CryptoPnLSnapshot` tables are well-structured.

### What's Concerning

1. **Components exist but aren't connected** — correlation limits, DB tables, funding tracker, global halt check. The code *looks* complete but the integration wiring is missing.
2. **Two kill switch systems** — in-memory (`CryptoRiskManager._kill_switch_active`) and Redis (`emergency.py`). They don't talk to each other.
3. **Auto-execute without HITL** — IDX/US has APPROVE/REJECT buttons. Crypto auto-executes. One bad LLM call = real money at risk (even on testnet, this trains bad habits).
4. **`httpx` for Telegram notifications** — used in `orchestrator.py` and `main.py` directly instead of through the bot application. Fragile, no retry, no rate limiting.

---

## Open Questions

> [!IMPORTANT]
> **Q1:** Should `CryptoRiskManager.evaluate()` call `check_correlation_limits()`, or should it be a separate pre-execution gate in the orchestrator?

> [!IMPORTANT]
> **Q2:** Should the in-memory kill switch in `CryptoRiskManager` be removed entirely in favor of the Redis-backed `emergency.py`? This would simplify to one kill switch system.

> [!IMPORTANT]
> **Q3:** The `_job_kill_switch` in main.py checks realized PnL at -1.5%, while `CryptoRiskManager.check_kill_switch()` checks unrealized at -3.0%. Should these be unified into a single check?

> [!IMPORTANT]
> **Q4:** Should crypto signals go through HITL (like IDX/US), or is the Analyst→Auditor→Risk pipeline sufficient for auto-execution on testnet?
