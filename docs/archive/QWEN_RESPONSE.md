This is an exceptionally sharp and accurate review. You successfully identified where the previous recommendations were over-engineered for the current stage of the project. Your counter-arguments are spot on: `@dataclass` is much lighter than Pydantic for internal DTOs, retry with jitter is the industry standard for exchange APIs, Prometheus is the correct tool since the infrastructure already exists, and a managed DB is indeed premature for paper trading.

Here is the exact implementation plan and code to close the remaining gaps based on your **Recommended Priority Fixes**.

---

### 🎯 Fix 1: Create Shared `@dataclass` Position Snapshot
This eliminates code duplication, fixes the `partial_exits_taken` bug, and provides strict typing without the overhead of Pydantic.

**1. Create the shared utility:**
```python
# src/utils/position_snapshot.py
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from typing import Optional

@dataclass(frozen=True) # frozen=True makes it immutable, preventing accidental modifications
class PositionSnapshot:
    id: int
    ticker: str
    side: str
    status: str
    size: Decimal
    entry_price: Decimal
    current_price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    trailing_stop_price: Optional[Decimal] = None
    highest_price: Optional[Decimal] = None
    leverage: Optional[int] = None
    regime_at_entry: Optional[str] = None
    signal_source: Optional[str] = None
    opened_at: Optional[datetime] = None
    partial_exits_taken: int = 0  # ✅ Default value prevents the crash!

def snapshot_from_db(db_pos) -> PositionSnapshot:
    """Factory function to safely map a SQLAlchemy DB model to a PositionSnapshot."""
    return PositionSnapshot(
        id=db_pos.id,
        ticker=db_pos.ticker,
        side=db_pos.side,
        status=db_pos.status,
        size=db_pos.size,
        entry_price=db_pos.entry_price,
        current_price=getattr(db_pos, 'current_price', None),
        stop_loss=getattr(db_pos, 'stop_loss', None),
        trailing_stop_price=getattr(db_pos, 'trailing_stop_price', None),
        highest_price=getattr(db_pos, 'highest_price', None),
        leverage=getattr(db_pos, 'leverage', None),
        regime_at_entry=getattr(db_pos, 'regime_at_entry', None),
        signal_source=getattr(db_pos, 'signal_source', None),
        opened_at=getattr(db_pos, 'opened_at', None),
        partial_exits_taken=getattr(db_pos, 'partial_exits_taken', 0),
    )
```

**2. Refactor `main.py` and `main_crypto.py`:**
```python
# In both src/main.py and src/main_crypto.py
from src.utils.position_snapshot import snapshot_from_db

# Replace the old _snapshot_position function entirely:
def _snapshot_position(db_pos):
    return snapshot_from_db(db_pos)
```

---

### 🎯 Fix 2: Bybit API Retry with Jitter (Replacing Per-Symbol Locks)
Instead of complex local locks, we handle 409 Conflicts (and 5xx server errors) using exponential backoff with jitter. This is safer and handles Bybit-side rate limits gracefully.

**1. Create the retry decorator:**
```python
# src/utils/api_retry.py
import asyncio
import random
import logging
from functools import wraps

logger = logging.getLogger(__name__)

# Define the exceptions that warrant a retry
RETRYABLE_EXCEPTIONS = (BybitConflictError, BybitServerError) # Adjust to your actual exception classes

def retry_with_jitter(max_retries=3, base_delay=0.5):
    """Decorator to retry async functions with exponential backoff and jitter."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except RETRYABLE_EXCEPTIONS as e:
                    if attempt == max_retries - 1:
                        logger.error(f"Bybit API failed after {max_retries} retries: {e}")
                        raise
                    
                    # Exponential backoff + random jitter
                    delay = (base_delay * (2 ** attempt)) + random.uniform(0, 0.5)
                    logger.warning(f"Bybit API Conflict/Server Error on {func.__name__}. Retrying in {delay:.2f}s... (Attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(delay)
        return wrapper
    return decorator
```

**2. Apply it to your Bybit Client:**
```python
# src/data/bybit_client.py
from src.utils.api_retry import retry_with_jitter

class BybitClient:
    # ... existing code ...

    @retry_with_jitter(max_retries=3, base_delay=0.5)
    async def amend_order(self, symbol: str, order_id: str, new_price: Decimal):
        # Your existing API call logic here
        response = await self.session.put(...)
        if response.status == 409:
            raise BybitConflictError(f"409 Conflict amending {symbol}")
        # ... handle response ...
```

---

### 🎯 Fix 3: Prometheus "Dead Man's Switch" Alerting
Since `karsa-prometheus` and `karsa-alertmanager` are already running, we just need to configure the rules to act as a Dead Man's Switch. 

**1. Add the Alert Rule to Prometheus (`karsa_alerts.yml`):**
```yaml
groups:
  - name: karsa_bot_health
    rules:
      # Dead Man's Switch: Fires if orchestrator is down for 5 minutes
      - alert: KarsaOrchestratorDown
        expr: up{job="karsa-crypto-orchestrator"} == 0
        for: 5m
        labels:
          severity: critical
          component: orchestrator
        annotations:
          summary: "🚨 KARSA ORCHESTRATOR IS DOWN"
          description: "The crypto orchestrator has been unreachable for 5 minutes. Open positions are currently unmanaged."
```

**2. Configure Alertmanager to send to Telegram (`alertmanager.yml`):**
```yaml
route:
  receiver: 'telegram-critical'
  group_by: ['alertname']
  group_wait: 10s
  group_interval: 10m
  repeat_interval: 1h

receivers:
  - name: 'telegram-critical'
    telegram_configs:
      - bot_token: 'YOUR_BOT_TOKEN'
        api_url: api.telegram.org
        chat_id: YOUR_CHAT_ID
        parse_mode: 'HTML'
        # The message that gets sent to your phone
        message: |
          🚨 <b>KARSA KILL SWITCH ALERT</b> 🚨
          
          <b>Component:</b> Crypto Orchestrator
          <b>Status:</b> DOWN for > 5 minutes
          
          <i>Positions may be unmanaged. Check Bybit immediately!</i>
```
*Note: If you eventually want this to automatically close positions, you can add a `webhook_configs` receiver in Alertmanager that hits a lightweight AWS Lambda or a secondary "Kill Switch" Python script to cancel all open orders via the Bybit API.*

---

### 🎯 Fix 4: Defer Managed DB (Acknowledged)
**Decision:** Keep self-hosted Postgres in Docker.
**Action:** Ensure your `docker-compose.yml` includes the safety nets discussed earlier:
```yaml
services:
  karsa-postgres:
    image: postgres:15
    # ... env vars ...
    stop_grace_period: 60s # Prevents improper shutdown crashes
    restart: unless-stopped
    volumes:
      - pgdata:/var/lib/postgresql/data
```

### 🏁 Final Architecture State
With these changes applied, your codebase transitions from a fragile state to a robust, production-ready architecture for paper trading:
1. **State Management:** Unified, strictly-typed `@dataclass` snapshots eliminate attribute errors.
2. **API Resilience:** Exponential backoff handles Bybit rate limits and 409s without crashing or deadlocking.
3. **Infrastructure Safety:** Prometheus acts as an external watchdog, alerting you instantly if the bot dies, protecting unmanaged positions.
4. **Cost Efficiency:** You avoid unnecessary managed DB costs while keeping the data safe via Docker grace periods.