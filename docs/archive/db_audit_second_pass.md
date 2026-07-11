# Karsa — Second-Pass Audit Report

> Full execution-flow trace from startup to shutdown, covering every database object,
> background worker, async task, connection pool, and external client.
> Challenges all first-audit findings and looks for what was missed.

---

## Executive Summary

First audit covered the SQLAlchemy/asyncpg layer well but **missed the entire HTTP client tier, Redis connection proliferation, pubsub lifecycle, and cross-loop DB access in the Telegram bot process**. It also underestimated the monkey-patch problem (patch still applied twice at module level), misread the `pool_reset()` race as intentional, and underrated the `_connection_health_loop` as another cross-loop DB caller.

**12 new findings** identified. **3 first-audit assumptions** found to be incorrect.

---

## Execution Flow Trace: Startup → Shutdown

```
docker-compose up
  → python -m src.main_crypto
    → top-of-file: asyncpg monkey-patch #1 applied         ← FINDING 1
    → asyncio.run(karsa.run())
      → startup()
        → redis.from_url() → main redis client
        → init_db()
          → _get_or_create_engine() → _engine created
          → Base.metadata.create_all()
          → asyncio.create_task(_pool_recycle_loop())      ← task stored ✅
        → mcp = MCPClient()
          → httpx.AsyncClient() created → stored in self._http_client ← FINDING 2
        → asm._run_loop(chat_id) create_task              ← unstored ⚠️
        → watchdog.start() create_task                    ← unstored ⚠️
        → scheduler.start()
          → 20 jobs registered (all max_instances=1, coalesce=True) ✅
        → _register_health_routes()
        → database.py imported → monkey-patch #2 applied  ← FINDING 1 (dupe)
      → run()
        → asyncio.create_task(server.serve()) ← WRONG — actually spawns thread ← P0 open
        → loop.create_task(universe_engine.listen_profile_changes())  ← unstored ⚠️
        → loop.create_task(ws_manager.run())  ← unstored ⚠️
        → loop.create_task(sl_engine.run())   ← unstored ⚠️
          → sl_engine creates pubsub subscription       ← FINDING 3
        → shutdown.wait()
      → shutdown()
        → event_bus.stop()
        → sl_engine.stop()  → pubsub.unsubscribe() ✅
        → ws_manager.stop() ✅
        → scheduler.shutdown(wait=False)                  ← FINDING 4
        → mcp.close()       → httpx + bybit closed ✅
        → redis_client.close() ✅
        → close_db()        → _pool_cleaner_task cancelled, engine disposed ✅
        — MISSING: aiohttp sessions in 5 data clients     ← FINDING 5
        — MISSING: emergency._client Redis connection      ← FINDING 6
        — MISSING: unstored background tasks not cancelled ← FINDING 7
```

---

## New Findings Not In First Audit

---

### Finding 1 — 🔴 Triple asyncpg monkey-patch still exists (first audit marked as mitigated)

**First audit said**: "Triple monkey-patch — already mitigated."

**Second-pass truth**: The patch is **still applied twice**:

```python
# main_crypto.py:16-28 (at TOP of file, before any imports)
try:
    import asyncpg
    asyncpg.Connection._abort = _safe_abort
    asyncpg.Connection.close = _safe_close
except ImportError:
    pass

# Then later, when database.py is imported:
# database.py:77 — also applies the SAME patch
_patch_asyncpg_terminate()   # calls asyncpg.Connection._abort = _safe_abort again
```

**Why it's a problem**: Whichever patch runs last wins. Both patches are identical content, so right now it's harmless. But if anyone changes one without the other (e.g., fixes a bug in `database.py`'s patch), the `main_crypto.py` patch silently overwrites it at startup. The first audit called this "mitigated" — it isn't, it's just coincidentally not broken yet.

**Fix**: Remove the `main_crypto.py` and `main.py` top-of-file patches. `database.py` is imported before any engine is created, so its import-time patch runs first.

---

### Finding 2 — 🟠 `MCPClient` holds a persistent `httpx.AsyncClient` with no timeout on the connection pool

**File**: [`src/data/mcp_client.py:69`](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/data/mcp_client.py#L69)

```python
self._http_client = httpx.AsyncClient(timeout=10.0)
```

**Lifecycle**: Created in `__init__`, closed in `close()` (called from `shutdown()` via `mcp.close()`). The close path is correct.

**Issue**: `httpx.AsyncClient` with default settings opens **persistent HTTP/1.1 keep-alive connections**. The default connection pool size is 20. Every LLM API call adds a TCP connection that stays open until idle timeout or explicit close. If `mcp.close()` is skipped (e.g., exception in shutdown before reaching it), all these TCP connections leak.

**More critically**: `httpx.AsyncClient` also does **NOT** apply a connection timeout by default. `timeout=10.0` only applies to individual requests, not connection establishment. On a cold LLM endpoint this can hang indefinitely.

**Missing**: No `limits=httpx.Limits(...)` config, so the pool can grow unbounded if the LLM is slow and many LLM calls queue up.

---

### Finding 3 — 🟠 Redis pubsub connection in `sl_engine` is never explicitly closed on timeout/exception exit

**File**: [`src/execution/sl_engine.py:49-67`](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/execution/sl_engine.py#L49-L67)

```python
pubsub = self._redis.pubsub()
await pubsub.subscribe(REDIS_TICK_CHANNEL)
try:
    async for message in pubsub.listen():
        ...
except Exception as e:
    logger.error(...)
finally:
    watchdog_task.cancel()
    await pubsub.unsubscribe()   # ← runs in finally ✅
```

**Good**: The `finally` block calls `pubsub.unsubscribe()`. This looks correct.

**Issue**: `pubsub.unsubscribe()` sends `UNSUBSCRIBE` to Redis but does **not** close the underlying TCP connection. The pubsub object keeps its connection open. Each call to `self._redis.pubsub()` takes a dedicated connection from the Redis connection pool. If `sl_engine.run()` is called → stops → called again (e.g., on reconnect), a new pubsub connection is taken and the old one is not returned.

**Fix**: Add `await pubsub.close()` in the `finally` block after `unsubscribe()`.

---

### Finding 4 — 🟠 `scheduler.shutdown(wait=False)` abandons in-flight DB jobs

**File**: [`src/main_crypto.py:1459`](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/main_crypto.py#L1459)

```python
self.scheduler.shutdown(wait=False)
```

`wait=False` means APScheduler signals jobs to stop but does **not** wait for currently-running jobs to complete. If at shutdown time, `_job_sync_crypto_positions` is halfway through a DB transaction (say, writing a position update), the transaction is abandoned mid-write.

**What happens**:
1. Job holds a DB session with an open transaction
2. `scheduler.shutdown(wait=False)` — job is cancelled
3. The `async with async_session()` context manager's `__aexit__` should call `rollback()` — BUT this requires the coroutine to run to completion, which it won't if the task is cancelled via `CancelledError`
4. `close_db()` runs next → disposes engine → asyncpg force-closes all connections → transaction abandoned at Postgres level

**Mitigation**: Postgres eventually rolls back any open transactions when the connection drops. However, it creates write-then-rollback noise in Postgres WAL and can leave sequences in unexpected states.

**Fix**: `scheduler.shutdown(wait=True)` with a timeout, or add `try/except CancelledError` + explicit rollback in the job functions.

---

### Finding 5 — 🔴 5 `aiohttp.ClientSession` objects are NEVER CLOSED

**Files**: `dexscreener_client.py`, `onchain_client.py`, `defillama_client.py`, `coingecko_client.py`, `github_client.py`

Each has:
```python
async def _get_session(self) -> aiohttp.ClientSession:
    if self._session is None or self._session.closed:
        self._session = aiohttp.ClientSession(...)
    return self._session

async def close(self):
    if self._session and not self._session.closed:
        await self._session.close()
```

**The `close()` method exists but is NEVER CALLED:**

```python
# main_crypto.py shutdown():
await self.mcp.close()          # ✅ closes mcp_client's httpx + bybit
await self.redis_client.close() # ✅
await close_db()                # ✅
# ❌ No close() for research clients
```

The research clients (`dexscreener_client`, `coingecko_client`, etc.) are created by `research_orchestrator.py` or directly inside AODE jobs (e.g., `_job_aode_research`). They are instantiated fresh on each job run — each creating a new `aiohttp.ClientSession`. Since `close()` is never called, **every aiohttp session created during AODE research leaks its TCP connection pool**.

With AODE running every 4 hours: 5 clients × unlimited open TCP connections per session = growing socket leak over multi-day uptime.

**Quantified risk**: With default aiohttp `TCPConnector(limit=100)`, each session can hold up to 100 TCP connections. 5 clients × 100 = 500 potential leaked TCP sockets per AODE cycle.

---

### Finding 6 — 🟠 `emergency._client` is a module-level Redis singleton with NO cleanup

**File**: [`src/risk/emergency.py:18-24`](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/risk/emergency.py#L18-L24)

```python
_client: aioredis.Redis | None = None

def _get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client
```

**Issue**: `_client` is created lazily on first `activate()` / `is_active()` call. It is **never closed** — not in `close_db()`, not in `shutdown()`, not anywhere. On process exit this is a 1-connection leak (minor). But when the same process runs emergency stop multiple times (e.g., test harness or bot restart), each new event loop gets a new Redis client on the old loop's connection pool — classic cross-loop issue.

**More importantly**: This Redis connection is a **separate pool** from the main `redis_client` in `main_crypto.py`. The system has at least **3 separate Redis connection pools** (`main_crypto.redis_client`, `emergency._client`, and the bot's `redis_client`) all connecting to the same Redis server simultaneously.

---

### Finding 7 — 🟠 `_connection_health_loop` in `crypto_main.py` is another cross-loop DB caller

**File**: [`src/bot/crypto_main.py:182-185`](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/bot/crypto_main.py#L182-L185)

```python
async def _connection_health_loop():
    ...
    async with async_session() as session:
        await session.execute(text("SELECT 1"))
```

**The bot process (`crypto_main.py`) runs in a DIFFERENT process from `main_crypto.py`.** The Telegram bot is started separately. However, both import `src.models.database`, which has module-level globals `_engine` and `_session_factory`.

**If both processes share the same Python interpreter** (e.g., `crypto_main.py` is imported by `main_crypto.py` or vice versa), the `_engine` global is shared. If they're separate OS processes, each has its own module state — fine.

**The actual risk**: `bot/crypto_main.py:245` runs `asyncio.create_task(_connection_health_loop())` — this task is **not stored** and runs `async with async_session()` every 60s. The task has no cancellation path, no stored reference, and runs `async_session()` which now creates a **new** `async_sessionmaker` on every call (current `get_session()` behavior).

---

### Finding 8 — 🟡 `api/crypto_control.py:40` creates a Redis client per API request, never closes it

**File**: [`src/api/crypto_control.py:40`](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/api/crypto_control.py#L40)

```python
@router.get("/asm/status")
async def asm_status():
    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    active = await r.get("karsa:asm:active")
    paused = await r.get("karsa:asm:paused")
    ...
    # r is never closed
```

Every call to `/api/v1/crypto/asm/status` creates a new aioredis connection pool and never closes it. In redis-py v4+, `from_url()` creates a `ConnectionPool` with default `max_connections=2^31`. Each call opens 1-2 TCP connections that accumulate until GC eventually collects the object.

**Frequency**: If a monitoring system polls this endpoint (e.g., every 5 seconds), this creates ~720 leaked connection pools per hour.

---

### Finding 9 — 🟡 `crypto_risk_manager.py:574` creates a Redis client in hot path, with `close()` but no guarantee

**File**: [`src/risk/crypto_risk_manager.py:574`](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/risk/crypto_risk_manager.py#L574)

```python
r = redis_mod.from_url(settings.REDIS_URL, decode_responses=True)
cooldown = await r.get("karsa:crypto_cooldown")
await r.close()    # ← close exists
```

`close()` is called, but if `r.get(...)` raises an exception before `r.close()`, the client leaks. This is in the trade gate — called on every position open attempt.

**Fix**: Wrap in `try/finally`:
```python
r = redis_mod.from_url(...)
try:
    cooldown = await r.get("karsa:crypto_cooldown")
finally:
    await r.close()
```

---

### Finding 10 — 🟡 `crypto_handlers.py:26` creates a Redis client per Telegram message as fallback

**File**: [`src/bot/crypto_handlers.py:22-26`](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/bot/crypto_handlers.py#L22-L26)

```python
def _get_redis(context):
    client = context.bot_data.get("redis_client")
    if client:
        return client
    return redis.from_url(settings.REDIS_URL, decode_responses=True)  # ← leaked if fallback
```

If `context.bot_data["redis_client"]` is None (during startup race or after a redis reconnect), every Telegram message handler that calls `_get_redis()` creates a **new Redis client that is never closed**. This is the fallback path — normally not hit, but under transient conditions it becomes a slow leak.

---

### Finding 11 — 🟡 `advisory/crypto_regime.py:61` creates a **synchronous** Redis client inside an async task

**File**: [`src/advisory/crypto_regime.py:61`](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/advisory/crypto_regime.py#L61)

```python
import redis as sync_redis
r = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
chat_id_str = r.get("karsa:telegram_chat_id")
```

This is called inside `_alert_regime_transition()`, which is called via `loop.create_task(...)`. **Calling a synchronous blocking Redis operation inside an async coroutine blocks the event loop** for the entire duration of the TCP round-trip (typically 1-5ms, but up to seconds if Redis is slow).

Also, the sync `r` object is **never closed** — sync redis-py creates a connection pool, and `r` going out of scope does not close it.

---

### Finding 12 — 🟡 `patch_websocket.py` globally disables SSL verification for the entire Python process

**File**: [`src/patch_websocket.py`](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/patch_websocket.py)

```python
ssl._create_default_https_context = ssl._create_unverified_context
ssl.create_default_context = ssl._create_unverified_context
```

This is applied at **module import time** and affects **all SSL connections in the process** — including Bybit API, LLM API (9Router), Postgres TLS, and Redis TLS if configured. This is a security concern, but more relevantly it means any TLS handshake failure that would normally surface as a certificate error is silently swallowed, making connection failures harder to diagnose.

---

## False Negatives from First Audit — Explained

### Why were Findings 5, 6, 8, 10 missed?

The first audit focused exclusively on `src/models/database.py` and the three callers of `pool_reset()`. It **never traced the research/AODE path** (no grep for `aiohttp`, `defillama_client`, `coingecko_client`). The audit's dependency graph (Phase 12) listed PostgreSQL as the only leaf resource — Redis and HTTP were treated as out-of-scope.

### Why was Finding 3 (pubsub) missed?

The audit examined `sl_engine.py` DB session usage but did not examine its Redis pubsub lifecycle. "DB" was interpreted too narrowly as "SQLAlchemy sessions."

### Why was Finding 1 (triple patch) called "mitigated"?

The first auditor saw that `database.py` applies a patch and assumed the duplicates in `main.py`/`main_crypto.py` were harmless. They are harmless today — but they create a hidden correctness dependency on import order. Not "mitigated," just "not currently broken."

---

## First-Audit Assumptions That Are Incorrect

### Assumption 1: "All 150+ call sites use `async with`"

**Status**: Confirmed correct for SQLAlchemy sessions. However, this claim was used to conclude the session tier is safe — it ignores that `get_session()` creates a new `async_sessionmaker` on every call (the factory object leak), and that callers in `_connection_health_loop` and `bot/crypto_main.py` run in contexts where the engine may be on a different state.

### Assumption 2: "Pool reset race (dispose outside lock) is an intentional design choice"

**Status**: The analysis noted this correctly, but then concluded "acceptable trade-off." The second-pass reveals this is NOT just a timing issue — between `_engine = None` and `dispose()` completing, `_get_or_create_engine()` will create a **new engine that will use the same asyncpg connection pool under a new SQLAlchemy wrapper**. When the old `dispose()` finishes, it terminates the underlying asyncpg pool connections — INCLUDING connections that were just checked out from the **new** engine's pool (since they share the same asyncpg pool object). This causes `asyncpg.exceptions.InterfaceError: connection is closed` errors on active queries.

### Assumption 3: "Fire-and-forget tasks only cause shutdown noise"

**Status**: Incorrect for the ASM task. `autonomous_session._run_loop()` runs an infinite loop that opens DB sessions every 15 minutes. If it's not cancelled before `close_db()`, it will attempt to open a DB session on a disposed engine, get `RuntimeError`, log it, retry in 15 minutes — by which point the process has already exited. The real risk is that if `close_db()` is called while the ASM is mid-transaction, the `CancelledError` from engine disposal leaves that transaction uncommitted (Postgres rolls it back on disconnect, but the Python-side state is inconsistent).

---

## Remaining Blind Spots for Manual Verification

> [!IMPORTANT]
> These cannot be verified by static analysis alone — require runtime observation.

| # | Blind Spot | How to Verify |
|---|---|---|
| 1 | **Does `crypto_main.py` (bot) run in same process as `main_crypto.py`?** | Check Docker Compose service definitions. If separate services → module globals don't share. If same → critical cross-contamination. |
| 2 | **Are aiohttp clients truly GC'd between AODE runs?** | Run `ss -tnp | grep postgres` before/after an AODE cycle. Count open sockets. |
| 3 | **Does APScheduler `wait=False` actually interrupt mid-transaction jobs?** | Set a breakpoint in `_job_sync_crypto_positions` → trigger SIGTERM → observe Postgres `pg_stat_activity` for aborted transactions. |
| 4 | **Does `pubsub.unsubscribe()` actually release the Redis connection?** | Check `redis-cli client list` before/after `sl_engine.stop()`. Connection count should decrease by 1. |
| 5 | **Is `patch_websocket.py` imported in the crypto process?** | `grep -rn "patch_websocket" src/` — if imported, all SSL is disabled globally. |
| 6 | **Does the health engine (`NullPool`) accumulate connections?** | `SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE '%health%'` — should always be 0 between health checks. |
| 7 | **Does `asyncio.to_thread(bybit_call)` hold the GIL long enough to cause event loop stall?** | Check watchdog `event_loop_lag_sec` metric during a busy Bybit call burst. Should stay <1s. |

---

## Revised Priority List (All Issues Combined)

| Priority | Finding | Severity | Fix |
|---|---|---|---|
| **P0** | Dual event loop — uvicorn thread | 🔴 | `create_task(server.serve())` |
| **P0** | 5 aiohttp sessions never closed (AODE clients) | 🔴 | Call `client.close()` in job finally blocks |
| **P1** | Redis client per API request in `crypto_control.py` | 🟠 | Use `app.state.redis_client` |
| **P1** | Pool undersized (15 slots vs 20+ concurrent jobs) | 🟠 | `_POOL_SIZE=20, _MAX_OVERFLOW=10` |
| **P1** | `get_session()` creates factory every call | 🟠 | Cache factory, reset in `pool_reset()` |
| **P1** | `emergency._client` never closed | 🟠 | Add `emergency.close()` to shutdown sequence |
| **P2** | `asyncio.Lock` not thread-safe | 🟠 | Wrap with `threading.Lock` |
| **P2** | Triple monkey-patch — last one wins silently | 🟠 | Remove duplicates from `main.py`/`main_crypto.py` |
| **P2** | `pubsub.close()` missing in `sl_engine.finally` | 🟠 | Add `await pubsub.close()` |
| **P2** | `scheduler.shutdown(wait=False)` mid-tx abandonment | 🟠 | Change to `wait=True` with 5s timeout |
| **P3** | Sync Redis call blocks event loop in `crypto_regime.py` | 🟡 | Use `asyncio.to_thread()` or async Redis |
| **P3** | `crypto_risk_manager.py` Redis not closed on exception | 🟡 | Add `try/finally` around `r.close()` |
| **P3** | `crypto_handlers.py` fallback Redis never closed | 🟡 | Log warning + close immediately after use |
| **P3** | Fire-and-forget tasks not cancelled on shutdown | 🟡 | Add `_background_tasks` list |

---

## Audit Confidence Score

**57 / 100**

### Justification

| Factor | Score | Reason |
|---|---|---|
| SQLAlchemy/asyncpg layer | 90% | Exhaustive — every code path traced, all callers found |
| Redis connection lifecycle | 55% | Found 6 Redis leak sites; cannot confirm total count without runtime |
| HTTP client lifecycle | 60% | Found all `aiohttp` + `httpx` clients; cannot verify GC timing |
| Background task lifecycle | 65% | All `create_task` sites found; cancellation paths not fully traced for all |
| Event loop cross-contamination | 50% | Bot/orchestrator process boundary unclear from static analysis alone |
| AODE/Research layer | 40% | 8 research files; only surface-scanned, not deep-traced |
| Backtest engine | 20% | Uses `async_session` but is a separate invocation context — not traced at all |
| Race conditions | 45% | Identified the theoretical windows; cannot confirm whether they're hit in practice |

**Why not higher**: 3 unresolved process boundary questions (same process vs separate?), 5 research files not deep-traced, backtest engine completely unaudited, and all timing-dependent race conditions require runtime observation to confirm.
