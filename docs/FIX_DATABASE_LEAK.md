### 🕵️ Root Cause 1: The "Thundering Herd" (Job Pile-ups)
In `src/main_crypto.py`, several critical jobs are scheduled to run every few minutes, but they are **missing `max_instances=1, coalesce=True`**.

**The Culprit Jobs:**
*   `_job_sync_crypto_positions` (Every 5 mins)
*   `_job_oms_cleanup` (Every 2 mins)
*   `_job_kill_switch` (Every 5 mins)
*   `_job_metrics_sync` (Every 60 seconds)

**Why this exhausts the pool:**
During periods of high market volatility, API calls to Bybit or slow DB responses can cause these jobs to take slightly longer than their scheduled interval (e.g., a 5-minute job takes 6 minutes). Because `max_instances` is not set, APScheduler spawns a **second instance** of the job while the first is still running. 
Each instance opens a database connection via `async_session()`. Within an hour, you can have 10 overlapping instances of the same job running concurrently, each holding onto a DB connection, easily exhausting the default pool size of 15 (10 + 5 overflow).

### 🕵️ Root Cause 2: The "Deadlock Trap" in the Safety Net
The codebase attempts to recover from connection leaks using a background task called `_pool_recycle_loop` in `src/models/database.py`. However, it contains two fatal logic errors that make it completely useless during a leak:

1.  **Incorrect Postgres Query:** It queries for `state = 'idle in transaction'`. But SQLAlchemy's `async with` blocks commit or rollback immediately, meaning leaked connections end up in the `'idle'` state, not `'idle in transaction'`. The query returns 0, so the safety net never triggers.
2.  **The Disposal Deadlock:** The code only disposes the engine if `pool.checkedout() == 0`. But when the pool is exhausted by overlapping jobs, connections are *actively checked out*. Because `checkedout() > 0`, the recycle loop logs `pool_recycle_deferred` and **refuses to dispose the engine**, creating an infinite deadlock where the bot is permanently starved of connections.

### 🕵️ Root Cause 3: The Silent Failure of the Monkey-Patch
The author attempted to fix a known `asyncpg` bug (where connections fail to close gracefully) by monkey-patching the `terminate` method. 
However, the code imports a **non-existent class** (`sqlalchemy.connectors.asyncio.AsyncAdapt_terminate`). The import throws an `ImportError`, which is silently swallowed by a broad `except Exception` block. The patch is never applied, meaning the underlying `asyncpg` bug remains active, leaking connections every time a pool recycle occurs.

---

### 🛠️ The Fix: Apply These Changes Immediately

To stabilize your bot and allow it to trade continuously without crashing, apply these three fixes.

#### 1. Fix the Scheduler in `src/main_crypto.py`
Locate the `_register_jobs` method (around line 286) and replace the entire block with this updated version. This adds `max_instances=1, coalesce=True` to **every** job to prevent overlapping instances from stealing connections.

```python
    def _register_jobs(self):
        """Register only crypto-related APScheduler jobs."""
        s = self.scheduler

        if not settings.BYBIT_API_KEY:
            logger.warning("bybit_api_key_missing_no_crypto_jobs")
            return

        # Core crypto (24/7)
        s.add_job(self._job_scan_crypto, "cron", minute="*/15",
                  id="scan_crypto", name="Crypto Market Scan (24/7)", replace_existing=True, misfire_grace_time=600, max_instances=1, coalesce=True)
        s.add_job(self._job_refresh_universe, "cron", minute="*/15",
                  id="refresh_universe", name="Crypto Universe Refresh (every 15m)", replace_existing=True, misfire_grace_time=600, max_instances=1, coalesce=True)
        s.add_job(self._job_monitor_crypto_positions, "cron", minute="*/15",
                  id="crypto_monitor", name="Crypto Position Monitor", replace_existing=True, misfire_grace_time=120, max_instances=1, coalesce=True)
        s.add_job(self._job_sync_crypto_funding, "cron", hour="0,8,16", minute=5,
                  id="crypto_funding", name="Crypto Funding Rate Sync", replace_existing=True, misfire_grace_time=300, max_instances=1, coalesce=True)
        s.add_job(self._job_crypto_pnl_snapshot, "cron", hour=0, minute=0,
                  id="crypto_pnl_snapshot", name="Crypto Daily PnL Snapshot", replace_existing=True, misfire_grace_time=600, max_instances=1, coalesce=True)
        s.add_job(self._job_sync_crypto_positions, "cron", minute="*/5",
                  id="crypto_position_sync", name="Crypto Position Sync", replace_existing=True, misfire_grace_time=120, max_instances=1, coalesce=True)

        # Lifecycle management
        s.add_job(self._job_update_trailing_stops, "cron", minute="*/5",
                  id="crypto_trailing_stops", name="Crypto Trailing Stop Update", replace_existing=True, misfire_grace_time=120, max_instances=1, coalesce=True)
        s.add_job(self._job_verify_sl, "interval", minutes=5,
                  id="sl_verification", name="SL Verification & Recovery", replace_existing=True, misfire_grace_time=120, max_instances=1, coalesce=True)
        s.add_job(self._job_check_partial_exits, "cron", minute="*/2",
                  id="crypto_partial_exits", name="Crypto Partial Exit Check", replace_existing=True, misfire_grace_time=60, max_instances=1, coalesce=True)
        s.add_job(self._job_check_time_exits, "cron", hour="*", minute=30,
                  id="crypto_time_exits", name="Crypto Time-Based Exit Check", replace_existing=True, misfire_grace_time=300, max_instances=1, coalesce=True)
        s.add_job(self._job_check_performance_gate, "cron", minute="*/5",
                  id="crypto_perf_gate", name="Performance Gate + AI Judge", replace_existing=True, misfire_grace_time=120, max_instances=1, coalesce=True)
        s.add_job(self._job_check_circuit_breakers, "cron", minute="*/1",
                  id="crypto_circuit_breakers", name="Crypto Circuit Breaker Check", replace_existing=True, misfire_grace_time=60, max_instances=1, coalesce=True)
        s.add_job(self._job_enforce_funding_limit, "cron", hour="*", minute=20,
                  id="crypto_funding_limit", name="Crypto Funding Limit Enforcement", replace_existing=True, misfire_grace_time=300, max_instances=1, coalesce=True)
        s.add_job(self._job_reconcile_positions, "interval", seconds=60,
                  id="crypto_reconciliation", name="Crypto Position Reconciliation", replace_existing=True, misfire_grace_time=30, max_instances=1, coalesce=True)
        s.add_job(self._job_liquidity_check, "cron", minute="*/15",
                  id="crypto_liquidity", name="Crypto Liquidity Check", replace_existing=True, misfire_grace_time=120, max_instances=1, coalesce=True)
        s.add_job(self._job_oms_cleanup, "interval", minutes=2,
                  id="oms_cleanup", name="OMS Stuck Order Cleanup", replace_existing=True, misfire_grace_time=60, max_instances=1, coalesce=True)
        s.add_job(self._job_kill_switch, "cron", minute="*/5",
                  id="kill_switch", name="Crypto Kill Switch", replace_existing=True, misfire_grace_time=60, max_instances=1, coalesce=True)
        s.add_job(self._job_metrics_sync, "interval", seconds=60,
                  id="crypto_metrics_sync", name="Crypto Metrics Sync", replace_existing=True, misfire_grace_time=30, max_instances=1, coalesce=True)

        # AODE Research Jobs
        s.add_job(self._job_aode_discovery, "cron", hour="*/1",
                  id="aode_discovery", name="AODE Token Discovery", replace_existing=True, misfire_grace_time=300, max_instances=1, coalesce=True)
        s.add_job(self._job_aode_research, "cron", hour="*/4",
                  id="aode_research", name="AODE Research Scoring", replace_existing=True, misfire_grace_time=600, max_instances=1, coalesce=True)
        s.add_job(self._job_aode_monitoring, "cron", minute="*/30",
                  id="aode_monitoring", name="AODE Monitoring Cycle", replace_existing=True, misfire_grace_time=120, max_instances=1, coalesce=True)

        logger.info("crypto_jobs_registered", count=len(self.scheduler.get_jobs()))
```

#### 2. Fix the Deadlock Trap in `src/models/database.py`
Locate the `_pool_recycle_loop` function and replace it with this logic. This fixes the Postgres query to look for `'idle'` connections and removes the deadlock trap that prevents the engine from disposing when connections are leaked.

```python
async def _pool_recycle_loop():
    """Periodically check pool health and force recycle if connections leak."""
    global _session_factory
    while True:
        await asyncio.sleep(120)
        try:
            engine = get_engine()
            
            # Check actual Postgres connection count (looking for 'idle' connections, not just 'idle in transaction')
            try:
                async with engine.connect() as conn:
                    result = await conn.execute(
                        text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND state IN ('idle', 'idle in transaction')")
                    )
                    idle_conns = result.scalar() or 0
                    
                    # If there are way more idle connections than our pool size, we have a leak
                    if idle_conns > 25: 
                        logger.warning("pg_idle_connections_high count=%d — forcing pool dispose", idle_conns)
                        # Force dispose regardless of checkedout status to break the deadlock
                        await engine.dispose()
                        _session_factory = None
            except Exception:
                pass # Postgres might be unreachable
        except Exception as e:
            logger.debug("pool_recycle_error error=%s", str(e))
```

#### 3. Fix the Silent Monkey-Patch Failure in `src/models/database.py`
Locate the `_patch_asyncpg_terminate` function and replace it. This uses the correct SQLAlchemy import path to ensure the patch actually applies and stops the underlying `asyncpg` socket leaks.

```python
def _patch_asyncpg_terminate():
    """Monkey-patch asyncpg connection terminate() to skip the broken graceful close."""
    try:
        # Correct import path for SQLAlchemy 2.0+ asyncpg dialect
        from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_connection

        def _fixed_terminate(self):
            # Force close the underlying asyncpg connection to avoid asyncio.shield() bugs
            if self._connection:
                self._connection.terminate()
            self._started = False

        AsyncAdapt_asyncpg_connection.terminate = _fixed_terminate
        logger.info("asyncpg_terminate_patched")
    except Exception as e:
        logger.warning("asyncpg_terminate_patch_failed error=%s", str(e))
```
