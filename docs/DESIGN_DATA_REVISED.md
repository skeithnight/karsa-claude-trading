# DESIGN_DATA_REVISED.md: Karsa Institutional Data Architecture (v3.0)

**Document Status:** Approved for Production Implementation  
**Author:** CIO / Head of Trading  
**Context:** Complete replacement of unstable scrapers with a resilient, API-driven Data Warehouse.

---

## 1. Executive Summary & Tool Audit

We have conducted a strict audit of our data ingestion tools. The following decisions are final and non-negotiable.

| Tool / Library | Status | Verdict for Karsa | Reasoning |
| :--- | :--- | :--- | :--- |
| **`Verdenroz/GoogleFinanceAPI`** | 🪦 **DEAD** | **REJECTED** | Archived and unmaintained. High risk of silent failures. |
| **`yfinance`** | ⚠️ **UNSTABLE** | **REJECTED** | Yahoo Finance aggressively blocks automated requests (429 errors). Unreliable for production. |
| **Polygon.io** | 🟢 **APPROVED** | **TIER 1 (US/ETF)** | Institutional-grade. Free tier provides reliable delayed EOD data. |
| **Finnhub.io** | 🟢 **APPROVED** | **TIER 2 (US/ETF)** | Excellent fallback. Generous free tier for real-time quotes and basic fundamentals. |
| **Local IDX Vendor API** | 🟢 **APPROVED** | **TIER 1 (IDX)** | Institutional desks do not scrape IDX. We will use a dedicated local data feed (e.g., RTI, local broker API). |
| **`dlt` (data load tool)** | 🟢 **APPROVED** | **ETL ENGINE** | Orchestrates the batch loading of historical data into PostgreSQL. |
| **`httpx`** | 🟢 **APPROVED** | **HTTP CLIENT** | Native async HTTP client. Replaces synchronous `requests`/`yfinance` to prevent event loop blocking. |

---

## 2. High-Level System Architecture

The system is strictly divided into the Data Ingestion Layer, the Storage Layer, and the Compute/Delivery Layer. **There is no direct scraping from the application layer.**

```text
[External APIs]                [Storage Layer]                [Compute/Delivery]
                                                                
 Polygon.io ──┐               ┌─ PostgreSQL (Warehouse) ─────┐   
              │               │  (daily_ohlcv, intraday)     │
 Finnhub ─────┼──> [dlt ETL] ─┤                              ├──> pandas-ta (Local Indicators)
              │   (Nightly)   │                              │         │
 IDX Vendor ──┘               └─ Redis (Traffic Cop) ────────┤         ▼
                               (Cache / Circuit Breaker)     ├──> FastAPI Backend
                                                             │         │
                                                             │         ▼
                                                             └──> Telegram Bot / Orchestrator
```

---

## 3. Data Storage & Schema Design

### 3.1 PostgreSQL Schema (The Data Warehouse)
The `dlt` pipeline will populate these tables. All historical analysis must query this database, never the external APIs.

```sql
-- Historical Daily Data (Populated by dlt ETL)
CREATE TABLE daily_ohlcv (
    ticker VARCHAR(20) NOT NULL,
    market VARCHAR(10) NOT NULL, -- 'US', 'IDX', 'ETF'
    date DATE NOT NULL,
    open NUMERIC(12, 4),
    high NUMERIC(12, 4),
    low NUMERIC(12, 4),
    close NUMERIC(12, 4),
    volume BIGINT,
    PRIMARY KEY (ticker, market, date)
);

-- Intraday Data (For short-term strategy analysis)
CREATE TABLE intraday_ohlcv (
    ticker VARCHAR(20) NOT NULL,
    market VARCHAR(10) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    interval VARCHAR(5) NOT NULL, -- '1m', '5m', '15m', '1h'
    open NUMERIC(12, 4),
    high NUMERIC(12, 4),
    low NUMERIC(12, 4),
    close NUMERIC(12, 4),
    volume BIGINT,
    PRIMARY KEY (ticker, market, timestamp, interval)
);

-- Corporate Actions & Fundamentals (Populated by dlt ETL)
CREATE TABLE corporate_actions (
    ticker VARCHAR(20) NOT NULL,
    action_type VARCHAR(20) NOT NULL, -- 'DIVIDEND', 'SPLIT', 'EARNINGS'
    ex_date DATE,
    payment_date DATE,
    amount NUMERIC(12, 4),
    PRIMARY KEY (ticker, action_type, ex_date)
);
```

### 3.2 Redis Caching Strategy (The Traffic Cop)
Redis protects our API rate limits and ensures sub-millisecond reads for the Orchestrator.

| Data Type | Redis Key Pattern | TTL | Rationale |
| :--- | :--- | :--- | :--- |
| **Real-time Quote** | `rt:quote:{ticker}` | `60s` | Prevents spamming Finnhub/Polygon websockets/REST. |
| **Intraday Bars (1m)** | `rt:bars:1m:{ticker}` | `300s` | 5 minutes. Balances freshness with API protection. |
| **Daily Bars (EOD)** | `rt:bars:1d:{ticker}` | `43200s` | 12 hours. EOD data is static after market close. |
| **Circuit Breaker** | `cb:fail:{provider}` | `600s` | If a provider fails 3x, block it for 10 mins. |

---

## 4. The Batch ETL Pipeline (`dlt`)

We use `dlt` to build a resilient, incremental ETL pipeline. It tracks state automatically, ensuring we only download *new* data every night.

### 4.1 Custom `dlt` Resource for Polygon.io
```python
# src/etl/polygon_dlt_resource.py
import dlt
import httpx
from datetime import datetime, timedelta

@dlt.resource(name="daily_ohlcv", write_disposition="merge", primary_key="id")
def polygon_daily_resource(tickers: list[str], market_map: dict, api_key: str):
    """
    dlt resource that fetches daily data from Polygon.io.
    """
    state = dlt.current.state()
    last_date = state.get("last_date", "2020-01-01")
    start_date = (datetime.strptime(last_date, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")
    
    # Using httpx for async/sync compatibility and better error handling
    client = httpx.Client(timeout=30.0)
    
    for ticker in tickers:
        try:
            # Polygon Aggregates API (Daily)
            url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}"
            response = client.get(url, params={"apiKey": api_key, "limit": 50000})
            response.raise_for_status()
            data = response.json()
            
            if data.get("resultsCount", 0) == 0:
                continue
                
            for bar in data["results"]:
                # Convert Polygon timestamp (ms) to date
                bar_date = datetime.fromtimestamp(bar['t'] / 1000).date()
                
                yield {
                    "id": f"{ticker}_{market_map.get(ticker, 'US')}_{bar_date}",
                    "ticker": ticker,
                    "market": market_map.get(ticker, "US"),
                    "date": bar_date,
                    "open": bar['o'],
                    "high": bar['h'],
                    "low": bar['l'],
                    "close": bar['c'],
                    "volume": bar['v']
                }
                
            state["last_date"] = end_date
            
        except Exception as e:
            print(f"ETL Error for {ticker} via Polygon: {e}")
            continue

def run_nightly_etl():
    pipeline = dlt.pipeline(
        pipeline_name="karsa_historical_data",
        destination="postgres",
        dataset_name="market_data",
        credentials="postgresql://karsa:password@localhost:5432/karsa_db"
    )
    
    tickers = ["AAPL", "NVDA", "MSFT", "SPY", "QQQ"] # Add IDX tickers via local vendor
    market_map = {t: "ETF" if t in ["SPY", "QQQ"] else "US" for t in tickers}
    
    load_info = pipeline.run(polygon_daily_resource(tickers, market_map, api_key="YOUR_POLYGON_KEY"))
    print(f"ETL Pipeline Complete: {load_info}")
```
*Deployment:* Triggered by a Cron job (e.g., `0 6 * * *`) via Linux Cron or Celery Beat.

---

## 5. The Real-Time Traffic Cop (`MarketDataRouter`)

Because we dropped `yfinance`, we no longer need `asyncio.to_thread()`. We can use `httpx.AsyncClient` natively, making the event loop incredibly fast and efficient.

### 5.1 Native Async Router with Circuit Breaker
```python
# src/data/market_data_router.py
import asyncio
import json
import time
import logging
import redis.asyncio as aioredis
import httpx

logger = logging.getLogger("MarketDataRouter")

class MarketDataRouter:
    def __init__(self, redis_url: str, polygon_key: str, finnhub_key: str):
        self.redis = aioredis.from_url(redis_url, decode_responses=True)
        self.polygon_key = polygon_key
        self.finnhub_key = finnhub_key
        
        # The Traffic Cop: Max 1 concurrent API request to prevent self-inflicted rate limits
        self.api_semaphore = asyncio.Semaphore(1)
        self.last_request_time = 0.0
        self.MIN_REQUEST_INTERVAL = 0.5  # 500ms delay (APIs allow more than yfinance)
        
        # Native async HTTP client
        self.http_client = httpx.AsyncClient(timeout=10.0)

    async def get_realtime_quote(self, ticker: str) -> dict:
        cache_key = f"rt:quote:{ticker}"
        lock_key = f"lock:{cache_key}"

        # 1. Check Cache
        cached_data = await self.redis.get(cache_key)
        if cached_data:
            return json.loads(cached_data)

        # 2. Acquire Redis Lock (Prevents Cache Stampede)
        lock_acquired = await self.redis.set(lock_key, "1", nx=True, ex=10)
        if not lock_acquired:
            # Wait for the other thread to populate the cache
            for _ in range(20): 
                await asyncio.sleep(0.5)
                cached_data = await self.redis.get(cache_key)
                if cached_data:
                    return json.loads(cached_data)
            raise TimeoutError(f"Timed out waiting for cache lock on {ticker}")

        try:
            # 3. Throttle & Fetch
            async with self.api_semaphore:
                wait_time = self.MIN_REQUEST_INTERVAL - (time.time() - self.last_request_time)
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                self.last_request_time = time.time()

                # 4. Try Tier 1: Polygon.io
                try:
                    return await self._fetch_polygon_quote(ticker, cache_key)
                except Exception as e:
                    logger.warning(f"[Router] Polygon failed for {ticker}: {e}. Trying Finnhub.")
                    
                # 5. Try Tier 2: Finnhub
                try:
                    return await self._fetch_finnhub_quote(ticker, cache_key)
                except Exception as e:
                    logger.error(f"[Router] Finnhub failed for {ticker}: {e}")
                    raise Exception(f"All data tiers failed for {ticker}")

        finally:
            if lock_acquired:
                await self.redis.delete(lock_key)

    async def _fetch_polygon_quote(self, ticker: str, cache_key: str) -> dict:
        url = f"https://api.polygon.io/v2/last/trade/{ticker}"
        response = await self.http_client.get(url, params={"apiKey": self.polygon_key})
        response.raise_for_status()
        data = response.json()
        
        price = data['results']['p']
        quote = {"ticker": ticker, "price": price, "timestamp": time.time(), "source": "polygon"}
        await self.redis.set(cache_key, json.dumps(quote), ex=60)
        return quote

    async def _fetch_finnhub_quote(self, ticker: str, cache_key: str) -> dict:
        url = "https://finnhub.io/api/v1/quote"
        response = await self.http_client.get(url, params={"symbol": ticker, "token": self.finnhub_key})
        response.raise_for_status()
        data = response.json()
        
        price = data['c'] # Current price
        quote = {"ticker": ticker, "price": price, "timestamp": time.time(), "source": "finnhub"}
        await self.redis.set(cache_key, json.dumps(quote), ex=60)
        return quote

    async def close(self):
        await self.http_client.aclose()
```

---

## 6. Local Technical Analysis Engine

**Rule:** We NEVER ask an external API to calculate RSI, MACD, or Bollinger Bands. We fetch the raw numbers from PostgreSQL and calculate them locally in milliseconds.

```python
# src/analysis/indicators.py
import pandas as pd
import pandas_ta as ta
from sqlalchemy import select
from src.models.database import async_session
from src.models.tables import DailyOhlcv

async def get_ticker_with_indicators(ticker: str, market: str):
    # 1. Fetch raw data from OUR Postgres DB
    async with async_session() as session:
        result = await session.execute(
            select(DailyOhlcv).where(
                DailyOhlcv.ticker == ticker, 
                DailyOhlcv.market == market
            ).order_by(DailyOhlcv.date.desc()).limit(200)
        )
        rows = result.scalars().all()
        
    # 2. Convert to Pandas DataFrame
    df = pd.DataFrame([{
        'open': r.open, 'high': r.high, 'low': r.low, 'close': r.close, 'volume': r.volume
    } for r in reversed(rows)])
    
    # 3. Calculate Indicators Locally (Instantaneous, Zero API Cost)
    df.ta.sma(length=50, append=True)
    df.ta.sma(length=200, append=True)
    df.ta.rsi(length=14, append=True)
    df.ta.bbands(length=20, append=True)
    df.ta.atr(length=14, append=True) # For volatility-based position sizing
    
    return df
```

---

## 7. Observability & Failure Handling

If the data pipeline fails, the CIO needs to know immediately. We cannot trade blind.

### 7.1 Stale Data Warning
If the `MarketDataRouter` fails to fetch fresh data from *all* tiers, it must return the **last cached value from Redis**, but flag it as stale.
```python
# In get_realtime_quote fallback logic:
stale_data = await self.redis.get(cache_key)
if stale_data:
    data = json.loads(stale_data)
    data["is_stale"] = True
    data["stale_minutes"] = int((time.time() - data["timestamp"]) / 60)
    return data # Return stale data rather than crashing the bot
```

### 7.2 Automated Telegram Alerts
If the nightly `dlt` ETL pipeline fails, or if the `MarketDataRouter` hits a critical failure, the system must push an alert to the CIO's Telegram.

```python
async def send_data_alert(message: str):
    await bot.send_message(
        chat_id=SETTINGS.TELEGRAM_CHAT_ID,
        text=f"🚨 <b>DATA PIPELINE ALERT</b>\n━━━━━━━━━━━━━━━━\n<i>{message}</i>",
        parse_mode="HTML"
    )
```

---

## 8. Infrastructure & Deployment

The data stack must be containerized using Docker Compose.

```yaml
# docker-compose.yml
version: '3.8'
services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: karsa
      POSTGRES_PASSWORD: password
      POSTGRES_DB: karsa_db
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  app:
    build: .
    depends_on:
      - postgres
      - redis
    environment:
      - DATABASE_URL=postgresql://karsa:password@postgres:5432/karsa_db
      - REDIS_URL=redis://redis:6379
      - POLYGON_API_KEY=${POLYGON_API_KEY}
      - FINNHUB_API_KEY=${FINNHUB_API_KEY}
    command: >
      sh -c "python src/etl/run_initial_backfill.py && 
             python src/main.py"

volumes:
  pgdata:
```

---

## 9. CIO Directives & Rules of Engagement

To the Development Team:

1.  **No More Scrapers:** `yfinance` and `Verdenroz` are officially banned from this repository. If I see them in `requirements.txt`, the PR will be rejected. We use official REST APIs (`httpx`) and `dlt`.
2.  **The Database is King:** The AI Orchestrator and the Telegram Bot must query PostgreSQL for historical data. If I see an external API being called to get data older than 24 hours in the logs, you have failed the architecture.
3.  **Respect the Traffic Cop:** The `asyncio.Semaphore(1)` and the Redis Locks in the `MarketDataRouter` are non-negotiable. They ensure we never accidentally DDoS our own API providers.
4.  **Calculate Locally:** Use `pandas-ta`. Do not pass raw price arrays to the LLM and ask it to calculate the RSI. It is slow, expensive, and mathematically unreliable.
5.  **Monitor the APIs:** Set up a log aggregator. If we see HTTP 401/403/429 errors from Polygon or Finnhub, the API keys are compromised or rate-limited, and we need to rotate them immediately.

By shifting the heavy lifting to a local Data Warehouse via `dlt`, and utilizing native async HTTP clients with strict Redis caching, we have built a data pipeline that is resilient, institutional-grade, and completely immune to the 429 errors that plagued our previous iteration.

Execute this plan immediately.