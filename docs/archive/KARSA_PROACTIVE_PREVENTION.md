# 🛡️ From Reactive Fixes to Proactive Prevention: Karsa Trading Bot Architecture Audit

In algorithmic trading, **reactive fixes** stop the bleeding, but **preventive architecture** ensures the system survives market volatility. Below is the strategic analysis transitioning your recent fixes into long-term, systemic prevention protocols.

---

## 1. Code Architecture & State Management
**The Incident:** The `partial_exits_taken` attribute was missing in `src/main.py` but present in `src/main_crypto.py`, causing a runtime crash in the trailing stop manager.

### 🔄 The Solving Idea (Reactive)
*   **Action:** Manually add `partial_exits_taken=db_pos.partial_exits_taken` to the `_snapshot_position` function in `main.py`.
*   **Limitation:** This relies on human memory. If a new field is added to the database schema later, the developer might update one file and forget the other, recreating the exact same crash.

### 🛡️ Prevention Strategy (Proactive)
1.  **Enforce DRY (Don't Repeat Yourself):**
    *   Never duplicate the `_snapshot_position` logic. Move this function to a shared utility module (e.g., `src/utils/position_snapshot.py`) and import it into both orchestrators.
2.  **Replace `SimpleNamespace` with Strict Typing:**
    *   `SimpleNamespace` hides missing attributes until runtime. Refactor to use **Pydantic Models** or **Python `dataclasses`**.
    ```python
    # Prevention: Strict Schema Validation
    from pydantic import BaseModel

    class PositionSnapshot(BaseModel):
        id: int
        ticker: str
        partial_exits_taken: int # If the DB lacks this, Pydantic throws an error on startup, not during a live trade.
    ```
3.  **Static Type Checking in CI/CD:**
    *   Integrate `mypy` or `pyright` into your GitHub Actions/GitLab CI pipeline. A missing attribute will fail the build *before* the code is ever deployed to the live server.

---

## 2. External API Resilience & Concurrency
**The Incident:** Bybit returned a `409 Conflict` (likely due to concurrent order modifications), followed by a JSON decoding crash because the API returned HTML/text instead of JSON.

### 🔄 The Solving Idea (Reactive)
*   **Action:** Wrap `.json()` calls in `try/except` blocks and check `response.status == 200` before parsing. Add basic retry logic.
*   **Limitation:** This prevents the crash, but it doesn't stop the bot from spamming the API with conflicting requests, which can lead to Bybit temporarily banning your API keys.

### 🛡️ Prevention Strategy (Proactive)
1.  **Per-Symbol Asynchronous Locks:**
    *   A 409 Conflict happens when two threads try to modify the same order simultaneously. Implement an `asyncio.Lock` dictionary mapped to each ticker.
    ```python
    # Prevention: Only one order modification per symbol at a time
    symbol_locks = {symbol: asyncio.Lock() for symbol in UNIVERSE}

    async def modify_order(symbol, new_price):
        async with symbol_locks[symbol]:
            await bybit_client.send_modify_request(symbol, new_price)
    ```
2.  **Centralized, Hardened API Client:**
    *   Never use raw `aiohttp` or `requests` in your business logic. Build a `BybitClient` wrapper that inherently handles:
        *   **Safe Parsing:** Automatically checks `Content-Type: application/json` before parsing.
        *   **Exponential Backoff:** Retries on 429 (Rate Limit) and 5xx errors, but *aborts* immediately on 400-level logic errors (like 409 or 401).
3.  **The Circuit Breaker Pattern:**
    *   If the bot receives three consecutive API errors for a specific symbol, the Circuit Breaker "trips." It pauses trading for that symbol for 5 minutes and alerts you via Telegram, preventing a cascade of failed orders.

---

## 3. Infrastructure & Database Integrity
**The Incident:** Postgres performed crash recovery due to an improper shutdown. Additionally, previous audits revealed database connection pool leaks (Thundering Herd & Deadlocks).

### 🔄 The Solving Idea (Reactive)
*   **Action:** Increase Docker `stop_grace_period` to 60s, fix the `_pool_recycle_loop` to catch `'idle'` connections, and correct the `asyncpg` monkey-patch.
*   **Limitation:** Self-hosted Docker Postgres is inherently fragile for financial systems. If the host machine runs out of RAM (OOM Kill), the grace period is ignored, and the DB crashes anyway.

### 🛡️ Prevention Strategy (Proactive)
1.  **Strict Context Managers for DB Sessions:**
    *   Ensure 100% of database interactions use `async with`. Never pass a raw session object across different functions.
    ```python
    # Prevention: Guaranteed connection return
    async with get_async_session() as session:
        result = await session.execute(query)
        # Connection is automatically returned to the pool here, even if an exception occurs.
    ```
2.  **Connection Pool Telemetry (Grafana/Prometheus):**
    *   Don't wait for the bot to crash to know the pool is leaking. Export SQLAlchemy pool metrics (`pool.size()`, `pool.checkedin()`, `pool.checkedout()`) to Prometheus. Set a Grafana alert to page you if `checkedout` connections stay above 80% for more than 2 minutes.
3.  **Infrastructure Upgrade (Managed Database):**
    *   For a live trading bot, **do not self-host Postgres in Docker**. Move to a managed service (AWS RDS, Supabase, or Neon). Managed databases handle automated backups, point-in-time recovery, and connection pooling (via PgBouncer) natively, eliminating crash recovery and leak issues entirely.

---

## 4. The "Unmanaged Position" Risk (Trading Specific)
**The Incident:** When the orchestrator crashes (due to the snapshot bug or DB leak), open positions are left completely unmanaged. If the market crashes while the bot is offline, losses are unlimited.

### 🛡️ Prevention Strategy (Proactive)
1.  **External Watchdog / Dead Man's Switch:**
    *   The bot should send a "heartbeat" to an external service (like Healthchecks.io or a simple AWS Lambda function) every 5 minutes.
    *   If the heartbeat stops for 10 minutes, the external service automatically triggers a **Kill Switch** (via a separate, lightweight script) that logs into Bybit and closes all open positions or cancels all open orders.
2.  **Hardcoded Exchange-Level Take-Profit/Stop-Loss:**
    *   Never rely solely on the bot's software trailing stop. When opening a position, *always* attach a hard Stop-Loss and Take-Profit directly to the Bybit order payload. If the bot dies, the exchange will still protect your capital.