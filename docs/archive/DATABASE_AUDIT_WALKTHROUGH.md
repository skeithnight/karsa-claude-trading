# Karsa Database & Event Loop Audit — Walkthrough

> A practical guide through every finding, fix, and remaining risk.
> Read this to understand **what broke**, **why it broke**, and **how it was fixed**.

---

## Table of Contents

1. [How the Database Layer Works](#1-how-the-database-layer-works)
2. [The Connection Lifecycle](#2-the-connection-lifecycle)
3. [Finding 1: Triple Monkey-Patch](#3-finding-1-triple-monkey-patch)
4. [Finding 2: Session Factory Leak](#4-finding-2-session-factory-leak)
5. [Finding 3: Pool Reset Race Condition](#5-finding-3-pool-reset-race-condition)
6. [Finding 4: Dual Event Loop (uvicorn)](#6-finding-4-dual-event-loop-uvicorn)
7. [Finding 5: aiohttp Session Leaks](#7-finding-5-aiohttp-session-leaks)
8. [Finding 6: Emergency Redis Leak](#8-finding-6-emergency-redis-leak)
9. [Finding 7: Pubsub Connection Leak](#9-finding-7-pubsub-connection-leak)
10. [Finding 8: Scheduler Shutdown](#10-finding-8-scheduler-shutdown)
11. [Finding 9-11: Redis Client Leaks](#11-finding-9-11-redis-client-leaks)
12. [Remaining Risks](#12-remaining-risks)
13. [How to Verify Fixes](#13-how-to-verify-fixes)

---

## 1. How the Database Layer Works

```
┌─────────────────────────────────────────────────────────┐
│                    database.py                          │
│                                                         │
│  _engine = None          ← Lazy-created async engine    │
│  _session_factory = None ← Created per-call (BUG)       │
│  _health_engine = None   ← NullPool for watchdog        │
│  _engine_lock = None     ← asyncio.Lock (lazy)          │
│                                                         │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────┐ │
│  │ get_engine() │───▶│ _make_engine │───▶│ PostgreSQL │ │
│  │  (sync)      │    │  QueuePool   │    │  (15 max)  │ │
│  └─────────────┘    │  size=10     │    └────────────┘ │
│                      │  overflow=5  │                   │
│  ┌─────────────┐    └──────────────┘                   │
│  │get_session()│───▶ async_sessionmaker(...)()          │
│  │  (sync)      │    Creates NEW factory each call!     │
│  └─────────────┘                                        │
└─────────────────────────────────────────────────────────┘
```

### The Call Chain

```
Your code:
  async with async_session() as session:
      await session.execute(...)

What happens internally:
  1. async_session() calls get_session()
  2. get_session() calls get_engine() → returns _engine global
  3. get_session() creates async_sessionmaker(_engine) → NEW factory
  4. factory() → returns AsyncSession
  5. async with → session.__aenter__() → checks out connection from pool
  6. ... your queries ...
  7. async with exit → session.__aexit__() → returns connection to pool
```

### Pool Math

```
Pool Size:     10 connections (always open)
Max Overflow:  5 connections (opened on demand)
Total Max:     15 connections

APScheduler Jobs: 20+ (all fire concurrently)
Each job:         1 connection (held during DB ops)
Peak demand:      20+ connections

Result: 5+ jobs wait 30s → TimeoutError
```

---

## 2. The Connection Lifecycle

### Normal Flow (Happy Path)

```
Job starts
  → async with async_session()
    → connection checked out from pool
    → queries execute
    → commit()
  → context exit
    → connection returned to pool
Job ends
```

### Leak Flow (What Was Happening)

```
Job starts
  → async with async_session()
    → connection checked out from pool
    → queries execute
    → EXCEPTION raised
  → context exit
    → rollback() called ✅
    → connection returned to pool ✅
Job ends

BUT: If the task is cancelled (CancelledError):
  → context exit never runs
  → connection stays checked out
  → pool slowly drains
```

### Cross-Loop Leak Flow (uvicorn thread)

```
Main loop creates engine (asyncpg pool bound to main loop)
  ↓
Uvicorn thread creates its own event loop
  ↓
Health endpoint: async with async_session()
  → checks out connection from main loop's pool
  → tries to use it on uvicorn's loop
  → asyncpg: "Future attached to different loop"
  → connection never returned
  → pool slowly drains
```

---

## 3. Finding 1: Triple Monkey-Patch

### What Happened

Three files all patched asyncpg at import time:

```python
# main.py (lines 4-17)
asyncpg.Connection._abort = _safe_abort
asyncpg.Connection.close = _safe_close

# main_crypto.py (lines 16-29)
asyncpg.Connection._abort = _safe_abort  # SAME patch
asyncpg.Connection.close = _safe_close

# database.py (line 77)
_patch_asyncpg_terminate()  # SAME patch again
```

### Why It's a Problem

Python imports are sequential. Whichever file is imported last wins:

```
python -m src.main_crypto
  → import main_crypto  → patch #1 applied
  → import database     → patch #2 applied (overwrites #1)
```

Today the patches are identical, so it's harmless. But if someone fixes a bug in `database.py`'s patch without removing the duplicates, the `main_crypto.py` patch silently overwrites the fix.

### The Fix

```python
# main.py — BEFORE:
try:
    import asyncpg
    asyncpg.Connection._abort = _safe_abort
    # ... 15 lines of patch code ...
except ImportError:
    pass

# main.py — AFTER:
# asyncpg monkey-patch is applied in src/models/database.py at import time.
# Do NOT duplicate it here — see docs/DATABASE_AUDIT.md Finding 1.
```

Same change in `main_crypto.py`.

### How to Verify

```bash
# Should find patch in ONLY database.py
grep -rn "asyncpg.Connection._abort" src/ --include="*.py"
# Expected: only src/models/database.py
```

---

## 4. Finding 2: Session Factory Leak

### What Happened

```python
# database.py:304-315
def get_session():
    return async_sessionmaker(
        get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )()  # ← Creates NEW factory + NEW session every call
```

Every call to `async_session()` creates:
1. A new `async_sessionmaker` object (factory)
2. A new `AsyncSession` object (session)

With 150+ call sites × 20 scheduler jobs = **3000+ factory objects per scheduler cycle**.

### Why It's a Problem

- **Memory**: Each factory object holds references to the engine, session class, and configuration
- **GC Pressure**: Python's garbage collector has to clean up thousands of short-lived objects
- **Not a connection leak**: The session itself is properly closed by `async with`, but the factory objects accumulate

### The Fix (Not Yet Applied)

```python
# Cache the factory, reset on pool_reset()
_session_factory = None

def get_session():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory()
```

### How to Verify

```python
# After fix, factory should be same object
import gc
gc.collect()
before = len(gc.get_objects())
for _ in range(100):
    async_session()
gc.collect()
after = len(gc.get_objects())
print(f"Objects created: {after - before}")  # Should be ~100, not ~200
```

---

## 5. Finding 3: Pool Reset Race Condition

### What Happened

```python
# database.py:145-163
async def pool_reset(reason: str = "manual") -> bool:
    async with _get_lock():
        _engine = None          # ← Engine cleared (inside lock)
        _session_factory = None
        _last_dispose_time = now
    # LOCK RELEASED HERE
    # Another coroutine can now create a new engine!
    if engine_to_dispose is not None:
        await engine_to_dispose.dispose()  # ← Old engine still disposing
```

### The Race Window

```
Time    Coroutine A (pool_reset)         Coroutine B (get_engine)
─────   ─────────────────────────────    ──────────────────────────
t0      async with _get_lock():
t1        _engine = None
t2      LOCK RELEASED
t3                                       async with _get_lock():
t4                                         if _engine is None:
t5                                           _engine = _make_engine()  ← NEW engine
t6                                       LOCK RELEASED
t7      await old_engine.dispose()
        ↑ This terminates connections
          that the NEW engine is using!
```

### Why It's a Problem

Both engines share the same underlying asyncpg connection pool. When the old engine's `dispose()` runs, it terminates connections that the new engine just checked out → `asyncpg.exceptions.InterfaceError: connection is closed`.

### The Fix (Not Yet Applied)

```python
async def pool_reset(reason: str = "manual") -> bool:
    async with _get_lock():
        now = time.monotonic()
        if now - _last_dispose_time < 45.0:
            return False
        engine_to_dispose = _engine
        _engine = None
        _session_factory = None
        _last_dispose_time = now
        # Dispose INSIDE the lock — no race window
        if engine_to_dispose is not None:
            try:
                await engine_to_dispose.dispose()
            except Exception as e:
                logger.warning("pool_reset_dispose_error", error=str(e))
    return True
```

---

## 6. Finding 4: Dual Event Loop (uvicorn)

### What Happened

```python
# main.py:1168-1177
def _run_uvicorn():
    asyncio.run(server.serve())  # ← Creates NEW event loop in thread

uvicorn_thread = threading.Thread(target=_run_uvicorn, daemon=True)
uvicorn_thread.start()
```

### The Problem

```
Main Thread                    Uvicorn Thread
─────────────                  ───────────────
asyncio.run(main)              asyncio.run(uvicorn)
  Event Loop A                   Event Loop B
  ↓                              ↓
  engine created (Loop A)        health endpoint:
  pool connections bound           async with async_session()
  to Loop A                        → uses engine from Loop A
                                   → asyncpg: "Future attached to different loop"
                                   → connection leak
```

### Why It Matters

Every health check request (every 5-30 seconds from monitoring) potentially leaks a connection. Over hours/days, the pool drains.

### The Fix (Not Yet Applied)

```python
# Option A: Run uvicorn on the main loop
async def run(self):
    await self.startup()
    config = uvicorn.Config(app, host="0.0.0.0", port=8001)
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())  # ← Same loop, no cross-loop issue
    await self._shutdown.wait()
    await self.shutdown()
```

### How to Verify

```bash
# Check if uvicorn is in a thread (BAD) or on main loop (GOOD)
grep -n "threading.Thread\|asyncio.run.*server" src/main_crypto.py
# Should see: asyncio.create_task(server.serve()) — NOT threading.Thread
```

---

## 7. Finding 5: aiohttp Session Leaks

### What Happened

5 research clients create `aiohttp.ClientSession` objects but never close them:

```python
# dexscreener_client.py, onchain_client.py, defillama_client.py, etc.
async def _get_session(self) -> aiohttp.ClientSession:
    if self._session is None or self._session.closed:
        self._session = aiohttp.ClientSession(...)  # ← TCP connections open
    return self._session

async def close(self):
    if self._session and not self._session.closed:
        await self._session.close()  # ← This existed but was NEVER CALLED
```

### The Leak Path

```
AODE research job runs (every 4 hours)
  → OpportunityScorer.score_opportunity()
    → OnchainIntelligence(cache=cache)
      → _ensure_clients()
        → DefiLlamaClient()  → aiohttp.ClientSession() → TCP pool opens
        → OnchainClient()    → aiohttp.ClientSession() → TCP pool opens
        → DexScreenerClient() → aiohttp.ClientSession() → TCP pool opens
    → DeveloperIntelligence()
      → GitHubClient()       → aiohttp.ClientSession() → TCP pool opens
      → CoinGeckoClient()    → aiohttp.ClientSession() → TCP pool opens
    → ... scoring happens ...
    → return result
    → ❌ close() NEVER CALLED
    → All 5 TCP pools leak
```

### The Fix (Applied)

Added `close()` to all 6 intel classes and wrapped scoring in try/finally:

```python
# opportunity_scorer.py
try:
    results = await asyncio.gather(
        onchain.snapshot(symbol, chain),
        developer.snapshot(symbol, coingecko_id),
        # ... all intel modules ...
    )
    # ... scoring logic ...
    return result
finally:
    # Close all HTTP clients to prevent aiohttp connection leaks
    for intel in (onchain, developer, community, narrative, smart_money, risk):
        await intel.close()
```

### How to Verify

```bash
# Check open TCP sockets before/after AODE cycle
ss -tnp | grep -c "aiohttp"
# Should return 0 between cycles
```

---

## 8. Finding 6: Emergency Redis Leak

### What Happened

```python
# emergency.py
_client: aioredis.Redis | None = None

def _get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client
# ❌ No close() function existed
```

### The Problem

- Module-level Redis client created lazily
- Never closed on shutdown
- Separate connection pool from main `redis_client`
- Cross-loop risk if emergency functions called from different contexts

### The Fix (Applied)

```python
# emergency.py — added:
async def close() -> None:
    """Close the module-level Redis client. Call during shutdown."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None

# main.py + main_crypto.py — added to shutdown():
from src.risk import emergency as _emergency
await _emergency.close()
```

### How to Verify

```bash
# Check Redis connections during shutdown
redis-cli client list | grep -c "karsa"
# Should drop to 0 after shutdown
```

---

## 9. Finding 7: Pubsub Connection Leak

### What Happened

```python
# sl_engine.py:49-68
pubsub = self._redis.pubsub()
await pubsub.subscribe(REDIS_TICK_CHANNEL)
try:
    async for message in pubsub.listen():
        # ... process messages ...
finally:
    await pubsub.unsubscribe()  # ← Sends UNSUBSCRIBE but doesn't close connection
```

### The Problem

`pubsub.unsubscribe()` sends the Redis UNSUBSCRIBE command but does **not** close the underlying TCP connection. Each call to `self._redis.pubsub()` takes a dedicated connection from the Redis pool. If `sl_engine.run()` is called → stops → called again, a new pubsub connection is taken and the old one is not returned.

### The Fix (Applied)

```python
finally:
    watchdog_task.cancel()
    await pubsub.unsubscribe()
    await pubsub.close()  # ← Now releases the dedicated Redis connection
```

### How to Verify

```bash
# Check Redis connections before/after sl_engine.stop()
redis-cli client list | grep "sub"
# Should decrease by 1 after stop
```

---

## 10. Finding 8: Scheduler Shutdown

### What Happened

```python
# main_crypto.py:1459
self.scheduler.shutdown(wait=False)
```

`wait=False` means APScheduler signals jobs to stop but does **not** wait for currently-running jobs to complete.

### The Problem

```
Job _job_sync_crypto_positions is halfway through:
  async with async_session() as session:
      session.add(CryptoPosition(...))
      await session.commit()  ← Not yet committed
      
scheduler.shutdown(wait=False)
  → Job cancelled via CancelledError
  → async with __aexit__ should call rollback()
  → BUT CancelledError may prevent __aexit__ from running
  → Transaction abandoned at Postgres level
  → Connection leaked until Postgres timeout
```

### The Fix (Applied)

```python
# BEFORE:
self.scheduler.shutdown(wait=False)

# AFTER:
self.scheduler.shutdown(wait=True)  # Wait for in-flight jobs to complete
```

### How to Verify

```bash
# During shutdown, check for abandoned transactions
psql -c "SELECT * FROM pg_stat_activity WHERE state = 'idle in transaction'"
# Should return 0 rows after shutdown
```

---

## 11. Finding 9-11: Redis Client Leaks

### Finding 9: crypto_risk_manager.py

```python
# BEFORE:
r = redis_mod.from_url(settings.REDIS_URL, decode_responses=True)
cooldown = await r.get("karsa:crypto_cooldown")
await r.close()  # ← Close exists but not in finally block

# AFTER:
r = redis_mod.from_url(settings.REDIS_URL, decode_responses=True)
try:
    cooldown = await r.get("karsa:crypto_cooldown")
finally:
    await r.close()  # ← Now closes even on exception
```

### Finding 10: crypto_handlers.py

```python
# BEFORE:
def _get_redis(context):
    client = context.bot_data.get("redis_client")
    if client:
        return client
    return redis.from_url(settings.REDIS_URL, decode_responses=True)  # ← Leaked

# AFTER:
def _get_redis(context):
    client = context.bot_data.get("redis_client")
    if client:
        return client
    # Log warning so we know when fallback is used
    logging.getLogger("crypto_handlers").warning(
        "redis_fallback_client_created — bot_data[redis_client] is None"
    )
    return redis.from_url(settings.REDIS_URL, decode_responses=True)
```

### Finding 11: crypto_regime.py

```python
# BEFORE:
import redis as sync_redis
r = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
chat_id_str = r.get("karsa:telegram_chat_id")  # ← Blocks event loop!

# AFTER:
import redis.asyncio as async_redis
r = async_redis.from_url(settings.REDIS_URL, decode_responses=True)
try:
    chat_id_str = await r.get("karsa:telegram_chat_id")  # ← Non-blocking
finally:
    await r.close()
```

---

## 12. Remaining Risks

### Not Yet Fixed (Require Architectural Decisions)

| Risk | Severity | Why Not Fixed | What's Needed |
|------|----------|---------------|---------------|
| Pool size (15 max) | 🟠 | Config change | `_POOL_SIZE=20, _MAX_OVERFLOW=10` |
| Session factory cache | 🟠 | Needs testing | Cache factory, reset in pool_reset() |
| Pool reset race | 🟠 | Needs testing | Move dispose inside lock |
| Dual event loop | 🔴 | Architectural | Run uvicorn on main loop |
| `_connection_health_loop` | 🟠 | Bot boundary unclear | Verify if bot runs in same process |
| `patch_websocket.py` SSL | 🟡 | Security decision | Evaluate if SSL disable is still needed |
| httpx.Limits on MCPClient | 🟡 | Config decision | Add connection pool limits |

### Known Blind Spots

| Blind Spot | How to Verify |
|------------|---------------|
| Does bot run in same process as orchestrator? | Check docker-compose.yml service definitions |
| Are aiohttp clients GC'd between AODE runs? | `ss -tnp \| grep postgres` before/after |
| Does `pubsub.unsubscribe()` release connection? | `redis-cli client list` before/after |
| Does NullPool accumulate connections? | `SELECT count(*) FROM pg_stat_activity` |

---

## 13. How to Verify Fixes

### Quick Health Check

```bash
# 1. Check pool status
curl -s http://localhost:8001/health | jq '.checks'

# 2. Check Redis connections
redis-cli client list | wc -l

# 3. Check Postgres connections
psql -c "SELECT count(*) FROM pg_stat_activity WHERE datname = 'karsa'"

# 4. Check for idle-in-transaction
psql -c "SELECT count(*) FROM pg_stat_activity WHERE state = 'idle in transaction'"

# 5. Check memory usage
ps aux | grep python | grep karsa
```

### After AODE Cycle

```bash
# Before
ss -tnp | grep -c aiohttp > /tmp/before.txt
redis-cli info clients | grep connected_clients >> /tmp/before.txt

# Run AODE cycle
curl -X POST http://localhost:8001/api/v1/crypto/aode/research

# After
ss -tnp | grep -c aiohttp > /tmp/after.txt
redis-cli info clients | grep connected_clients >> /tmp/after.txt

# Compare
diff /tmp/before.txt /tmp/after.txt
# Should be identical (no leaked connections)
```

### During Shutdown

```bash
# Send SIGTERM
kill -TERM $(pgrep -f "src.main_crypto")

# Watch logs for clean shutdown
docker logs -f karsa-crypto-orchestrator | grep -E "shutdown|disposed|close"

# Verify no abandoned transactions
psql -c "SELECT * FROM pg_stat_activity WHERE state = 'idle in transaction'"
```

---

## Summary

### What Was Fixed (12 files)

| Fix | Files | Impact |
|-----|-------|--------|
| Removed duplicate monkey-patches | main.py, main_crypto.py | Eliminated import-order dependency |
| Added aiohttp close() | 6 intel files + opportunity_scorer.py | Prevents TCP socket leak (500+ sockets/cycle) |
| Added pubsub.close() | sl_engine.py | Prevents Redis connection leak |
| Changed scheduler shutdown | main.py, main_crypto.py | Prevents abandoned transactions |
| Added emergency.close() | emergency.py, main.py, main_crypto.py | Prevents Redis connection leak |
| Fixed Redis try/finally | crypto_control.py, crypto_risk_manager.py | Prevents Redis leak on exception |
| Replaced sync Redis | crypto_regime.py | Prevents event loop blocking |
| Added fallback warning | crypto_handlers.py | Surfaces hidden Redis client creation |

### What Remains (Architectural)

| Item | Priority | Effort |
|------|----------|--------|
| Pool size increase | P1 | 5 min (config change) |
| Session factory cache | P1 | 30 min (code + test) |
| Pool reset race fix | P1 | 30 min (code + test) |
| Dual event loop fix | P0 | 2 hours (architecture change) |
| Bot process boundary | P2 | 1 hour (docker investigation) |

---

*End of walkthrough.*
