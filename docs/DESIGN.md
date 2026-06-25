# Architecture Design Document: Karsa AI Trading System

**Target Markets:** Indonesia Stock Exchange (IDX), US Equities (NYSE/NASDAQ), Global ETFs  
**Core Infrastructure:** Claude Code Native Agent Teams + 9Router API Gateway + TradingView MCP  
**Repository Name:** `karsa-claude-trading`

---

## 1. Executive Summary

**Karsa** is a next-generation, AI-driven multi-market trading system. It abandons traditional sequential, middleware-heavy architectures (like custom Python orchestrators) in favor of a **Claude Code-Native Architecture**. 

By leveraging Claude's native **Agent Teams** and **Sub-agents**, the system achieves parallel processing and superior reasoning. To ensure 24/7 zero-downtime and extreme cost efficiency, all LLM traffic is routed through **9Router**, an intelligent API gateway that compresses massive market data payloads (saving 20-40% on tokens) and provides a 3-tier fallback mechanism. The system features a robust **Human-in-the-Loop (HITL)** Telegram interface for execution approval and strictly adheres to the microstructural rules of both the IDX and US markets.

---

## 2. High-Level Architecture

```mermaid
graph TD
    subgraph "Interface Layer"
        TG[Telegram Bot<br/>HITL Approval & Alerts]
    end

    subgraph "API Gateway & Cost Optimization"
        NR[9Router<br/>RTK Token Saver & 3-Tier Fallback]
    end

    subgraph "Claude Code Runtime (Agent Teams)"
        LEAD[Lead Orchestrator<br/>Sonnet Model]
        
        subgraph "Parallel Sub-Agents"
            IDX[IDX Analyst<br/>Haiku Model]
            US[US Equity Analyst<br/>Haiku Model]
            ETF[ETF Analyst<br/>Haiku Model]
            RISK[Risk & Macro Manager<br/>Opus Model]
        end
    end

    subgraph "Data & State Layer"
        MCP[TradingView MCP<br/>Unified Market Data]
        RDS[(Redis<br/>Cache & Pub/Sub)]
        PG[(PostgreSQL<br/>Audit & Trade History)]
    end

    subgraph "Execution Layer"
        IDX_BRK[IDX Broker API<br/>IPOT/Mirae]
        US_BRK[US Broker API<br/>Alpaca/IBKR]
    end

    TG -->|Commands/Approvals| LEAD
    LEAD -->|LLM Requests| NR
    IDX & US & ETF & RISK -->|LLM Requests| NR
    NR -->|Routes to Anthropic/DeepSeek/etc| LLM_Providers[External LLMs]
    
    LEAD -->|Spawn Teams| IDX & US & ETF & RISK
    IDX & US & ETF & RISK -->|Query Data| MCP
    MCP -->|Real-time JSON| NR
    NR -->|Compressed Tool Results| IDX & US & ETF & RISK
    
    IDX & US & ETF & RISK -->|State/Cache| RDS
    RISK -->|Audit Logs| PG
    LEAD -->|Final Decision| TG
    TG -->|Approved Trade| LEAD
    LEAD -->|Execute| IDX_BRK & US_BRK
```

---

## 3. Infrastructure & State Management

The system runs on a containerized Docker stack ensuring persistent state, fast caching, and isolated execution.

### 3.1 Docker Compose Stack
```yaml
version: '3.8'

services:
  # 1. API GATEWAY & COST OPTIMIZATION
  9router:
    image: decolua/9router:latest
    container_name: karsa-9router
    ports:
      - "20128:20128"
    volumes:
      - ./9router-config.yaml:/app/config.yaml
      - 9router-data:/app/data
    networks:
      - trading-net
    restart: unless-stopped

  # 2. DATABASE & STATE LAYER
  redis:
    image: redis:7-alpine
    container_name: karsa-redis
    command: redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru
    volumes:
      - redis-data:/data
    networks:
      - trading-net
    restart: unless-stopped

  postgres:
    image: postgres:15-alpine
    container_name: karsa-postgres
    environment:
      POSTGRES_DB: trading
      POSTGRES_USER: trader
      POSTGRES_PASSWORD: ${DB_PASSWORD:-secure_password}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    networks:
      - trading-net
    restart: unless-stopped

  # 3. DATA LAYER
  tradingview-mcp:
    image: python:3.11-slim
    container_name: karsa-tradingview-mcp
    working_dir: /app
    command: >
      sh -c "pip install tradingview-mcp-server && 
             tradingview-mcp --port 8080"
    environment:
      - TRADINGVIEW_MARKET=stocks,etf,forex
      - IDX_SUFFIX=.JK
      - US_MARKETS=NYSE,NASDAQ
    ports:
      - "8080:8080"
    networks:
      - trading-net
    restart: unless-stopped

  # 4. AGENT LAYER (The Brain)
  claude-agent:
    build:
      context: .
      dockerfile: Dockerfile.agent
    container_name: karsa-claude-agent
    env_file:
      - .env
    environment:
      # POINT TO 9ROUTER INSTEAD OF ANTHROPIC DIRECTLY
      - ANTHROPIC_BASE_URL=http://9router:20128/v1
      - REDIS_URL=redis://redis:6379
      - POSTGRES_URL=postgresql://trader:${DB_PASSWORD:-secure_password}@postgres:5432/trading
      - MCP_SERVER_URL=http://tradingview-mcp:8080
    volumes:
      - ./:/workspace
      - ./.claude:/root/.claude
    depends_on:
      - 9router
      - redis
      - postgres
      - tradingview-mcp
    networks:
      - trading-net
    command: ["python", "run_agent_sdk.py"]
    restart: unless-stopped

networks:
  trading-net:
    driver: bridge

volumes:
  redis-data:
  postgres-data:
  9router-data:
```

---

## 4. 9Router Configuration & Token Strategy

9Router acts as the intelligent API gateway, abstracting API keys from the agents and optimizing costs.

### 4.1 RTK Token Saver (Crucial for MCP)
The `tradingview-mcp` returns massive JSON payloads (historical candles, order books). 9Router's **RTK Token Saver** automatically compresses these `tool_result` outputs *before* they hit Claude's context window, saving **20-40% on input tokens**.

### 4.2 3-Tier Fallback Combos
```yaml
# 9router-config.yaml
server:
  port: 20128

rtk_token_saver:
  enabled: true
  compression_level: high 

combos:
  # Used by: Lead Orchestrator, Risk Manager (Requires deep reasoning)
  - name: "karsa-critical"
    models:
      - provider: anthropic
        model: claude-3-5-sonnet-20241022
        tier: subscription
      - provider: deepseek
        model: deepseek-chat
        tier: cheap
        fallback_on: [rate_limit, quota_exhausted, error]

  # Used by: Data Agents, Technical Analysts (High volume, lower reasoning need)
  - name: "karsa-routine"
    models:
      - provider: anthropic
        model: claude-3-haiku-20240307
        tier: subscription
      - provider: minimax
        model: MiniMax-M2.7
        tier: cheap
      - provider: kiro
        model: claude-sonnet-4.5
        tier: free
        fallback_on: [rate_limit, quota_exhausted]
```

---

## 5. Agent Architecture & Model Routing

The agents no longer manage API keys. They request specific "Combos" from 9Router based on their cognitive load requirements.

| Agent Role | 9Router Combo Used | Fallback Strategy |
| :--- | :--- | :--- |
| **Lead Orchestrator** | `karsa-critical` | Anthropic Sonnet → DeepSeek-V3 |
| **Risk & Macro Manager** | `karsa-critical` | Anthropic Opus → DeepSeek-V3 |
| **Data & Formatting Agents** | `karsa-routine` | Anthropic Haiku → MiniMax → Free Tier |

### 5.1 Environment Variables (.env)
```bash
# ==========================================
# 9ROUTER & LLM CONFIGURATION
# ==========================================
ANTHROPIC_BASE_URL="http://9router:20128/v1"
ANTHROPIC_AUTH_TOKEN="9router_internal_token" 
ANTHROPIC_MODEL="claude-3-5-sonnet-20241022"
ANTHROPIC_DEFAULT_SONNET_MODEL="claude-3-5-sonnet-20241022"
ANTHROPIC_DEFAULT_OPUS_MODEL="claude-3-opus-20240229"
ANTHROPIC_DEFAULT_HAIKU_MODEL="claude-3-haiku-20240307"

# ==========================================
# BROKER & EXECUTION APIs
# ==========================================
IDX_BROKER_API_URL="https://api.broker.co.id/v1"
IDX_BROKER_TOKEN="idx_token_here"
US_BROKER_API_URL="https://api.alpaca.markets/v2"
US_BROKER_KEY="us_key_here"
US_BROKER_SECRET="us_secret_here"

# ==========================================
# TELEGRAM & NOTIFICATIONS
# ==========================================
TELEGRAM_TOKEN="telegram_bot_token"
TELEGRAM_CHAT_ID="chat_id_here"

# ==========================================
# DATABASE & STATE
# ==========================================
REDIS_URL="redis://redis:6379"
POSTGRES_URL="postgresql://trader:secure_password@postgres:5432/trading"
```

---

## 6. Trading Strategies (Detailed)

The system employs distinct algorithmic strategies tailored to the microstructure of each market.

### 6.1 IDX Strategy: "Foreign Flow Breakout"
*Context: IDX is heavily influenced by foreign institutional flow and has strict auto-rejection limits.*
*   **Universe:** LQ45 and IDX30 (High liquidity, e.g., BBCA.JK, TLKM.JK).
*   **Entry Signals:**
    1.  **Foreign Net Buy:** 3 consecutive days of foreign net buying > 5% of daily volume.
    2.  **Technical Breakout:** Price breaks above the 20-day Bollinger Band upper limit with volume > 1.5x average.
    3.  **Auto-Rejection Buffer:** Entry price must be at least 2% below the daily Auto Rejection Upper (ARA) limit to avoid being trapped at the ceiling.
*   **Exit Signals:** Trailing stop at 2x ATR or -5% hard stop. Scale out 50% at +10%.
*   **Position Sizing:** Max 15% of portfolio per stock. Minimum 1 lot (100 shares).

### 6.2 US Equity Strategy: "Relative Strength Momentum"
*Context: US markets are highly efficient; alpha is found in relative strength and sector rotation.*
*   **Universe:** US Mega-cap and high-volume mid-caps (e.g., NVDA, AAPL).
*   **Entry Signals:**
    1.  **Relative Strength (RS):** Stock's 60-day return must outperform SPY by > 15%.
    2.  **Trend Alignment:** Price > 50 EMA > 200 EMA.
*   **Exit Signals:** Close below 20-day EMA or 3:1 Risk/Reward target hit.
*   **Position Sizing:** Volatility-targeted sizing (risk 1% of total equity per trade). Supports fractional shares.

### 6.3 ETF Strategy: "Macro Trend & Mean Reversion"
*   **Strategy A (Trend Following):** For broad market ETFs (SPY, QQQ, ETFIDX). Buy when price > 200 SMA. Rebalance monthly.
*   **Strategy B (Mean Reversion):** For sector ETFs (XLF, XLK). Buy when RSI < 30 and price touches lower Bollinger Band. Sell when RSI > 70.

---

## 7. Telegram Bot & Human-in-the-Loop (HITL)

The Telegram bot is the primary interface for the human portfolio manager, handling alerts, portfolio queries, and cryptographic/manual trade approval.

### 7.1 Bot Architecture
*   **Framework:** `python-telegram-bot` (Asyncio based).
*   **Webhook Mode:** Deployed behind a reverse proxy to receive inline button callbacks instantly.
*   **Security:** Only the authorized `TELEGRAM_CHAT_ID` can execute trades.

### 7.2 The Trade Approval Flow (HITL)
When the Claude Agent Team synthesizes a high-conviction trade, it does **not** execute it immediately. It triggers the Telegram Bot.

**Step 1: The Alert Message**
```text
🚨 NEW TRADE SIGNAL: BBCA.JK (IDX)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 Strategy: Foreign Flow Breakout
💰 Entry: IDR 9,450 (Limit)
🎯 Target: IDR 10,200 (+7.9%)
🛑 Stop Loss: IDR 9,100 (-3.7%)
📐 Size: 10 Lots (IDR 9,450,000)
🧠 Model Used: Sonnet (via 9Router)
📉 Risk: 1.2% of total portfolio
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Expires in: 15 minutes
```

**Step 2: Inline Action Buttons**
`[ ✅ APPROVE ]` `[ ❌ REJECT ]` `[ ✏️ MODIFY ]` `[ 📊 VIEW CHART ]`

**Step 3: Execution Logic**
*   **APPROVE:** Bot sends webhook to `claude-agent`. Agent executes via Broker API and logs to Postgres.
*   **MODIFY:** Bot opens conversational flow to adjust limit price/quantity.
*   **REJECT:** Trade cancelled, reason logged for model fine-tuning.

---

## 8. Execution & Broker Integration

### 8.1 IDX Execution (IPOT/Mirae API)
*   **Lot Conversion:** Agent calculates shares, divides by 100, and rounds down to the nearest integer.
*   **ARA Check:** Middleware hook checks if `Limit Price <= ARA Upper Limit` before sending. If not, the order is rejected automatically.

### 8.2 US Execution (Alpaca/IBKR API)
*   **Fractional Shares:** Orders exact decimal quantities (e.g., `2.5` shares).
*   **PDT Check:** Risk Agent blocks trades that would trigger the Pattern Day Trader rule if account equity < $25,000.

---

## 9. Observability & Monitoring

### 9.1 9Router Metrics
Because 9Router handles all LLM traffic, it exposes critical cost metrics to Prometheus:
*   `tokens_saved_by_rtk_total`: Total input tokens saved by compressing MCP outputs.
*   `fallback_triggered_total`: Number of times the system switched from Anthropic to DeepSeek/MiniMax.
*   `api_cost_usd`: Real-time tracking of API spend across all providers.

### 9.2 Grafana Dashboards
1.  **System Health:** 9Router fallback events, Redis memory, Postgres connections.
2.  **Trading Performance:** Equity curve (USD and IDR), Win Rate by Market, Sharpe Ratio.
3.  **Cost Efficiency:** Token usage vs. Tokens saved by RTK, Cost per trade signal.

---

## 10. Security & Compliance

1.  **Secret Isolation:** API keys for Anthropic, DeepSeek, and Brokers are stored *only* inside 9Router's secure vault. The `claude-agent` container has zero knowledge of the actual LLM API keys.
2.  **Audit Immutability:** Postgres tables for trade history are append-only. Every trade decision, approval timestamp, and execution result is logged immutably.
3.  **Circuit Breakers:** If 9Router exhausts all fallback tiers (Subscription + Cheap + Free), it triggers a critical alert to Telegram and halts all new trade generation.
4.  **Market Compliance:** Strict adherence to IDX foreign ownership limits and US FINRA day trading rules via the continuous Risk Manager agent.

---

## 11. Implementation Roadmap

| Phase | Deliverable | Timeline |
| :--- | :--- | :--- |
| **Phase 1** | Docker Compose setup, **9Router deployment & config**, MCP connection. | Week 1 |
| **Phase 2** | Sub-agent creation, Strategy logic implementation (IDX/US/ETF). | Week 2 |
| **Phase 3** | Telegram Bot development, HITL approval flow, Broker API integration. | Week 3 |
| **Phase 4** | Postgres audit logging, Prometheus/Grafana dashboards (including 9Router metrics). | Week 4 |
| **Phase 5** | Live deployment with small capital, monitoring fallback events and token savings. | Week 5 |
```