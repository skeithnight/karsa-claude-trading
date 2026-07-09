This log reveals two distinct **fatal configuration errors** that the bot is incorrectly treating as **transient network issues**. 

Here is the deep-dive analysis of the errors, how to fix the underlying code, and how to correct the bot's flawed retry logic.

---

### 🔍 1. Root Cause Analysis

#### 🔴 Error 1: `BybitClient.__init__() missing 1 required position`
*   **What it means:** This is a Python `TypeError`. The `BybitClient` class constructor expects a specific number of positional arguments (e.g., `api_key`, `api_secret`, `testnet`), but the code is only passing `N-1` arguments.
*   **Likely Culprits:**
    1.  **Missing API Secret:** The code is passing the API Key but failing to pass the API Secret (perhaps an environment variable failed to load).
    2.  **Missing `testnet` flag:** If using the official `pybit` library (v5+), it strictly requires a boolean for `testnet` (e.g., `testnet=False`).
    3.  **Custom Wrapper Bug:** If you have a custom `BybitClient` wrapper, the `__init__` signature was updated, but the instantiation call wasn't.

#### 🔴 Error 2: `9Router/LLM: status 401`
*   **What it means:** HTTP `401 Unauthorized`. The request to your LLM provider (via the 9Router) was rejected because the credentials are invalid.
*   **Likely Culprits:**
    1.  The LLM API key is expired, revoked, or typed incorrectly in the `.env` file.
    2.  The LLM provider account has run out of credits/quota.
    3.  The API key is valid, but lacks permissions for the specific model/endpoint being requested.

#### ⏳ Error 3: `Will retry in 60s...` (The Architectural Flaw)
*   **What it means:** The bot's connection manager caught the exceptions and scheduled a retry.
*   **Why this is dangerous:** **You cannot retry a configuration error.** 
    *   Retrying a missing Python argument will just throw the exact same `TypeError` 60 seconds later.
    *   Retrying a `401 Unauthorized` will just keep hitting the LLM API with a bad key, potentially getting your IP banned or wasting resources.

---

### 🛠️ 2. Immediate Code Fixes

#### Fix 1: Resolve the Bybit Client Initialization
Locate where the Bybit client is instantiated (likely in `src/exchange/bybit_client.py` or `src/bot/crypto_main.py`).

**Check your `.env` file:**
```env
BYBIT_API_KEY=your_actual_key
BYBIT_API_SECRET=your_actual_secret
```

**Check the instantiation code:**
```python
# ❌ WRONG (Missing arguments)
client = BybitClient(api_key=os.getenv("BYBIT_API_KEY")) 

# ✅ CORRECT (Assuming pybit v5)
from pybit.unified_trading import HTTP
client = HTTP(
    testnet=False, # <--- This is likely the missing positional argument!
    api_key=os.getenv("BYBIT_API_KEY"),
    api_secret=os.getenv("BYBIT_API_SECRET")
)
```

#### Fix 2: Resolve the LLM 401 Error
1.  Log into your LLM provider dashboard (or 9Router dashboard).
2.  Verify the account has active credits.
3.  Regenerate the API key and update it in your `.env` file.
4.  Ensure the key is being loaded correctly in the code:
    ```python
    llm_key = os.getenv("LLM_API_KEY")
    if not llm_key:
        raise ValueError("LLM_API_KEY is missing from environment variables!")
    ```

---

### 🏗️ 3. Architectural Fix: Smart Retry Logic (Crucial)

You must separate **Transient Errors** (network blips, exchange downtime) from **Fatal Errors** (bad code, bad credentials). 

Update your connection manager / retry decorator to handle this:

```python
# src/utils/connection_manager.py

import asyncio
import logging
from pybit.exceptions import InvalidRequestError, FailedRequestError

# Define which errors should NEVER be retried
FATAL_ERRORS = (
    TypeError,           # Code bugs (like missing arguments)
    ValueError,          # Bad configuration
    # HTTP 401/403 equivalents in your LLM/Exchange libraries
)

async def smart_retry(func, *args, max_retries=5, **kwargs):
    retries = 0
    while retries < max_retries:
        try:
            return await func(*args, **kwargs)
            
        except FATAL_ERRORS as e:
            # 🛑 FATAL: Do not retry. Halt and alert.
            logging.critical(f"Fatal configuration error: {e}. Halting retries.")
            await send_telegram_alert(f"🛑 FATAL ERROR: {e}\nBot requires manual restart.")
            raise  # Re-raise to crash the specific task safely
            
        except (ConnectionError, TimeoutError, FailedRequestError) as e:
            # 🔄 TRANSIENT: Network or Exchange issue. Retry.
            retries += 1
            wait_time = 2 ** retries  # Exponential backoff (2s, 4s, 8s...)
            logging.warning(f"Transient error: {e}. Retrying in {wait_time}s...")
            await asyncio.sleep(wait_time)
            
        except Exception as e:
            # Catch-all for unexpected errors
            logging.error(f"Unexpected error: {e}")
            raise
            
    raise Exception(f"Max retries ({max_retries}) exceeded for {func.__name__}")
```

---

### 🎨 4. Telegram UX Update for Connection Alerts

Instead of a generic "Will retry in 60s" message, the bot should tell the user *exactly* what is wrong and whether it's fixing itself or needs human intervention.

**Update the Alert Formatter (`src/utils/formatters.py`):**

**Current (Bad) Output:**
```text
🚨 Connection Alert — 06:53
🔴 Bybit API: BybitClient.__init__() missing 1 required position
🔴 9Router/LLM: status 401
⏳ Will retry in 60s...
```

**New (Good) Output:**
```text
🚨 SYSTEM ALERT — 06:53
━━━━━━━━━━━━━━━━━━━━━━━
🛑 FATAL: Configuration Errors Detected

1️⃣ Bybit API: Missing API Secret or Testnet flag.
2️⃣ LLM Router: 401 Unauthorized (Invalid API Key or No Credits).

⚠️ Action Required:
These are not network issues. The bot cannot auto-recover.
Please update your .env file and restart the bot.
```

### 📋 Summary Checklist for the Dev Agent
1. [ ] **Fix Bybit Init:** Add the missing argument (likely `testnet=False` or `api_secret`) to the `BybitClient` instantiation.
2. [ ] **Fix LLM Key:** Verify and update the LLM API key in the `.env` file.
3. [ ] **Refactor Retries:** Implement the `smart_retry` logic to distinguish between `FATAL_ERRORS` (no retry) and `TRANSIENT_ERRORS` (retry with backoff).
4. [ ] **Update Alerts:** Change the Telegram alert formatter to clearly state when an error requires manual intervention vs. when the bot is auto-recovering.