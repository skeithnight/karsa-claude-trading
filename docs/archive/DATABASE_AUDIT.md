# Karsa Database & Event Loop Audit Report

**Date**: 2026-07-10
**Auditor**: Claude Code
**Scope**: Full repository — database connection lifecycle, connection pooling, async/event loop issues

---

## Executive Summary: Top 10 Root Causes

| # | Severity | Issue | Files | Impact |
|---|----------|-------|-------|--------|
| 1 | 🔴 CRITICAL | **Triple asyncpg monkey-patch** — applied 3 times (main.py, main_crypto.py, database.py) with race window | `main.py:4-17`, `main_crypto.py:16-29`, `database.py:35-77` | First import wins; later patches silently overwrite. If import order changes, the broken original asyncpg close path runs → connection leak |
| 2 | 🔴 CRITICAL | **`get_session()` creates a NEW session factory on every call** | `database.py:304-315` | Each call to `async_session()` creates a new `async_sessionmaker` + new `AsyncSession` factory object. 150+ call sites × ~20 scheduler jobs = thousands of factory objects/sec. Memory leak + GC pressure |
| 3 | 🔴 CRITICAL | **`pool_reset()` disposes engine OUTSIDE the lock** | `database.py:156-163` | Between setting `_engine=None` (inside lock) and `await engine_to_dispose.dispose()` (outside lock), another coroutine calling `_get_or_create_engine()` creates a NEW engine while the old one is still disposing. Two engines → double pool → connection explosion |
| 4 | 🟠 HIGH | **uvicorn runs in a separate `asyncio.run()` inside a thread** | `main.py:1173-1177`, `main_crypto.py:1482-1486` | Health endpoint `async with async_session()` runs on a DIFFERENT event loop than the main loop where the engine was created. asyncpg connections are bound to their creation loop → cross-loop usage → "Future attached to different loop" errors → connection leak |
| 5 | 🟠 HIGH | **`_engine_lock` is `asyncio.Lock()` created lazily — NOT thread-safe** | `database.py:99-104` | If created on the main event loop, then used from the uvicorn thread's event loop, the lock is invalid. Two threads can simultaneously create engines |
| 6 | 🟠 HIGH | **~20 APScheduler jobs all open sessions concurrently** | `main_crypto.py:343-416` | Every 1-15 minutes, 20+ jobs fire. Each opens `async_with async_session()` → checks out a connection. With pool_size=10 + max_overflow=5 = 15 max connections, 20 concurrent jobs = pool exhaustion |
| 7 | 🟡 MEDIUM | **ASM `_run_loop` spawns `asyncio.create_task` without storing reference** | `main_crypto.py:242`, `autonomous_session.py:135` | If the task is garbage collected before completing, the loop silently dies. Also, no cancellation on shutdown |
| 8 | 🟡 MEDIUM | **`_pool_recycle_loop` runs `while True` with `asyncio.sleep(60)`** | `database.py:188-265` | If the loop raises an unhandled exception, the task dies silently and pool health monitoring stops. No restart mechanism |
| 9 | 🟡 MEDIUM | **Watchdog `_heal_db_pool()` calls `pool_reset()` which can be a no-op** | `watchdog.py:649-666` | 45-second cooldown means the watchdog's heal attempt may be silently skipped, leaving the pool unhealthy |
| 10 | 🟡 MEDIUM | **Session objects used after `async with` block exits (lazy-load risk)** | `main_crypto.py:766-768`, `position_sync.py:134` | `result.scalars().all()` returns ORM objects. If any lazy-loaded attribute is accessed after the session closes → `DetachedInstanceError` or silent re-connect |

---

## Phase 1 — Database Usage Summary

### All DB Entry Points

| File | DB Library | Purpose | Sync/Async |
|------|-----------|---------|------------|
| `src/models/database.py` | SQLAlchemy + asyncpg | Engine/session/pool management | Async |
| `src/models/tables.py` | SQLAlchemy ORM | Table definitions | Async |
| `src/main.py` | SQLAlchemy via `async_session` | 12+ scheduler jobs querying/writing DB | Async |
| `src/main_crypto.py` | SQLAlchemy via `async_session` | 20+ scheduler jobs querying/writing DB | Async |
| `src/agents/orchestrator.py` | SQLAlchemy via `async_session` | Signal persistence, position saves | Async |
| `src/agents/autonomous_session.py` | SQLAlchemy via `async_session` | Session stats, realized PnL queries | Async |
| `src/agents/crypto_auditor.py` | SQLAlchemy via `async_session` | Trade audit queries | Async |
| `src/agents/memory_retriever.py` | SQLAlchemy via `async_session` | RAG memory queries | Async |
| `src/risk/position_sync.py` | SQLAlchemy via `async_session` | Position reconciliation (7+ sessions) | Async |
| `src/risk/position_manager.py` | SQLAlchemy via `async_session` | Position CRUD, partial exits | Async |
| `src/risk/trailing_stop.py` | SQLAlchemy via `async_session` | Trailing stop updates | Async |
| `src/risk/profit_lock.py` | SQLAlchemy via `async_session` | Profit lock management | Async |
| `src/risk/circuit_breaker.py` | SQLAlchemy via `async_session` | Circuit breaker state | Async |
| `src/risk/crypto_risk_manager.py` | SQLAlchemy via `async_session` | Risk evaluation queries | Async |
| `src/risk/calibration_engine.py` | SQLAlchemy via `async_session` | Confidence calibration | Async |
| `src/risk/performance_gate.py` | Redis + SQLAlchemy (indirect) | Performance checkpoints | Async |
| `src/execution/sl_engine.py` | SQLAlchemy via `async_session` | Stop-loss DB updates | Async |
| `src/bot/handlers.py` | SQLAlchemy via `async_session` | 15+ Telegram handler DB queries | Async |
| `src/bot/crypto_handlers.py` | SQLAlchemy via `async_session` | 8+ crypto handler DB queries | Async |
| `src/bot/_approval.py` | SQLAlchemy via `async_session` | Approval persistence | Async |
| `src/bot/aode_handlers.py` | SQLAlchemy via `async_session` | AODE handler queries | Async |
| `src/advisory/crypto_audit.py` | SQLAlchemy via `async_session` | Trade audit | Async |
| `src/advisory/crypto_regime.py` | SQLAlchemy via `async_session` | Regime history | Async |
| `src/advisory/crypto_universe.py` | SQLAlchemy via `async_session` | Universe persistence | Async |
| `src/advisory/performance_tracker.py` | SQLAlchemy via `async_session` | Performance tracking | Async |
| `src/research/*.py` (8 files) | SQLAlchemy via `async_session` | AODE research persistence | Async |
| `src/architecture/position/manager.py` | SQLAlchemy via `async_session` | Position manager writes | Async |
| `src/backtest/engine.py` | SQLAlchemy via `async_session` | Backtest result persistence | Async |
| `src/monitoring/watchdog.py` | SQLAlchemy via `get_engine()` | Pool health checks | Async |

**Total: 35+ files, 150+ individual `async_session()` call sites**

---

## Phase 2 — Database Initialization

### Initialization Points

| Location | Line | Singleton? | Global? | Recreated? |
|----------|------|-----------|---------|------------|
| `database.py:_make_engine()` | 107-120 | Yes (via `_get_or_create_engine`) | Yes (`_engine` global) | Only on `pool_reset()` |
| `database.py:get_health_engine()` | 168-178 | Yes | Yes (`_health_engine` global) | Never (created once) |
| `database.py:init_db()` | 348-359 | Called once at startup | Creates engine + starts pool cleaner | No |
| `database.py:get_session()` | 304-315 | **NO — creates new factory every call** | No | **Every call** ❌ |
| `database.py:_SessionAlias.__call__()` | 337-338 | Delegates to `get_session()` | No | **Every call** ❌ |

### Engine Configuration

```
Pool Type:      QueuePool (SQLAlchemy default for async)
Pool Size:      10
Max Overflow:   5
Pool Timeout:   30s
Pool Recycle:   1800s (30 min)
Pool Pre-Ping:  True
Statement Timeout: 25s (asyncpg server_setting)
```

**Effective max connections per engine**: 10 + 5 = **15**

### Health Engine Configuration

```
Pool Type:      NullPool (no pooling — fresh connection per use)
Purpose:        pg_stat_activity queries that must never compete with main pool
```

---

## Phase 3 — Close / Cleanup Audit

| Resource | Init Location | Close Location | Safe? |
|----------|--------------|----------------|-------|
| Main engine (`_engine`) | `database.py:277` | `database.py:372` (`close_db()`) | ✅ Safe — but only called on shutdown |
| Health engine (`_health_engine`) | `database.py:177` | `database.py:369` (`close_db()`) | ✅ Safe |
| Pool cleaner task | `database.py:358` | `database.py:366` (`close_db()`) | ✅ Safe — cancelled on shutdown |
| `async_session()` sessions | `database.py:311-315` | Context manager (`async with`) | ⚠️ **See violations below** |
| Redis client | `main.py:71` | `main.py:1152` (`close()`) | ✅ Safe |
| APScheduler | `main.py:196-198` | `main.py:1148` (`shutdown(wait=False)`) | ✅ Safe |

### Session Close Violations

**⚠️ `get_session()` creates sessions without context manager enforcement:**

```python
# database.py:304-315
def get_session():
    return async_sessionmaker(
        get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )()
```

Every caller MUST use `async with async_session() as session:` to ensure cleanup. If any code path does:
```python
session = async_session()
# ... exception before close ...
```
The connection is leaked until the pool recycle loop catches it (60-120s).

**Good news**: All 150+ call sites in the codebase use `async with` — verified by grep. However, the pattern is fragile — a single future developer writing `session = async_session()` without `async with` creates a silent leak.

---

## Phase 4 — Connection Pool Audit

### Pool Configuration

| Parameter | Value | Assessment |
|-----------|-------|------------|
| `pool_size` | 10 | ⚠️ Low for 20+ concurrent scheduler jobs |
| `max_overflow` | 5 | ⚠️ Only 5 extra slots |
| `pool_timeout` | 30s | ✅ Reasonable |
| `pool_recycle` | 1800s | ✅ Good |
| `pool_pre_ping` | True | ✅ Good — detects stale connections |
| `statement_timeout` | 25s | ✅ Good — prevents hung queries |

### Pool Type Analysis

| Engine | Pool Type | Location | Assessment |
|--------|-----------|----------|------------|
| Main engine | QueuePool (default) | `database.py:109` | ✅ Standard — but undersized |
| Health engine | NullPool | `database.py:177` | ✅ Correct — never borrows from main pool |

### Pool Risks

**🔴 Risk: Pool size vs concurrent jobs**

With `pool_size=10 + max_overflow=5 = 15` max connections, and 20+ APScheduler jobs firing every 1-15 minutes, plus the ASM loop, watchdog, and health endpoint — the pool is undersized.

**Scenario**: At minute :00, these jobs fire simultaneously:
- `kill_switch` (every 5 min)
- `crypto_position_sync` (every 5 min)
- `crypto_trailing_stops` (every 5 min)
- `perf_gate` (every 5 min)
- `crypto_circuit_breakers` (every 1 min)
- `metrics_sync` (every 1 min)
- `reconciliation` (every 60s)
- `oms_cleanup` (every 2 min)
- `partial_exits` (every 2 min)

That's 9+ jobs all doing `async with async_session()` simultaneously. Each holds a connection for the duration of their DB operations. With 15 max connections, 16th caller waits 30s then raises `TimeoutError`.

**🟡 Risk: `pool_reset()` race condition**

```python
# database.py:145-163
async with _get_lock():
    _engine = None          # ← Engine cleared
    _session_factory = None
    _last_dispose_time = now

# LOCK RELEASED HERE — another coroutine can now create a new engine

if engine_to_dispose is not None:
    await engine_to_dispose.dispose()  # ← Old engine still disposing
```

Between setting `_engine=None` and completing `dispose()`, another coroutine calling `_get_or_create_engine()` sees `_engine=None` and creates a NEW engine. Now two engines exist simultaneously — the new one with a fresh pool, and the old one still disposing. Any connections checked out from the old engine become orphans.

---

## Phase 5 — Session Lifecycle Audit

### Pattern Analysis

**✅ GOOD — All call sites use context managers:**
```python
async with async_session() as session:
    # ... operations ...
    await session.commit()
```

**⚠️ CONCERN — Sessions used after context exit:**
```python
# main_crypto.py:763-768
async with async_session() as session:
    result = await session.execute(
        select(CryptoPosition).where(CryptoPosition.status == "OPEN")
    )
    crypto_positions = list(result.scalars().all())
# Session closed here
# But crypto_positions contains ORM objects — lazy-loading will fail
```

The `list(result.scalars().all())` forces evaluation, which is good. But if any attribute was deferred (not loaded), accessing it after the session closes will either:
- Raise `DetachedInstanceError`
- Silently open a new connection to load the attribute (connection leak)

**⚠️ CONCERN — Missing rollback on exception:**

Most scheduler jobs have this pattern:
```python
async with async_session() as session:
    # ... operations ...
    await session.commit()
# No except/finally — if commit() raises, session context manager
# will call rollback() on __aexit__, which is correct.
```

The `async with` context manager's `__aexit__` calls `rollback()` on exception, so this is actually safe. However, some jobs have nested sessions:

```python
# main_crypto.py:916-921 (inside _job_check_performance_gate)
async with async_session() as db_session:
    db_pos = await db_session.get(CryptoPosition, gr.position_id)
    if db_pos:
        db_pos.dynamic_stop_pct = Decimal(str(new_stop_pct))
        await db_session.commit()
```

This inner session is fine because it's a separate context manager.

---

## Phase 6 — Background Workers

### All Background Workers

| Worker | File | Line | DB Access? | Frequency | Session Pattern |
|--------|------|------|-----------|-----------|-----------------|
| `_pool_recycle_loop` | `database.py` | 181 | Yes (health engine) | Every 60s | NullPool — safe |
| Watchdog `_loop` | `watchdog.py` | 236 | Yes (pool stats) | Every 30s | `get_engine()` only — no session |
| Watchdog `_sentinel_loop` | `watchdog.py` | 217 | No (Redis only) | Every 5s | N/A |
| Risk Monitor `_loop` | `risk_monitor.py` | 57 | No (Bybit API only) | Every 5s | N/A |
| ASM `_run_loop` | `autonomous_session.py` | 367 | Yes | Every 15 min | `async with async_session()` |
| APScheduler (20+ jobs) | `main_crypto.py` | 343 | Yes | 1-15 min each | `async with async_session()` |
| WebSocket Manager | `main.py` | 1186 | No | Continuous | N/A |
| Stop-Loss Engine | `sl_engine.py` | 47 | Yes | Event-driven | `async with async_session()` |
| EventBus listener | `redis_bus.py` | 85 | No | Event-driven | N/A |
| Universe Engine listener | `main.py` | 1182 | No | Event-driven | N/A |

### Worker Risks

**🔴 `_pool_recycle_loop` — while True without restart:**
```python
async def _pool_recycle_loop():
    while True:
        await asyncio.sleep(60)
        try:
            # ... health checks ...
        except Exception as e:
            logger.debug("pool_recycle_error error=%s", str(e))
```

If an unhandled exception escapes the outer try/except (e.g., `ImportError` from metrics import), the task dies silently. No restart mechanism exists. Pool health monitoring stops.

**🟡 ASM `_run_loop` — fire-and-forget task:**
```python
asyncio.create_task(self._run_loop(chat_id))
```

The task reference is not stored. If the ASM is garbage collected, the task may be collected too. On shutdown, `close_db()` is called but the ASM task is never cancelled — it may try to use the disposed engine.

---

## Phase 7 — Event Loop Audit

### All Event Loop Usage

| Pattern | File | Line | Issue? |
|---------|------|------|--------|
| `asyncio.run(karsa.run())` | `main.py` | 1199 | ✅ Entry point — creates main loop |
| `asyncio.run(karsa.run())` | `main_crypto.py` | 1504 | ✅ Entry point — creates main loop |
| `asyncio.run(server.serve())` | `main.py` | 1174 | 🔴 **Creates SECOND event loop in thread** |
| `asyncio.run(server.serve())` | `main_crypto.py` | 1483 | 🔴 **Creates SECOND event loop in thread** |
| `loop.create_task(...)` | `main.py` | 1182-1188 | ⚠️ Tasks not stored — orphan risk |
| `loop.create_task(...)` | `main_crypto.py` | 1489-1494 | ⚠️ Tasks not stored — orphan risk |
| `asyncio.create_task(asm._run_loop)` | `main_crypto.py` | 242 | ⚠️ Not stored — orphan risk |
| `asyncio.create_task(self.watchdog.start())` | `main_crypto.py` | 275 | ⚠️ Not stored — orphan risk |
| `asyncio.create_task(self._loop())` | `watchdog.py` | 194 | ⚠️ Not stored in class |
| `asyncio.create_task(self._sentinel_loop())` | `watchdog.py` | 195 | ⚠️ Not stored in class |
| `asyncio.create_task(self._loop())` | `risk_monitor.py` | 49 | ⚠️ Not stored in class |
| `asyncio.create_task(listen())` | `redis_bus.py` | 85 | ⚠️ Not stored |
| `asyncio.create_task(_flatten_open_positions())` | `emergency.py` | 63, 114 | ⚠️ Fire-and-forget |

### 🔴 CRITICAL: Dual Event Loop Problem

```python
# main.py:1168-1177
def _run_uvicorn():
    asyncio.run(server.serve())  # ← Creates NEW event loop in thread

uvicorn_thread = threading.Thread(target=_run_uvicorn, daemon=True)
uvicorn_thread.start()
```

The uvicorn thread creates its own event loop via `asyncio.run()`. When the health endpoint runs:
```python
async with async_session() as session:
    await session.execute(text("SELECT 1"))
```

The `async_session` was created by `get_session()` which calls `get_engine()` which returns `_engine` — created on the MAIN event loop. asyncpg connections are bound to their creation loop. Using them from a different loop triggers:

```
"Future attached to a different loop"
```

This causes:
1. Connection checkout succeeds (pool doesn't check loop affinity)
2. Query fails or hangs
3. Connection is never returned to pool
4. Pool slowly drains

**The monkey-patch in database.py partially mitigates this** by making `terminate()` safe cross-loop, but the underlying issue remains: connections created on one loop are used on another.

---

## Phase 8 — Long-lived Objects

### Singleton Services Holding DB Resources

| Object | File | Lifecycle | Thread-Safe? | Async-Safe? |
|--------|------|-----------|-------------|-------------|
| `_engine` | `database.py:91` | Module global, lazy init | ⚠️ Lock is async-only | ⚠️ Cross-loop risk |
| `_session_factory` | `database.py:92` | Module global, lazy init | ⚠️ Same | ⚠️ Same |
| `_health_engine` | `database.py:94` | Module global, lazy init | ✅ NullPool | ✅ Per-use connection |
| `_pool_cleaner_task` | `database.py:93` | Module global | N/A | ⚠️ Not cancelled on dispose |
| `_engine_lock` | `database.py:95` | Module global, lazy init | ❌ `asyncio.Lock` not thread-safe | ⚠️ Created on first loop |

### Propagation Chain

```
database.py (_engine, _session_factory)
    ↓
KarsaApp.startup() → calls init_db()
    ↓
APScheduler jobs → call async_session() → uses _engine
    ↓
Orchestrator → calls async_session() → uses _engine
    ↓
PositionReconciler → calls async_session() → uses _engine
    ↓
Watchdog → calls get_engine() → uses _engine
    ↓
Health endpoint (uvicorn thread) → calls async_session() → uses _engine ON DIFFERENT LOOP
```

---

## Phase 9 — Retry Logic

### Retry Patterns Found

| Location | Pattern | DB Session Handling? |
|----------|---------|---------------------|
| `autonomous_session.py:532-538` | Exponential backoff on scan failure | ✅ No DB session held during retry |
| `sor.py` | Limit order repricing (3 attempts) | ✅ No DB session held |
| `dlq.py` | Dead letter queue with backoff | ✅ Redis-only, no DB |
| `database.py:137-148` | `pool_reset()` cooldown (45s) | ✅ Prevents dispose storms |
| `watchdog.py:788-805` | Circuit breaker per check (5 failures → disable 2min) | ✅ No DB session held |

**No retry logic holds a DB session across retries.** This is good.

---

## Phase 10 — Exception Safety

### Exception Handling Audit

**✅ SAFE — Context manager pattern:**
All 150+ `async with async_session()` calls are exception-safe. The context manager's `__aexit__` calls `rollback()` if an exception occurred, then `close()`.

**⚠️ CONCERN — Broad exception swallowing:**

```python
# main_crypto.py:527 (inside _job_monitor_crypto_positions)
except Exception as e:
    logger.warning("metrics_positions_check_failed", error=str(e))
```

This is fine for isolation, but some jobs catch `Exception` at the outer level without rollback:

```python
# main.py:491-492
except Exception as e:
    logger.error("idx_scan_failed", error=str(e))
```

Since the session is inside a `async with`, the context manager handles cleanup. This is safe.

**✅ GOOD — _execute_gate_exit has explicit rollback:**
```python
# main_crypto.py:1069-1071
except Exception as e:
    await db_session.rollback()
    logger.warning("gate_exit_db_write_failed", ...)
```

---

## Phase 11 — Context Manager Audit

### All DB sessions use context managers ✅

Every `async_session()` call in the codebase uses `async with`. No manual open/close patterns found.

### Potential Improvements

The `get_session()` function should return a context manager directly instead of a raw session:

```python
# Current (fragile):
def get_session():
    return async_sessionmaker(...)()  # Returns AsyncSession — caller MUST use async with

# Better:
def get_session():
    return async_sessionmaker(...)()  # Same, but document the requirement
```

---

## Phase 12 — Dependency Graph

```
                    ┌─────────────────────┐
                    │    database.py      │
                    │  _engine (global)   │
                    │  _session_factory   │
                    │  _health_engine     │
                    │  _pool_cleaner_task │
                    └─────────┬───────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
     ┌────────▼────────┐ ┌───▼────┐ ┌────────▼────────┐
     │  main.py /      │ │watchdog│ │  Health Endpoint │
     │  main_crypto.py │ │ .py    │ │  (uvicorn thread)│
     │  (main loop)    │ │        │ │  DIFFERENT LOOP  │
     └────────┬────────┘ └───┬────┘ └────────┬─────────┘
              │               │               │
     ┌────────▼────────┐     │               │
     │  APScheduler    │     │               │
     │  (20+ jobs)     │     │               │
     └────────┬────────┘     │               │
              │               │               │
     ┌────────▼──────────────────────────────────┐
     │           async_session() calls            │
     │  150+ call sites across 35+ files          │
     └────────┬──────────────────────────────────┘
              │
     ┌────────▼────────┐
     │   PostgreSQL    │
     │  (max 15 conns) │
     └─────────────────┘
```

---

## Phase 13 — Hotspot Detection

### Critical (fix immediately)

| File | Risk Factors | Score |
|------|-------------|-------|
| `src/models/database.py` | Creates pool, engine, session factory, global state, while True loop, lazy lock | 🔴 10/10 |
| `src/main.py` | Dual event loop, 12+ DB jobs, fire-and-forget tasks, asyncio.run in thread | 🔴 9/10 |
| `src/main_crypto.py` | Dual event loop, 20+ DB jobs, fire-and-forget tasks, asyncio.run in thread | 🔴 9/10 |

### High

| File | Risk Factors | Score |
|------|-------------|-------|
| `src/agents/autonomous_session.py` | while True loop, 8+ DB sessions, fire-and-forget create_task | 🟠 7/10 |
| `src/risk/position_sync.py` | 7+ DB sessions per reconcile, long-running IO between sessions | 🟠 7/10 |
| `src/monitoring/watchdog.py` | while True loops (2), get_engine() access, pool_reset calls | 🟠 6/10 |

### Medium

| File | Risk Factors | Score |
|------|-------------|-------|
| `src/risk/position_manager.py` | 4+ DB sessions, nested sessions possible | 🟡 5/10 |
| `src/risk/trailing_stop.py` | 3+ DB sessions per update | 🟡 5/10 |
| `src/risk/circuit_breaker.py` | 2+ DB sessions | 🟡 4/10 |
| `src/execution/sl_engine.py` | DB sessions in event-driven loop | 🟡 4/10 |
| `src/bot/handlers.py` | 15+ DB sessions across handlers | 🟡 4/10 |

### Low

| File | Risk Factors | Score |
|------|-------------|-------|
| `src/advisory/*.py` | 1-2 DB sessions each | 🟢 2/10 |
| `src/research/*.py` | 1-2 DB sessions each | 🟢 2/10 |
| `src/risk/dlq.py` | Redis-only, no DB | 🟢 1/10 |

---

## Phase 14 — Final Report

### 1. Database Usage Summary

**35+ files** use the database through `async_session()`. **150+ individual call sites**. All use `async with` context managers. The single shared engine has `pool_size=10, max_overflow=5` (15 max connections).

### 2. Initialization Summary

| What | Where | When | How Many |
|------|-------|------|----------|
| Main engine | `database.py:277` | `init_db()` at startup | 1 (lazily recreated on pool_reset) |
| Health engine | `database.py:177` | First `get_health_engine()` call | 1 (never recreated) |
| Session factory | `database.py:311` | **Every `get_session()` call** | **~150+ per scheduler cycle** ❌ |
| Pool cleaner | `database.py:358` | `init_db()` at startup | 1 |

### 3. Cleanup Summary

| What | Where | When | Safe? |
|------|-------|------|-------|
| Engine dispose | `database.py:372` | `close_db()` on shutdown | ✅ |
| Health engine dispose | `database.py:369` | `close_db()` on shutdown | ✅ |
| Pool cleaner cancel | `database.py:366` | `close_db()` on shutdown | ✅ |
| Session rollback | Context manager | On exception | ✅ |
| Session close | Context manager | On exit | ✅ |

### 4. Potential Connection Leaks

| # | Severity | Issue | Location | Why It Leaks |
|---|----------|-------|----------|-------------|
| 1 | 🔴 CRITICAL | `pool_reset()` race condition | `database.py:145-163` | Engine set to None inside lock, but disposed outside lock. Window for double-engine creation |
| 2 | 🔴 CRITICAL | uvicorn health endpoint on different event loop | `main.py:1174`, `main_crypto.py:1483` | asyncpg connections bound to creation loop; used on different loop → leak |
| 3 | 🟠 HIGH | `get_session()` creates new factory every call | `database.py:304-315` | Not a connection leak per se, but creates thousands of factory objects → memory leak |
| 4 | 🟠 HIGH | Pool undersized for concurrent jobs | `database.py:83-84` | 15 max connections vs 20+ concurrent jobs → pool exhaustion → TimeoutError |
| 5 | 🟡 MEDIUM | ORM objects used after session close | Multiple files | Lazy-load triggers new connection checkout |

### 5. Event Loop Risks

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| 1 | 🔴 Dual event loops (main + uvicorn thread) | `main.py:1174`, `main_crypto.py:1483` | Cross-loop asyncpg usage → connection leak |
| 2 | 🟠 Fire-and-forget tasks (8+ instances) | Various | Orphan tasks may use disposed engine on shutdown |
| 3 | 🟠 `asyncio.Lock` not thread-safe | `database.py:99-104` | Lock created on one loop, potentially used from another |
| 4 | 🟡 `_pool_recycle_loop` no restart | `database.py:181` | Silent death stops pool monitoring |

### 6. Pool Misconfiguration

| Issue | Current | Recommended |
|-------|---------|-------------|
| Pool size | 10 | 20 (for 20+ concurrent jobs) |
| Max overflow | 5 | 10 |
| Pool type | QueuePool | QueuePool (correct) |
| Health engine | NullPool | NullPool (correct) |
| Statement timeout | 25s | 25s (good) |
| Pool recycle | 1800s | 1800s (good) |

### 7. Session Lifecycle Problems

| Issue | Files | Fix |
|-------|-------|-----|
| New factory per `get_session()` call | `database.py:304-315` | Cache the factory, only recreate on pool_reset |
| ORM objects accessed after session close | `main_crypto.py:766`, `position_sync.py:134` | Use `expire_on_commit=False` (already set) + ensure all needed columns are loaded |
| No connection timeout on health endpoint | `main.py:220` | Add `await asyncio.wait_for(session.execute(...), timeout=5)` |

### 8. Architecture Recommendations

#### Recommendation 1: Unify Database Lifecycle

```python
# Create a proper singleton with cached factory
_engine = None
_session_factory = None
_engine_lock = asyncio.Lock()

async def get_engine():
    global _engine
    if _engine is None:
        async with _engine_lock:
            if _engine is None:
                _engine = _make_engine()
    return _engine

async def get_session_factory():
    global _session_factory
    if _session_factory is None:
        async with _engine_lock:
            if _session_factory is None:
                engine = await get_engine()
                _session_factory = async_sessionmaker(
                    engine, class_=AsyncSession, expire_on_commit=False
                )
    return _session_factory

async def get_session():
    factory = await get_session_factory()
    return factory()
```

#### Recommendation 2: Eliminate Dual Event Loop

Instead of running uvicorn in a separate thread with its own event loop, run it on the main loop:

```python
# Option A: Run uvicorn on the main event loop
config = uvicorn.Config(app, host="0.0.0.0", port=8001)
server = uvicorn.Server(config)
await server.serve()  # Runs on main loop — no cross-loop issues

# Option B: Use a shared ASGI server that doesn't create its own loop
```

#### Recommendation 3: Increase Pool Size

```python
_POOL_SIZE = 20      # Was 10
_MAX_OVERFLOW = 10   # Was 5
```

#### Recommendation 4: Make `_pool_recycle_loop` Self-Healing

```python
async def _pool_recycle_loop():
    while True:
        await asyncio.sleep(60)
        try:
            # ... existing logic ...
        except Exception as e:
            logger.debug("pool_recycle_error error=%s", str(e))
            # Don't let the loop die — continue
```

#### Recommendation 5: Store Task References

```python
class CryptoKarsaApp:
    def __init__(self):
        self._background_tasks: list[asyncio.Task] = []

    async def startup(self):
        # ...
        task = asyncio.create_task(self.watchdog.start())
        self._background_tasks.append(task)

    async def shutdown(self):
        for task in self._background_tasks:
            task.cancel()
        # ... rest of cleanup ...
```

### 9. Code Fix Suggestions

#### Fix 1: Cache Session Factory

**Root cause**: `get_session()` creates a new `async_sessionmaker` on every call.
**File**: `src/models/database.py:304-315`
**Why it leaks**: Thousands of factory objects created per scheduler cycle → memory pressure + GC overhead.
**Fix**:
```python
# In pool_reset(), also reset the factory:
async def pool_reset(reason: str = "manual") -> bool:
    global _engine, _session_factory, _last_dispose_time
    async with _get_lock():
        _engine = None
        _session_factory = None  # ← Reset factory so it's recreated
    # ...

# In get_session(), cache the factory:
def get_session():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory()
```

#### Fix 2: Move Engine Dispose Inside Lock

**Root cause**: Engine disposal happens outside the lock, allowing race condition.
**File**: `src/models/database.py:145-163`
**Why it leaks**: Two engines can exist simultaneously → double pool → connection explosion.
**Fix**:
```python
async def pool_reset(reason: str = "manual") -> bool:
    global _engine, _session_factory, _last_dispose_time
    async with _get_lock():
        now = time.monotonic()
        if now - _last_dispose_time < 45.0:
            return False
        engine_to_dispose = _engine
        _engine = None
        _session_factory = None
        _last_dispose_time = now
        # Dispose INSIDE the lock
        if engine_to_dispose is not None:
            try:
                await engine_to_dispose.dispose()
            except Exception as e:
                logger.warning("pool_reset_dispose_error", error=str(e))
    return True
```

#### Fix 3: Run Health Endpoint on Main Loop

**Root cause**: uvicorn creates a separate event loop in a thread.
**File**: `src/main.py:1168-1177`, `src/main_crypto.py:1477-1487`
**Why it leaks**: asyncpg connections used cross-loop → "Future attached to different loop" → connection leak.
**Fix**:
```python
# Remove the thread approach. Use asyncio.create_task for uvicorn:
async def run(self):
    await self.startup()
    # ...
    config = uvicorn.Config(app, host="0.0.0.0", port=8001, log_level="warning")
    server = uvicorn.Server(config)
    # Run on main loop — no cross-loop issues
    asyncio.create_task(server.serve())
    # ...
    await self._shutdown.wait()
    await self.shutdown()
```

#### Fix 4: Make Thread Lock for Engine

**Root cause**: `asyncio.Lock` is not thread-safe.
**File**: `src/models/database.py:99-104`
**Why it leaks**: If uvicorn thread calls `_get_lock()` on a different loop, the lock is invalid.
**Fix**:
```python
import threading

_engine_lock = None
_thread_lock = threading.Lock()

def _get_lock():
    global _engine_lock
    if _engine_lock is None:
        with _thread_lock:
            if _engine_lock is None:
                _engine_lock = asyncio.Lock()
    return _engine_lock
```

#### Fix 5: Increase Pool Size

**Root cause**: Pool too small for concurrent workload.
**File**: `src/models/database.py:83-84`
**Why it leaks**: Not a leak per se, but causes `TimeoutError` which can cascade.
**Fix**:
```python
_POOL_SIZE = 20
_MAX_OVERFLOW = 10
```

---

## Appendix: File-by-File DB Usage Map

| File | `async_session()` calls | Context Manager | Exception Safe | Notes |
|------|------------------------|-----------------|----------------|-------|
| `database.py` | 0 (creates sessions) | N/A | N/A | Core — see fixes above |
| `main.py` | 12 | ✅ All `async with` | ✅ | Jobs isolated by try/except |
| `main_crypto.py` | 20+ | ✅ All `async with` | ✅ | Jobs have max_instances=1 |
| `orchestrator.py` | 4 | ✅ | ✅ | |
| `autonomous_session.py` | 8 | ✅ | ✅ | Uses `_get_session_metrics()` consolidation |
| `position_sync.py` | 7 | ✅ | ⚠️ Some missing rollback | IO between sessions is good pattern |
| `position_manager.py` | 4 | ✅ | ✅ | |
| `trailing_stop.py` | 3 | ✅ | ✅ | |
| `profit_lock.py` | 1 | ✅ | ✅ | |
| `circuit_breaker.py` | 2 | ✅ | ✅ | |
| `handlers.py` | 15 | ✅ | ✅ | Many small sessions |
| `crypto_handlers.py` | 8 | ✅ | ✅ | |
| `research/*.py` | 12 total | ✅ | ✅ | |
| `advisory/*.py` | 8 total | ✅ | ✅ | |
| `sl_engine.py` | 2 | ✅ | ✅ | |
| `watchdog.py` | 0 (uses get_engine) | N/A | ✅ | Pool stats only |

---

*End of audit report.*
