# Zombie Process & Hanging Connection Analysis

## Root Cause Chain

```
WARP proxy (socks5h://warp:1080) slow/stuck
  → pybit.HTTP (requests lib) blocks on SOCKS connection
    → asyncio.to_thread() has NO timeout — thread hangs forever
      → Event loop task never returns
        → FastAPI HTTP server can't process new requests
          → curl healthcheck hangs (10s timeout)
            → Docker spawns new healthcheck every 30s
              → 67 zombie curl processes accumulate
                → Container marked "unhealthy"
                  → Telegram bot not responding
```

## Specific Blocking Points

### 1. `_connection_health_loop` — New BybitClient every 60s (CRITICAL)
**File**: `src/bot/crypto_main.py:131-135`
```python
from src.data.bybit_client import BybitClient
bybit = BybitClient(cache)          # NEW connection pool every 60s
await bybit._throttle()
resp = await _aio.to_thread(bybit._http_client.get_server_time)  # NO TIMEOUT
```

**Problems**:
- Creates a new `pybit.HTTP` client every 60 seconds
- Each creates a new SOCKS5 connection through WARP
- `BybitClient.close()` is a no-op — connections never cleaned up
- `to_thread()` has no timeout — if WARP hangs, thread hangs forever
- Accumulates connection pools over time

### 2. `_make_asm` — New BybitClient per API call (CRITICAL)
**File**: `src/api/crypto_control.py:22-30`
```python
def _make_asm(request: Request):
    orch, redis_client = _get_app_state(request)
    from src.data.bybit_client import BybitClient
    bybit = BybitClient(cache)  # NEW per API request
    return AutonomousSessionManager(orch, redis_client, bybit)
```

**Problem**: Every `/api/v1/crypto/*` call creates a new BybitClient with new connection pool.

### 3. `pybit.HTTP` — Synchronous requests through SOCKS proxy
**File**: `src/data/bybit_client.py:62-81`
```python
self._http_client = HTTP(
    testnet=self._testnet,
    api_key=settings.BYBIT_API_KEY,
    api_secret=settings.BYBIT_API_SECRET,
)
# Proxy: socks5h://warp:1080
self._http_client.client.proxies = {"https": proxy, "http": proxy}
self._http_client.client.verify = False
```

**Problem**: `pybit.HTTP` uses `requests` library (sync). When routed through SOCKS5:
- Each call opens a TCP connection to WARP container
- WARP routes to api.bybit.com
- If WARP is overloaded/stuck, the `requests` call blocks indefinitely
- `asyncio.to_thread()` doesn't add timeout — thread hangs

### 4. Docker Healthcheck — Zombie accumulation
**File**: `docker-compose.yml`
```yaml
healthcheck:
  test: ["CMD-SHELL", "curl -f http://localhost:8444/health || exit 1"]
  interval: 30s
  timeout: 10s
  retries: 3
```

**Problem**: When HTTP server is stuck:
- curl hangs for 10s (timeout), then exits
- Docker doesn't kill the curl process — it becomes a zombie
- Next healthcheck spawns another curl — also hangs
- 67 zombies accumulated in 30 minutes

### 5. `_get_bybit` — Lazy init but shared
**File**: `src/data/mcp_client.py:74-79`
```python
def _get_bybit(self):
    if self._bybit is None:
        self._bybit = BybitClient(self.cache)
    return self._bybit
```

**Status**: OK — reuses single instance. But the health loop doesn't use this path.

## Connection Leak Summary

| Source                    | Frequency   | Connection Pool | Cleanup     |
| ---------------------------| -------------| -----------------| -------------|
| `_connection_health_loop` | Every 60s   | NEW pool        | NEVER       |
| `_make_asm` (API)         | Per request | NEW pool        | NEVER       |
| `mcp._get_bybit()`        | Once        | Single instance | On shutdown |
| Orchestrator              | Once        | Single instance | On shutdown |

## Fix Plan

### Fix 1: Reuse BybitClient in health loop
```python
# In _connection_health_loop, use the shared instance:
bybit = orch.mcp._get_bybit()  # Reuse existing client
resp = await asyncio.wait_for(
    asyncio.to_thread(bybit._http_client.get_server_time),
    timeout=5.0
)
```

### Fix 2: Add timeout to all to_thread calls
```python
resp = await asyncio.wait_for(
    asyncio.to_thread(bybit._http_client.get_server_time),
    timeout=5.0
)
```

### Fix 3: Reuse BybitClient in _make_asm
```python
def _make_asm(request: Request):
    orch, redis_client = _get_app_state(request)
    bybit = orch.mcp._get_bybit()  # Reuse existing client
    return AutonomousSessionManager(orch, redis_client, bybit)
```

### Fix 4: Add timeout to Docker healthcheck
```yaml
healthcheck:
  test: ["CMD-SHELL", "curl -f --max-time 5 http://localhost:8444/health || exit 1"]
  interval: 30s
  timeout: 10s
  retries: 3
```

### Fix 5: Watchdog monitors event loop lag
Already implemented — sentinel task detects when event loop is starved.
