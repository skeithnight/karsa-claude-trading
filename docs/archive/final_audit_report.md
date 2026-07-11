# Final Audit Report

> Verification of the implementation against the architectural design outlined in `docs/DATABASE_AUDIT_WALKTHROUGH.md`.

---

## Executive Summary

The code successfully implements the majority of the resource lifecycle fixes outlined in the walkthrough. The critical connection leaks stemming from background tasks, missing `close()` calls, and incorrect shutdown sequencing have been correctly mitigated.

However, the **four most fundamental architectural issues identified in the walkthrough remain unfixed**. The codebase explicitly acknowledges them as "Not Yet Applied", meaning this system is still exposed to cross-loop connection errors and race conditions under heavy load.

Additionally, one new Redis connection leak was discovered that the walkthrough missed completely.

---

## 1. Verified Fixes (Correctly Implemented)

I traced the execution path for the following fixes and confirmed they match the expected lifecycle design:

| Finding | Implementation Status | Notes |
|---------|-----------------------|-------|
| **Triple Monkey-Patch** | ✅ Fixed | Duplicate patches removed from `main.py` and `main_crypto.py`. Only `database.py` patches `asyncpg`. |
| **aiohttp Session Leaks** | ✅ Fixed | All 5 AODE research clients now have `close()` methods. `opportunity_scorer.py` and `discovery_engine.py` use `try/finally` and `asyncio.gather` to guarantee cleanup. |
| **Emergency Redis Leak** | ✅ Fixed | `emergency.py` exposes `close()`, which is correctly awaited in `main_crypto.py:shutdown()` and `main.py:shutdown()`. |
| **Pubsub Connection Leak** | ✅ Fixed | `sl_engine.py` correctly calls `await pubsub.close()` in the `finally` block, releasing the dedicated Redis connection. |
| **Scheduler Shutdown** | ✅ Fixed | Both orchestrators now use `self.scheduler.shutdown(wait=True)`, preventing mid-transaction DB abandonment. |
| **crypto_risk_manager Leak** | ✅ Fixed | `r.close()` is safely wrapped in a `try/finally` block. |
| **crypto_regime Blocking** | ✅ Fixed | Switched from sync `redis` to `redis.asyncio` with proper `try/finally` closure, protecting the event loop. |

---

## 2. Remaining Issues & Deviations from Walkthrough

The following issues were identified in the walkthrough as root causes of instability but have **not yet been implemented** in the code:

### 🔴 P0: Dual Event Loop (Uvicorn in Thread)
- **Walkthrough Expectation**: Run uvicorn on the main event loop (`asyncio.create_task(server.serve())`).
- **Current State**: `main_crypto.py:1475` and `main.py:1167` **still run uvicorn in a separate thread** (`threading.Thread(target=_run_uvicorn)`).
- **Impact**: The `/health` endpoint operates on a different event loop than the database pool. This is the root cause of the `asyncpg` "Future attached to different loop" errors.

### 🟠 P1: Pool Size Under-provisioned
- **Walkthrough Expectation**: `_POOL_SIZE = 20`, `_MAX_OVERFLOW = 10`.
- **Current State**: `database.py:83` is still `10` and `5`.
- **Impact**: 20+ concurrent scheduler jobs will hit the 15-connection limit and raise `TimeoutError` during bursts.

### 🟠 P1: Session Factory GC Pressure
- **Walkthrough Expectation**: Cache `async_sessionmaker` and reset it in `pool_reset()`.
- **Current State**: `database.py:311` creates a **new factory object** on every single `get_session()` call.
- **Impact**: Heavy garbage collection pressure (3000+ objects per scheduler cycle).

### 🟠 P1: Pool Reset Race Condition
- **Walkthrough Expectation**: Move `engine.dispose()` inside the `async with _get_lock():` block.
- **Current State**: `database.py:158` still calls `dispose()` **outside** the lock.
- **Impact**: A concurrent request can create a new engine that shares the same asyncpg pool. When the old engine finishes disposing, it destroys the new engine's connections, causing `InterfaceError: connection is closed`.

---

## 3. Newly Discovered Edge Cases (Not in Walkthrough)

During the final verification, I identified two issues that the walkthrough missed:

### 🔴 NEW Redis Connection Leak in Bot Handlers
**File**: `src/bot/crypto_handlers.py:1792`
```python
r = redis.from_url(settings.REDIS_URL, decode_responses=True)
events = await get_event_history(r, limit)
# ❌ r is NEVER CLOSED
```
Every time a user triggers the event history command, a new Redis connection pool is created and leaked. **This must be wrapped in `try/finally` or use the bot's shared context client.**

### 🟡 Severe Performance Penalty in API Endpoint
**File**: `src/api/crypto_control.py:40`
```python
r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
try:
    active = await r.get("karsa:asm:active") ...
finally:
    await r.close()
```
While this no longer leaks memory (thanks to `finally`), creating a brand new Redis connection pool (`from_url`) on every HTTP request to `/asm/status` adds massive TCP handshake latency and churn.
**Fix**: It should use `_get_app_state(request)[1]` to retrieve the globally shared Redis client instead of creating a new one.

---

## 4. Recommendations for Production Readiness

Before this system can be considered production-ready, the following actions must be taken:

1.  **Implement the 4 missing architectural fixes**: The dual event loop (Uvicorn thread) is the most critical stability threat and must be removed.
2.  **Fix the `crypto_handlers.py` line 1792 Redis leak**.
3.  **Refactor `/api/v1/crypto/asm/status`** to use the shared app state Redis client.
4.  **Track Background Tasks**: Tasks launched via `loop.create_task()` (like `universe_engine`, `ws_manager`, `sl_engine`) are still fire-and-forget. While they mostly run indefinitely, it is safer to store references to them to prevent premature garbage collection and allow for graceful cancellation.
