# Karsa Crypto-Only Stack: Implementation & Validation Guide

## 📑 Executive Summary

This document outlines the procedure to disable the IDX/US/ETF trading stack (`karsa-orchestrator` and `karsa-telegram-bot`) and run a pure crypto autonomous stack. The goal is to reduce resource consumption, eliminate HITL overhead, and focus entirely on the crypto ASM (Autonomous Session Manager).

**Target State:**
- ✅ Crypto bot running autonomously on port 8444
- ✅ Infrastructure (Redis, Postgres, WARP) active
- ✅ Monitoring (Prometheus, Grafana, Alertmanager) active
- ❌ Orchestrator (port 8000) disabled
- ❌ Telegram bot (port 8443) disabled

---

## 🛑 Phase 0: Pre-Flight Checks

Before making changes, verify the current state and create a backup.

### 0.1 Verify Current Stack Status

```bash
# Check all running containers
docker-compose ps

# Expected output: All services should be "Up" or "running"
```

### 0.2 Backup Configuration Files

```bash
# Create backup directory
mkdir -p backups/$(date +%Y%m%d)

# Backup critical configs
cp docker-compose.yml backups/$(date +%Y%m%d)/docker-compose.yml.bak
cp prometheus/prometheus.yml backups/$(date +%Y%m%d)/prometheus.yml.bak

# Verify backups
ls -lh backups/$(date +%Y%m%d)/
```

### 0.3 Export Current Database State (Optional but Recommended)

```bash
# Dump the entire database
docker-compose exec postgres pg_dump -U karsa karsa_trading > backups/$(date +%Y%m%d)/db_dump.sql

# Verify dump size
ls -lh backups/$(date +%Y%m%d)/db_dump.sql
```

### 0.4 Record Current Crypto Bot Metrics

```bash
# Capture current metrics baseline
curl -s http://localhost:8444/metrics > backups/$(date +%Y%m%d)/crypto_metrics_baseline.txt

# Check current open positions
curl -s http://localhost:8444/positions | jq .
```

---

## 🔧 Phase 1: Implementation

### 1.1 Disable IDX/US/ETF Services in Docker Compose

Edit `docker-compose.yml` and comment out the orchestrator and telegram-bot services:

```yaml
services:
  # ============================================
  # INFRASTRUCTURE - KEEP ALL
  # ============================================
  redis:
    image: redis:7.0-alpine
    container_name: karsa-redis
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  postgres:
    image: pgvector/pgvector:pg15
    container_name: karsa-postgres
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: karsa
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: karsa_trading
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U karsa"]
      interval: 10s
      timeout: 5s
      retries: 5

  warp:
    image: caomingjun/warp
    container_name: karsa-warp
    ports:
      - "1080:1080"
    environment:
      - WARP_SLEEP=2
    restart: unless-stopped
    cap_add:
      - MKNOD
      - AUDIT_WRITE
      - NET_ADMIN
    devices:
      - /dev/net/tun:/dev/net/tun

  # ============================================
  # MONITORING - KEEP ALL
  # ============================================
  prometheus:
    image: prom/prometheus:v2.45.0
    container_name: karsa-prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus:/etc/prometheus
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.console.libraries=/usr/share/prometheus/console_libraries'
      - '--web.console.templates=/usr/share/prometheus/consoles'
    restart: unless-stopped

  grafana:
    image: grafana/grafana:10.0.0
    container_name: karsa-grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
    volumes:
      - grafana_data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning
    restart: unless-stopped

  alertmanager:
    image: prom/alertmanager:v0.25.0
    container_name: karsa-alertmanager
    ports:
      - "9093:9093"
    volumes:
      - ./alertmanager:/etc/alertmanager
    command:
      - '--config.file=/etc/alertmanager/alertmanager.yml'
    restart: unless-stopped

  # ============================================
  # CRYPTO STACK - KEEP
  # ============================================
  karsa-crypto-bot:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: karsa-crypto-bot
    ports:
      - "8444:8444"
    environment:
      - BYBIT_API_KEY=${BYBIT_API_KEY}
      - BYBIT_API_SECRET=${BYBIT_API_SECRET}
      - DATABASE_URL=postgresql+asyncpg://karsa:${POSTGRES_PASSWORD}@postgres:5432/karsa_trading
      - REDIS_URL=redis://redis:6379/0
      - WARP_PROXY=socks5://warp:1080
      - NINE_ROUTER_API_KEY=${NINE_ROUTER_API_KEY}
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
      warp:
        condition: service_started
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8444/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  # ============================================
  # IDX/US/ETF STACK - DISABLED
  # ============================================
  # karsa-orchestrator:
  #   build:
  #     context: .
  #     dockerfile: Dockerfile
  #   container_name: karsa-orchestrator
  #   ports:
  #     - "8000:8000"
  #   environment:
  #     - DATABASE_URL=postgresql+asyncpg://karsa:${POSTGRES_PASSWORD}@postgres:5432/karsa_trading
  #     - REDIS_URL=redis://redis:6379/0
  #     - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
  #   depends_on:
  #     redis:
  #       condition: service_healthy
  #     postgres:
  #       condition: service_healthy
  #   restart: unless-stopped

  # karsa-telegram-bot:
  #   build:
  #     context: .
  #     dockerfile: Dockerfile
  #   container_name: karsa-telegram-bot
  #   ports:
  #     - "8443:8443"
  #   environment:
  #     - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
  #     - REDIS_URL=redis://redis:6379/0
  #     - DATABASE_URL=postgresql+asyncpg://karsa:${POSTGRES_PASSWORD}@postgres:5432/karsa_trading
  #   depends_on:
  #     redis:
  #       condition: service_healthy
  #   restart: unless-stopped

volumes:
  redis_data:
  postgres_data:
  prometheus_data:
  grafana_data:
```

### 1.2 Update Prometheus Scrape Configuration

Edit `prometheus/prometheus.yml` to remove the disabled targets:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  # Crypto bot metrics
  - job_name: 'karsa-crypto-bot'
    static_configs:
      - targets: ['karsa-crypto-bot:8444']
    metrics_path: '/metrics'
    scrape_interval: 15s

  # Prometheus self-monitoring
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  # ============================================
  # DISABLED TARGETS - Commented out
  # ============================================
  # - job_name: 'karsa-orchestrator'
  #   static_configs:
  #     - targets: ['karsa-orchestrator:8000']
  #   metrics_path: '/metrics'
  #   scrape_interval: 15s

  # - job_name: 'karsa-telegram-bot'
  #   static_configs:
  #     - targets: ['karsa-telegram-bot:8443']
  #   metrics_path: '/metrics'
  #   scrape_interval: 15s
```

### 1.3 Restart the Stack

```bash
# Stop all services gracefully
docker-compose down

# Verify all containers are stopped
docker-compose ps

# Start only the active services
docker-compose up -d

# Wait for services to initialize
sleep 10

# Check status
docker-compose ps
```

**Expected output:**
```
NAME                STATUS              PORTS
karsa-redis         Up (healthy)        0.0.0.0:6379->6379/tcp
karsa-postgres      Up (healthy)        0.0.0.0:5432->5432/tcp
karsa-warp          Up                  0.0.0.0:1080->1080/tcp
karsa-crypto-bot    Up (healthy)        0.0.0.0:8444->8444/tcp
karsa-prometheus    Up                  0.0.0.0:9090->9090/tcp
karsa-grafana       Up                  0.0.0.0:3000->3000/tcp
karsa-alertmanager  Up                  0.0.0.0:9093->9093/tcp
```

---

## 🏗️ Crypto-Only Architecture Reference

### Component Inventory

| Layer | Component | File | Role | Status |
|-------|-----------|------|------|--------|
| **Ingress** | Crypto Bot HTTP | `src/crypto_bot.py` | HTTP server (port 8444), starts autonomous sessions | Core |
| **Ingress** | Bybit Data Client | `src/data/bybit_client.py` | REST/WS market data + execution | Core |
| **Core Loop** | Orchestrator | `src/orchestrator.py` | `run_crypto_scan()` — discovery → filter → agents → execute | Core |
| **Core Loop** | Autonomous Session | `src/execution/autonomous_session.py` | Session lifecycle, P&L tracking, Prometheus metrics | Core |
| **AI Pipeline** | Analyst Agent | `src/agents/analyst.py` | LLM market analysis via 9Router | Core |
| **AI Pipeline** | Trading Agent | `src/agents/trading_agent.py` | LLM trade decisions | Core |
| **AI Pipeline** | AI Judge | `src/execution/position_judge.py` | Position evaluation + hold/close decisions | Core |
| **Risk** | Risk Controls | `src/risk/risk_controls.py` | Pre-trade risk gates | Core |
| **Risk** | Circuit Breaker | `src/risk/circuit_breaker.py` | Consecutive loss detection → cooldown | Core |
| **Risk** | Emergency | `src/risk/emergency.py` | Kill switch, Telegram /kill integration | Core |
| **Execution** | Position Manager | `src/execution/position_manager.py` | Position CRUD, reconciliation | Core |
| **Execution** | Smart Order Router | `src/execution/sor.py` | Order routing, slippage tracking | Core |
| **Execution** | Trailing Stop | `src/execution/trailing_stop_manager.py` | Dynamic stop-loss management | Core |
| **Execution** | Performance Gate | `src/execution/performance_gate.py` | Zone-based entry/exit gating | Core |
| **Infrastructure** | Funding Tracker | `src/risk/funding_tracker.py` | Funding payment tracking | Core |
| **Infrastructure** | Trade Memory (RAG) | `src/memory/trade_memory.py` | Embedding + retrieval (requires `sentence-transformers`) | ⚠️ Degraded |
| **Scoring** | Universe Scorer | `src/scoring/universe_scorer.py` | Token ranking by momentum/volume | Conditional |
| **Scoring** | Candidate Filter | `src/scoring/candidate_filter.py` | Threshold filtering, short squeeze detection | Conditional |

### Data Flow

```
Market Data (Bybit WS/REST)
  → MarketIntelClient (OHLCV + funding)
    → UniverseScorer → CandidateFilter
      → AnalystAgent → TradingAgent → PositionManager
        → RiskManager → Telegram Approval
          → SmartOrderRouter → Bybit Execution
            → PositionMonitor → TrailingStopManager
```

Redis pub/sub bridges everything: `discovery_events`, `pending_approvals`, position state.

### Autonomous Session Lifecycle

`sessions/crypto_bot.py` runs autonomous trading sessions managed by APScheduler:

1. **Trigger**: APScheduler job fires every ~60s (configurable via `SCAN_INTERVAL`)
2. **Gate**: Checks kill switch + circuit breakers before proceeding
3. **Scan**: `Orchestrator.run_crypto_scan()` executes the full pipeline
4. **Execute**: Approved trades go through SOR → Bybit
5. **Complete**: Session returns P&L summary, increments `session_id`

Key constraints:
- **Only 1 session runs at a time** (`max_instances=1, coalesce=True`) — prevents connection pool exhaustion
- Jobs use `MemoryJobStore` — **do not survive container restarts**
- If a scan takes longer than the interval, the next trigger is coalesced (skipped)

### Position State Machine

Positions progress through these states, tracked in Redis (`karsa:positions:{symbol}`) and Postgres (`positions` table):

```
PENDING_APPROVAL → APPROVED → FILLED → TRAILING → PARTIAL_EXIT → CLOSED
       ↓                           ↓                              ↓
    REJECTED                    STOPPED_OUT                   EXPIRED
```

Each active position (`CryptoPosition`) includes:
- `entry_price`, `size`, `direction` (LONG/SHORT)
- `trailing_stop_pct`, `highest_price` (for trailing stop tracking)
- `partial_exits: List[PartialExit]` — history of scaled-out fills
- `funding_cost_accumulated` — running funding payment total
- `ai_judge_last_call` — timestamp of last AI Judge evaluation

### Risk Controls Cascade

Pre-trade gates in `CryptoRiskManager.evaluate()` — evaluated **in order**, first rejection wins:

| Gate | Check | Config | Default |
|------|-------|--------|---------|
| **0** | Signal validation | entry_price > 0, confidence ≥ threshold, direction ∈ {LONG, SHORT} | — |
| **1** | Daily loss limit | `CRYPTO_DAILY_LOSS_LIMIT_PCT` | 3% |
| **2** | Max concurrent positions | `CRYPTO_MAX_CONCURRENT_POSITIONS` | 5 |
| **3** | Duplicate ticker | Reject if position already open in same symbol | — |
| **3b** | Correlation limits | Static tiers + downside correlation check | tier1: 2 pos, 15% exposure |
| **4** | Cooldown | Redis `karsa:crypto_cooldown` key | Fail-closed if Redis down |
| **5** | Max position cap | `CRYPTO_MAX_POSITION_PCT` | 10% of equity |
| **6** | Minimum order size | Hardcoded $5 minimum | $5 |
| **7** | Funding rate | `CRYPTO_FUNDING_HARD_REJECT_PCT` + funding drag vs ATR target | 0.05%, 30% drag |
| **8** | Cost-aware risk gate | Total cost (fee + slippage + funding) vs ATR-based edge | — |

**Regime-adjusted sizing** (applied after Gate 4):
- `FULL_TREND_ALIGNMENT`: 1.0x size, min confidence 50
- `MACRO_BULL_MICRO_PULLBACK`: 0.8x size, min confidence 60
- `PURE_DEAD_CHOP`: 0.0x size (no trading)
- `MEAN_REVERSION`: 0.8x size, min confidence 65

**Circuit Breakers** (separate from pre-trade gates, checked every 1 min by `CircuitBreakerManager`):

| Breaker | Trigger | Severity | TTL |
|---------|---------|----------|-----|
| `DAILY_DD` | Realized P&L ≤ -`CRYPTO_DAILY_LOSS_LIMIT_PCT` | HALT | 30 min |
| `VOLATILITY:{ticker}` | >5% move in 15 min (BTC/ETH/SOL) | WARNING | 30 min |
| `CORRELATION` | >60% of tier positions losing | WARNING | 30 min |
| `MAX_DD` | Cumulative equity drawdown > `CRYPTO_MAX_EQUITY_DD_PCT` from peak | HALT | 30 min |

**Emergency Stop** (Redis-backed, survives restarts):
- `karsa:emergency_stop` — standard kill switch
- `karsa:global_halt` — OOB kill via `/kill` command, sets both keys
- Both trigger auto-flatten of all open positions

### Redis Key Schema

| Key | Type | Purpose |
|-----|------|---------|
| `karsa:global_halt` | string | Global kill switch (any value = active) |
| `karsa:emergency_stop` | string | Emergency stop (set by `/kill`) |
| `karsa:circuit_breaker:{strategy}` | hash | Per-strategy circuit breaker state |
| `karsa:daily_pnl:{date}` | string | Running daily P&L |
| `karsa:positions:{symbol}` | string (JSON) | Active position state (`CryptoPosition`) |
| `karsa:position_count` | string | Current open position count |

### Database Constraints

- `signals.direction` CHECK constraint: must be `LONG`, `SHORT`, or `CLOSE` (not BUY/SELL/HOLD/WATCH)
- `positions.partial_exits` is a `JSONB` column (list of `PartialExit` objects)
- `trade_memory` column requires `pgvector` extension (`pgvector/pgvector:pg15` image, not `postgres:15-alpine`)
- All scheduler jobs must have `max_instances=1, coalesce=True` to prevent connection pool exhaustion

### Key Metrics (Prometheus)

| Domain | Key Metrics | Source |
|--------|-------------|--------|
| AI Judge | `karsa_ai_judge_decisions_total`, `karsa_ai_judge_confidence_score` | `position_judge.py` |
| Regime | `karsa_crypto_regime`, `karsa_btc_dominance_pct` | `orchestrator.py` |
| Session | `karsa_session_return_pct`, `karsa_profit_factor` | `autonomous_session.py` |
| Position | `karsa_position_age_hours`, `karsa_funding_cost_8h_usd` | `autonomous_session.py` |
| Risk Safety | `karsa_kill_switch_active`, `karsa_circuit_breaker_active` | `emergency.py`, `circuit_breaker.py` |
| Order Execution | `karsa_order_fill_total`, `karsa_order_slippage_bps` | `sor.py` |

Metrics endpoint: `curl http://localhost:8444/metrics`

### Grafana Dashboards

- **Trading Operations v2** — full metrics dashboard
- **ASM - Core Operations** (`monitoring/asm-core-operations.json`) — 9-panel dashboard with live tables and AI Judge analytics
- Access: http://localhost:3000 (admin/admin)

### ⚠️ Known Gap: RAG Memory

`sentence-transformers` is **not** in the Dockerfile. Without it, `trade_memory.py` silently degrades to empty context — the embedding model never loads, and all similarity searches return nothing. To enable RAG:

```dockerfile
# Add to Dockerfile
RUN pip install sentence-transformers
```

---

## ✅ Phase 2: Validation

### 2.1 Infrastructure Health Checks

```bash
# Redis health
docker-compose exec redis redis-cli ping
# Expected: PONG

# Postgres health
docker-compose exec postgres psql -U karsa -c "SELECT 1;"
# Expected: 1 row returned

# WARP proxy connectivity
curl -x socks5://localhost:1080 https://api.bybit.com/v5/market/tickers?category=linear
# Expected: JSON response from Bybit
```

### 2.2 Crypto Bot Health Checks

```bash
# Health endpoint
curl -s http://localhost:8444/health | jq .
# Expected: {"status": "healthy", "version": "3.1", "uptime": ...}

# Metrics endpoint
curl -s http://localhost:8444/metrics | head -20
# Expected: Prometheus-formatted metrics

# Check open positions
curl -s http://localhost:8444/positions | jq .
# Expected: List of current positions (or empty array)

# Check portfolio summary
curl -s http://localhost:8444/portfolio | jq .
# Expected: Portfolio PnL, equity, drawdown metrics
```

### 2.3 Verify Disabled Services Are Not Running

```bash
# Check orchestrator is NOT running
curl -s http://localhost:8000/health
# Expected: Connection refused

# Check telegram bot is NOT running
curl -s http://localhost:8443/health
# Expected: Connection refused

# Verify no containers for disabled services
docker-compose ps | grep -E "orchestrator|telegram"
# Expected: No output
```

### 2.4 Prometheus Target Validation

```bash
# Check Prometheus targets
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, health: .health}'

# Expected output:
# {"job": "karsa-crypto-bot", "health": "up"}
# {"job": "prometheus", "health": "up"}
```

### 2.5 Crypto Bot Functional Validation

```bash
# Check recent logs for errors
docker-compose logs --tail=100 karsa-crypto-bot | grep -i "error\|exception\|critical"

# Expected: No critical errors

# Verify universe scanner is running
docker-compose logs --tail=50 karsa-crypto-bot | grep "universe_refresh"
# Expected: Recent universe refresh logs

# Verify risk gates are active
docker-compose logs --tail=50 karsa-crypto-bot | grep "risk_gate"
# Expected: Risk gate evaluation logs

# Check kill switch status
curl -s http://localhost:8444/kill-switch/status | jq .
# Expected: {"active": false, "last_triggered": null}
```

### 2.6 Database Integrity Check

```bash
# Connect to database
docker-compose exec postgres psql -U karsa karsa_trading

# Check open positions table
SELECT COUNT(*) FROM open_positions;
-- Expected: Matches API response from /positions

# Check recent closed trades
SELECT symbol, net_pnl, closed_at
FROM closed_paper_trades
ORDER BY closed_at DESC
LIMIT 10;
-- Expected: Recent trade history

# Check for state drift (positions in DB but not on exchange)
SELECT op.symbol, op.size, op.side
FROM open_positions op
WHERE op.symbol NOT IN (
  -- This would require a custom function to check exchange state
  -- For now, just verify the reconciler is running
  SELECT 'dummy'
);

# Exit psql
\q
```

### 2.7 Grafana Dashboard Validation

1. Open Grafana: `http://localhost:3000`
2. Login with admin credentials
3. Verify dashboards:
   - **Trading Operations v2**: Should show active crypto metrics
   - **ASM - Core Operations**: Should show AI Judge analytics and live position tables
   - **Portfolio PnL**: Should display crypto-only PnL
   - **Risk Metrics**: Should show drawdown, correlation, funding rates
   - **Universe Scorer**: Should show token rankings and scores

4. Check for broken panels:
   - Navigate to each dashboard
   - Look for "No data" or error messages
   - If found, update the panel queries to exclude orchestrator/telegram metrics

---

## 🧪 Phase 3: Integration Testing

### 3.1 Test Kill Switch Activation

```bash
# Trigger kill switch via API
curl -X POST http://localhost:8444/kill-switch/activate \
  -H "Content-Type: application/json" \
  -d '{"reason": "integration_test"}'

# Verify kill switch is active
curl -s http://localhost:8444/kill-switch/status | jq .
# Expected: {"active": true, "reason": "integration_test"}

# Check logs for kill switch execution
docker-compose logs --tail=20 karsa-crypto-bot | grep "kill_switch"
# Expected: Kill switch activation logs

# Deactivate kill switch
curl -X POST http://localhost:8444/kill-switch/deactivate

# Verify deactivated
curl -s http://localhost:8444/kill-switch/status | jq .
# Expected: {"active": false}
```

### 3.2 Test Universe Scanner

```bash
# Manually trigger universe refresh
curl -X POST http://localhost:8444/universe/refresh

# Check logs for universe refresh
docker-compose logs --tail=30 karsa-crypto-bot | grep "universe_refresh"
# Expected: Universe refresh completed, Top 50 tokens selected

# Verify top tokens in database
docker-compose exec postgres psql -U karsa karsa_trading -c \
  "SELECT symbol, score, price_change_24h, price_change_1h
   FROM universe_tokens
   ORDER BY score DESC
   LIMIT 10;"
```

### 3.3 Test Position Reconciliation

```bash
# Manually trigger reconciliation
curl -X POST http://localhost:8444/reconciliation/run

# Check logs for reconciliation
docker-compose logs --tail=30 karsa-crypto-bot | grep "reconciliation"
# Expected: Reconciliation completed, no drift detected (or drift corrected)
```

---

## 📊 Phase 4: Monitoring & Alerting Validation

### 4.1 Verify Prometheus Scraping

```bash
# Check Prometheus targets status
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, health: .health, lastScrape: .lastScrape}'

# Expected: All targets "up", lastScrape within last 30 seconds
```

### 4.2 Verify Alertmanager Configuration

```bash
# Check Alertmanager status
curl -s http://localhost:9093/api/v2/status | jq .

# Check active alerts
curl -s http://localhost:9093/api/v2/alerts | jq .
# Expected: Empty array or only non-critical alerts
```

### 4.3 Test Alert Firing (Optional)

```bash
# Manually fire a test alert
curl -X POST http://localhost:9093/api/v2/alerts \
  -H "Content-Type: application/json" \
  -d '[{
    "labels": {
      "alertname": "TestAlert",
      "severity": "warning"
    },
    "annotations": {
      "summary": "This is a test alert"
    }
  }]'

# Verify alert is received
curl -s http://localhost:9093/api/v2/alerts | jq '.[] | select(.labels.alertname == "TestAlert")'
# Expected: Test alert appears

# Wait for alert to resolve automatically or manually resolve
```

---

## 🔄 Phase 5: Rollback Plan

If issues arise, rollback to the previous state:

### 5.1 Restore Configuration Files

```bash
# Stop current stack
docker-compose down

# Restore backups
cp backups/$(date +%Y%m%d)/docker-compose.yml.bak docker-compose.yml
cp backups/$(date +%Y%m%d)/prometheus.yml.bak prometheus/prometheus.yml

# Restart full stack
docker-compose up -d

# Verify all services are running
docker-compose ps
```

### 5.2 Restore Database (If Needed)

```bash
# Stop crypto bot to prevent writes
docker-compose stop karsa-crypto-bot

# Restore database from backup
docker-compose exec -T postgres psql -U karsa karsa_trading < backups/$(date +%Y%m%d)/db_dump.sql

# Restart crypto bot
docker-compose start karsa-crypto-bot
```

---

## 📋 Success Criteria Checklist

- [ ] **Infrastructure**: Redis, Postgres, WARP all healthy
- [ ] **Crypto Bot**: Health endpoint returns 200, metrics endpoint active
- [ ] **Disabled Services**: Orchestrator and Telegram bot not running, ports 8000/8443 refuse connections
- [ ] **Prometheus**: Only scraping crypto-bot and self, no "down" targets
- [ ] **Grafana**: Dashboards displaying crypto metrics, no broken panels
- [ ] **Database**: Open positions match exchange state, no drift detected
- [ ] **Kill Switch**: Can be activated/deactivated via API
- [ ] **Universe Scanner**: Refreshing every 15 minutes, selecting Top 50 tokens
- [ ] **Reconciliation**: Running every 5 minutes, no state drift
- [ ] **Alerts**: Alertmanager receiving metrics, no false positives
- [ ] **Logs**: No critical errors in crypto bot logs

---

## 📝 Notes & Troubleshooting

### Common Issues

**Issue**: Crypto bot fails to start
- **Cause**: Database or Redis not ready
- **Fix**: Check `depends_on` conditions, verify healthchecks are passing

**Issue**: Prometheus shows "down" targets
- **Cause**: Old targets still in config
- **Fix**: Verify `prometheus.yml` has orchestrator/telegram targets commented out

**Issue**: Grafana dashboards show "No data"
- **Cause**: Panels querying orchestrator/telegram metrics
- **Fix**: Update panel queries to use crypto-bot metrics only

**Issue**: Kill switch activation fails
- **Cause**: Redis connection issue
- **Fix**: Check Redis health, verify `REDIS_URL` environment variable

**Issue**: Universe scanner not refreshing
- **Cause**: Bybit API rate limit or WARP proxy issue
- **Fix**: Check WARP connectivity, verify Bybit API credentials

**Issue**: RAG memory returning empty results
- **Cause**: `sentence-transformers` not installed in Docker image
- **Fix**: Add `RUN pip install sentence-transformers` to Dockerfile and rebuild

### Performance Monitoring

Monitor these key metrics in Grafana:
- **CPU/Memory usage**: Should decrease after disabling orchestrator/telegram
- **API rate limits**: Should have more headroom for crypto operations
- **Database connections**: Should remain stable (pool exhaustion fixed)
- **Universe refresh time**: Should complete within 30 seconds
- **Risk gate evaluation time**: Should complete within 5 seconds per token
- **AI Judge confidence**: Track `karsa_ai_judge_confidence_score` for decision quality
- **Funding costs**: Track `karsa_funding_cost_8h_usd` for position carry costs

---

## 🎯 Next Steps

After successful validation:

1. **Monitor for 24 hours**: Ensure no regressions or silent failures
2. **Review Grafana dashboards**: Identify any missing metrics or broken panels
3. **Optimize resource allocation**: Reduce CPU/memory limits for disabled services
4. **Update documentation**: Reflect the crypto-only architecture
5. **Plan Phase 1 deployment**: Begin implementing structural edge improvements

---

**Document Version**: 1.1
**Last Updated**: 2026-07-08
**Author**: Karsa System Architecture Team
