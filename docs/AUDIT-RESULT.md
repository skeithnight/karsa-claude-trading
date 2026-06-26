# Architecture Design Document: Karsa AI Trading System

**Target Markets:** Indonesia Stock Exchange (IDX), US Equities (NYSE/NASDAQ), Global ETFs  
**Core Infrastructure:** Custom Python Orchestrator (Anthropic SDK) + 9Router API Gateway + TradingView MCP  
**Repository Name:** `karsa-claude-trading`

---

## 1. Executive Summary

**Karsa** is a production-grade, multi-market AI trading system. It utilizes a **Custom Python Orchestrator** powered by the Anthropic SDK's agentic tool-use capabilities to manage parallel analysis and decision-making. 

To ensure 24/7 zero-downtime and cost efficiency, all LLM traffic is routed through **9Router**, an intelligent API gateway that provides prompt caching and a 3-tier fallback mechanism. The system features a robust **Human-in-the-Loop (HITL)** Telegram interface for execution approval, strict adherence to IDX/US market microstructure rules, and comprehensive state management via PostgreSQL and Redis.

---

## 2. High-Level Architecture

*Correction from previous draft: The "Agent Layer" is now a standard Python microservice using the Anthropic SDK, not a hacked Claude Code CLI daemon.*

```mermaid
graph TD
    subgraph "Interface Layer"
        TG[Telegram Bot<br/>HITL & Webhook Server]
    end

    subgraph "API Gateway"
        NR[9Router<br/>Fallback Routing & Caching]
    end

    subgraph "Orchestration Layer (Python Microservices)"
        SCH[Scheduler<br/>APScheduler / Cron]
        ORCH[Lead Orchestrator<br/>Anthropic SDK Tool-Use Loop]
        
        subgraph "Specialized Agents (Python Classes)"
            IDX[IDX Analyst]
            US[US Equity Analyst]
            ETF[ETF Analyst]
            RISK[Risk Manager]
        end
    end

    subgraph "Data & State Layer"
        MCP[TradingView MCP<br/>Price & TA Data]
        IDX_API[IDX Broker API<br/>Foreign Flow Data]
        RDS[(Redis<br/>Cache, Pub/Sub, Rate Limits)]
        PG[(PostgreSQL<br/>Schema, State, Audit)]
    end

    subgraph "Execution Layer"
        IDX_BRK[IDX Execution<br/>IPOT/Mirae]
        US_BRK[US Execution<br/>Alpaca/IBKR]
    end

    SCH -->|Trigger| ORCH
    ORCH -->|Dispatch| IDX & US & ETF & RISK
    IDX & US & ETF & RISK -->|LLM API Calls| NR
    NR -->|Routes/Fallbacks| LLM_Providers[Anthropic/DeepSeek/etc]
    
    IDX & US & ETF -->|Market Data| MCP
    IDX -->|Foreign Flow| IDX_API
    
    ORCH -->|State Sync| PG
    ORCH -->|Rate Limiting| RDS
    ORCH -->|Trade Signal| TG
    TG -->|Approved| ORCH
    ORCH -->|Execute| IDX_BRK & US_BRK
```

---

## 3. Infrastructure & State Management

### 3.1 Docker Compose Stack
*Note: Removed the fake `claude-agent` daemon. Replaced with actual Python microservices.*

```yaml
version: '3.8'

services:
  # 1. API GATEWAY
  karsa-9router:
    image: decolua/9router:latest
    container_name: karsa-9router
    ports:
      - "20128:20128"
    volumes:
      - ./9router-config.yaml:/app/config.yaml
      - 9router-data:/app/data
    networks: [trading-net]
    restart: unless-stopped

  # 2. DATABASE & STATE
  redis:
    image: redis:7-alpine
    container_name: karsa-redis
    command: redis-server --appendonly yes --maxmemory 512mb --maxmemory-policy allkeys-lru
    networks: [trading-net]
    restart: unless-stopped

  postgres:
    image: postgres:15-alpine
    container_name: karsa-postgres
    environment:
      POSTGRES_DB: trading
      POSTGRES_USER: trader
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./db/init.sql:/docker-entrypoint-initdb.d/init.sql # Auto-creates schema
    networks: [trading-net]
    restart: unless-stopped

  # 3. DATA LAYER
  tradingview-mcp:
    image: python:3.11-slim
    container_name: karsa-tradingview-mcp
    command: >
      sh -c "pip install tradingview-mcp-server && tradingview-mcp --port 8080"
    networks: [trading-net]
    restart: unless-stopped

  # 4. ORCHESTRATION & AGENTS (The actual Python App)
  karsa-orchestrator:
    build:
      context: .
      dockerfile: Dockerfile.orchestrator
    container_name: karsa-orchestrator
    env_file: [.env]
    environment:
      - REDIS_URL=redis://redis:6379
      - POSTGRES_URL=postgresql://trader:${DB_PASSWORD}@postgres:5432/trading
    depends_on: [karsa-9router, redis, postgres, tradingview-mcp]
    networks: [trading-net]
    command: ["python", "-m", "src.main"] # Runs the scheduler and orchestrator
    restart: unless-stopped

  # 5. TELEGRAM BOT (Separate service for webhook handling)
  karsa-telegram-bot:
    build:
      context: .
      dockerfile: Dockerfile.bot
    container_name: karsa-telegram-bot
    env_file: [.env]
    ports:
      - "8443:8443" # Webhook port
    depends_on: [redis, postgres]
    networks: [trading-net]
    command: ["python", "-m", "src.bot.main"]
    restart: unless-stopped

networks:
  trading-net: { driver: bridge }

volumes:
  redis-data:
  postgres-data:
  9router-data:
```

---

## 4. Database Schema & State Management

*Addresses Audit Gap #7 & #8: Explicit schema and portfolio state definition.*

### 4.1 PostgreSQL Schema (`db/init.sql`)
```sql
-- 1. Portfolio State (Synced from Brokers)
CREATE TABLE portfolio_state (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    market VARCHAR(10) NOT NULL, -- 'IDX', 'US', 'ETF'
    quantity DECIMAL(18, 4) NOT NULL,
    avg_cost DECIMAL(18, 4) NOT NULL,
    last_synced_at TIMESTAMP DEFAULT NOW()
);

-- 2. Signals (Generated by Agents)
CREATE TABLE signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker VARCHAR(20) NOT NULL,
    market VARCHAR(10) NOT NULL,
    strategy VARCHAR(50) NOT NULL,
    direction VARCHAR(10) NOT NULL, -- 'LONG', 'SHORT', 'CLOSE'
    confidence_score INT CHECK (confidence_score >= 0 AND confidence_score <= 100),
    target_price DECIMAL(18, 4),
    stop_loss_price DECIMAL(18, 4),
    created_at TIMESTAMP DEFAULT NOW()
);

-- 3. Trades (Execution Records)
CREATE TABLE trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id UUID REFERENCES signals(id),
    ticker VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL, -- 'BUY', 'SELL'
    quantity DECIMAL(18, 4) NOT NULL,
    filled_price DECIMAL(18, 4),
    status VARCHAR(20) DEFAULT 'PENDING', -- 'PENDING', 'FILLED', 'PARTIAL', 'REJECTED'
    broker_order_id VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 4. Immutable Audit Logs
CREATE TABLE audit_logs (
    id BIGSERIAL PRIMARY KEY,
    component VARCHAR(50) NOT NULL, -- 'ORCHESTRATOR', 'RISK_AGENT', 'TELEGRAM'
    action VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 5. Historical OHLCV Cache (Persisted from Redis)
CREATE TABLE ohlcv_cache (
    ticker VARCHAR(20),
    market VARCHAR(10),
    timeframe VARCHAR(5), -- '1H', '4H', '1D'
    timestamp TIMESTAMP,
    open DECIMAL(18, 4), high DECIMAL(18, 4), low DECIMAL(18, 4), close DECIMAL(18, 4), volume BIGINT,
    PRIMARY KEY (ticker, market, timeframe, timestamp)
);
```

### 4.2 State & Caching Strategy (Redis)
*Addresses Audit Gap #6: Data persistence and TTLs.*
- **Real-time Prices:** Cached in Redis with a **60-second TTL**. If MCP fails, agents use the stale cache.
- **Rate Limiting:** Redis Token Bucket algorithm to enforce API rate limits (e.g., max 10 MCP calls/minute per agent).
- **OHLCV Data:** Fetched from MCP, cached in Redis (1-hour TTL), and asynchronously flushed to Postgres `ohlcv_cache` for backtesting.

---

## 5. 9Router Configuration & Cost Management

*Addresses Audit Issue #3 & Missing Section: Cost budgeting and verified 9Router features.*

### 5.1 Verified 9Router Features
- **Fallback Routing:** If Anthropic returns a 429 (Rate Limit) or 500 error, 9Router instantly routes to the next tier.
- **Prompt Caching:** 9Router caches system prompts and large tool definitions, significantly reducing input token costs for repetitive agent loops.
- *(Note: Removed unverified "RTK Token Saver" claims. Relying on standard prompt caching and fallback routing).*

### 5.2 3-Tier Fallback Combos
```yaml
# 9router-config.yaml
server:
  port: 20128

combos:
  - name: "karsa-critical" # Orchestrator, Risk Manager
    models:
      - { provider: anthropic, model: claude-3-5-sonnet-20241022, tier: subscription }
      - { provider: deepseek, model: deepseek-v3, tier: cheap, fallback_on: [rate_limit, error] }

  - name: "karsa-routine" # Data Agents, Technical Analysts
    models:
      - { provider: anthropic, model: claude-3-haiku-20240307, tier: subscription }
      - { provider: deepseek, model: deepseek-v3, tier: cheap, fallback_on: [rate_limit, error] }
```

### 5.3 Cost Budgeting
- **Monthly Ceiling:** Hard limit set in 9Router dashboard at $150/month.
- **Circuit Breaker:** If daily spend exceeds $10, 9Router blocks all `karsa-routine` requests, forcing the system to rely on cached data and halt new signal generation until the next day.

---

## 6. Agent Architecture (Anthropic SDK)

*Addresses Audit Issue #1 & #2: Replaced fake Claude Code daemon with actual Python SDK implementation.*

The "Agents" are Python classes utilizing the Anthropic SDK's native **Tool Use** loop.

```python
# src/agents/base.py
import anthropic
from src.mcp.client import MCPClient

class BaseAgent:
    def __init__(self, combo_name: str):
        self.client = anthropic.AsyncAnthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
        self.combo_name = combo_name # Passed in headers for 9Router routing
        self.mcp = MCPClient()

    async def run(self, task: str, tools: list):
        # Standard Anthropic Agentic Tool-Use Loop
        messages = [{"role": "user", "content": task}]
        while True:
            response = await self.client.messages.create(
                model=self.combo_name, # 9Router handles the actual model mapping
                max_tokens=2000,
                tools=tools,
                messages=messages
            )
            
            # Process tool calls (e.g., get_quote, get_technical_analysis)
            tool_results = await self.mcp.execute_tool_calls(response.content)
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
            
            if response.stop_reason == "end_turn":
                return response.content
```

---

## 7. Market Data & Strategy Specifics

*Addresses Audit Concern #11 & #12: Data sources and timeframe clarifications.*

### 7.1 IDX Strategy: "Foreign Flow Breakout"
- **Data Source Correction:** TradingView MCP does *not* provide granular IDX foreign flow data. 
- **Solution:** We integrate a dedicated **IDX Data Adapter** (via Stockbit/RTI API or Broker API) specifically to fetch `foreign_net_buy_volume`. This is cached in Redis and exposed as a custom MCP tool `get_idx_foreign_flow`.
- **Timeframe:** Daily close data for trend, intraday 15m for entry execution.

### 7.2 ETF Strategy: "Mean Reversion"
- **Timeframe Clarification:** Uses **Daily (1D)** candles for calculating RSI and Bollinger Bands. Signals are generated at market close (16:00 ET / 15:00 WIB). Execution happens at the next day's open or via limit orders.

---

## 8. Execution, Error Handling & Reliability

*Addresses Audit Gaps #4, #5, #9 and Missing Sections.*

### 8.1 Broker Execution Error Handling
- **Rejected Orders:** If IPOT/Alpaca rejects an order (e.g., insufficient funds, invalid price), the `trades` table is updated to `REJECTED`. The Orchestrator logs the error and alerts Telegram. No automatic retry for logical rejections.
- **Partial Fills:** The Orchestrator polls the broker API every 10 seconds. If a fill is partial, it updates the `quantity` in Postgres and keeps the order open until `FILLED` or `CANCELLED`.
- **State Reconciliation:** Every morning at 08:00 local time, a background job fetches the official position list from both brokers and overwrites `portfolio_state` in Postgres to prevent drift.

### 8.2 Rate Limiting & Throttling
- **Redis Token Bucket:** Each agent has a Redis key `ratelimit:{agent_name}`. Before calling 9Router or MCP, the agent checks the bucket. If empty, it sleeps for 5 seconds.
- **MCP Polling:** Market data is not polled continuously. The Scheduler triggers data fetches exactly at candle closes (e.g., 09:30, 10:00, 16:00) to minimize API calls.

### 8.3 Timezone & Market Hours
- **Scheduler:** Uses `APScheduler` running in UTC.
- **Market Hours Logic:** 
  - IDX: 09:00 - 16:00 WIB (UTC+7). Lunch break 12:00 - 13:30.
  - US: 09:30 - 16:00 ET (UTC-4/5).
- **Holidays:** The system checks a `market_holidays` table before generating signals. If the market is closed, the scheduler skips the cycle.

### 8.4 Disaster Recovery & Multi-Instance
- **Stateless Orchestrator:** The `karsa-orchestrator` container holds no local state. If the VPS crashes, Docker restarts the container, and it reads the current state from Postgres/Redis.
- **Idempotency:** Every trade execution request includes a unique `idempotency_key` (UUID) sent to the broker API to prevent double-execution if the network drops during the request.
- **Multi-Instance:** Currently, the system is designed as a **Single Active Instance** (Leader Election via Redis Lock) to prevent two orchestrators from generating conflicting signals.

---

## 9. Telegram Bot & Security

*Addresses Audit Concern #14: Webhook security.*

### 9.1 Webhook Security
- **Secret Token:** The Telegram Bot API webhook is registered with a `secret_token` header.
- **Validation:** The FastAPI webhook endpoint in `karsa-telegram-bot` strictly validates the `X-Telegram-Bot-Api-Secret-Token` header on every request. Requests without the correct secret are dropped immediately (403 Forbidden).
- **Chat ID Whitelist:** Even with a valid secret token, the bot only processes commands from the hardcoded `TELEGRAM_CHAT_ID`.

---

## 10. Backtesting Framework

*Addresses Audit Gap #10.*

Before deploying a strategy to live trading, it must be validated using the `ohlcv_cache` in Postgres.

- **Engine:** We use `vectorbt` for fast, vectorized backtesting of the technical indicators.
- **Data Pipeline:** Historical data is downloaded via TradingView MCP and stored in `ohlcv_cache`.
- **Metrics:** The backtester outputs Win Rate, Sharpe Ratio, Max Drawdown, and Profit Factor. A strategy must meet minimum thresholds (e.g., Sharpe > 1.2, Max DD < 15%) before the Orchestrator is allowed to use it in live mode.

---

## 11. Logging & Observability

*Addresses Missing Section: Logging strategy.*

- **Structured Logging:** All Python services use `structlog` to output JSON-formatted logs.
- **Log Destinations:** 
  - `stdout` is captured by Docker.
  - Logs are shipped to a centralized Loki stack (or Datadog) via Promtail.
- **Agent Reasoning Traces:** Every LLM prompt and response is logged to the `audit_logs` table in Postgres with the component name (e.g., `RISK_AGENT`). This allows us to debug *why* an agent made a specific decision.
```

### Summary of Fixes Applied from Audit:
1. **Architectural Pivot:** Completely removed the "Claude Code Daemon" concept. Replaced with a standard Python microservice using the Anthropic SDK's tool-use loop.
2. **Database Schema:** Added explicit SQL schema for `portfolio_state`, `signals`, `trades`, `audit_logs`, and `ohlcv_cache`.
3. **Data Reality Check:** Acknowledged TradingView lacks IDX foreign flow; added a dedicated IDX Data Adapter requirement. Clarified ETF strategy timeframes.
4. **Execution & Reliability:** Added comprehensive error handling for broker rejections, partial fills, state reconciliation, and idempotency keys.
5. **Security:** Added Telegram webhook secret token validation and Redis-based rate limiting.
6. **Missing Sections:** Added Disaster Recovery, Logging Strategy, Cost Budgeting, and a Backtesting Framework section.