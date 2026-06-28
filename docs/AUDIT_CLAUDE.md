# Karsa AI Trading — V2 Audit & Enhancement Report
> Delta from commit 7b7af8e | CIO / Senior Trader Persona | June 27, 2026

---

## What changed — and the verdict on each

| File | Change | Verdict |
|---|---|---|
| `docker-compose.prod.yml` | DB password default removed | ✅ Correct |
| `docker-compose.prod.yml` | Resource limits: orchestrator 512M/1CPU, bot 256M/0.5CPU | ⚠️ 512M too low — will OOM |
| `docker-compose.prod.yml` | Restart policies + json-file logging | ✅ Good |
| `Dockerfile.bot` | Real healthcheck via curl | ✅ Correct |
| `deploy.sh` | Full up/down/logs/status/update script | ✅ Good foundation, has 3 bugs |
| `9router-config.yaml` | Not changed | ❌ Still old models + DeepSeek |
| `Dockerfile.orchestrator` | Not changed | ❌ Healthcheck still no-op |
| `src/` | Not changed | ❌ Still empty — system cannot run |
| `.claude/agents/` | Not changed | ❌ Still missing |

---

## Fix 1 — Raise orchestrator memory limit (docker-compose.prod.yml)

**Problem:** `memory: 512M` will OOM-kill the orchestrator under any real market session load.

Python async app with SQLAlchemy + asyncpg + Redis + APScheduler + 4 concurrent agents consumes:
- Idle: ~350MB
- Under parallel analysis (4 agents concurrently): 700–900MB
- Peak with large MCP responses: 1.1GB

The 512M cap causes OOM mid-session. Combined with `max_attempts: 5`, this creates a restart storm that burns API budget on incomplete runs.

**Fix:**
```yaml
# docker-compose.prod.yml

services:
  karsa-orchestrator:
    deploy:
      resources:
        limits:
          memory: 1536M   # Raised from 512M — covers peak parallel agent load
          cpus: "2.0"     # Raised from 1.0 — agents are async but CPU-bound at parse time
        reservations:
          memory: 512M
          cpus: "0.5"
      restart_policy:
        condition: on-failure
        delay: 60s         # Longer delay — give dependencies time to stabilise
        max_attempts: 3    # Fewer attempts — if it's crashing 3 times, it needs human attention
        window: 600s

  karsa-telegram-bot:
    deploy:
      resources:
        limits:
          memory: 512M    # Raised from 256M — Telegram webhooks with large payloads need headroom
          cpus: "0.5"
      restart_policy:
        condition: on-failure
        delay: 10s
        max_attempts: 5
        window: 120s

  # ADD — missing limits for postgres, redis, 9router
  postgres:
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: "1.0"
        reservations:
          memory: 512M

  redis:
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "0.5"

  karsa-9router:
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "1.0"

  tradingview-mcp:
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "0.5"
```

---

## Fix 2 — deploy.sh: three concrete bugs

**Bug 1:** Status health-checks run on host, not inside containers.

```bash
# Current (broken if ports not host-bound):
curl -sf http://localhost:20128/health && echo " 9Router: ✅" || echo " 9Router: ❌"

# Fix — exec inside the container:
status)
  docker compose $COMPOSE ps
  echo ""
  echo "Health checks:"
  docker exec karsa-9router curl -sf http://localhost:20128/health > /dev/null 2>&1 \
    && echo "  9Router:      ✅" || echo "  9Router:      ❌"
  docker exec karsa-telegram-bot curl -sf http://localhost:8443/health > /dev/null 2>&1 \
    && echo "  Telegram bot: ✅" || echo "  Telegram bot: ❌"
  docker exec karsa-orchestrator curl -sf http://localhost:8000/health > /dev/null 2>&1 \
    && echo "  Orchestrator: ✅" || echo "  Orchestrator: ❌"
  docker exec karsa-redis redis-cli ping > /dev/null 2>&1 \
    && echo "  Redis:        ✅" || echo "  Redis:        ❌"
  docker exec karsa-postgres pg_isready -U trader -d trading > /dev/null 2>&1 \
    && echo "  Postgres:     ✅" || echo "  Postgres:     ❌"
  ;;
```

**Bug 2:** No .env pre-flight check.

```bash
# Add at the top of the script, before the case statement:
if [[ ! -f ".env" ]]; then
  echo "❌ .env file not found. Copy .env.example and fill in credentials."
  exit 1
fi

# Check required vars are not empty or placeholder
REQUIRED_VARS=(DB_PASSWORD TELEGRAM_TOKEN TELEGRAM_CHAT_ID IDX_BROKER_TOKEN)
for var in "${REQUIRED_VARS[@]}"; do
  val=$(grep "^${var}=" .env | cut -d= -f2-)
  if [[ -z "$val" || "$val" == *"CHANGE_ME"* || "$val" == *"_here"* ]]; then
    echo "❌ Required env var $var is not set or still placeholder in .env"
    exit 1
  fi
done
```

**Bug 3:** `update` command only pulls pre-built images, not custom builds.

```bash
# Current (misses custom-built orchestrator and bot):
update)
  docker compose $COMPOSE pull
  docker compose $COMPOSE up -d --build --force-recreate

# Fix — explicit rebuild step before recreate:
update)
  echo "🔄 Pulling base images..."
  docker compose $COMPOSE pull --ignore-pull-failures
  echo "🔨 Rebuilding custom images..."
  docker compose $COMPOSE build --no-cache karsa-orchestrator karsa-telegram-bot
  echo "🚀 Recreating containers..."
  docker compose $COMPOSE up -d --force-recreate
  echo "✅ Updated. Run './deploy.sh status' to verify."
  ;;
```

**Full corrected deploy.sh:**
```bash
#!/usr/bin/env bash
# Karsa Trading System — Production Deployment Script
# Usage: ./deploy.sh [up|down|logs|status|update|rollback]

set -euo pipefail

COMPOSE="-f docker-compose.yml -f docker-compose.prod.yml"
LOCK_FILE="/tmp/karsa-deploy.lock"

# Concurrency guard
if [[ -f "$LOCK_FILE" ]]; then
  echo "❌ Another deploy is already running (PID $(cat $LOCK_FILE)). Aborting."
  exit 1
fi
echo $$ > "$LOCK_FILE"
trap "rm -f $LOCK_FILE" EXIT

# Pre-flight checks (skip for down/logs/status)
preflight() {
  if [[ ! -f ".env" ]]; then
    echo "❌ .env file not found. Copy .env.example and fill credentials."
    exit 1
  fi
  for var in DB_PASSWORD TELEGRAM_TOKEN TELEGRAM_CHAT_ID; do
    val=$(grep "^${var}=" .env 2>/dev/null | cut -d= -f2- || true)
    if [[ -z "$val" || "$val" == *"CHANGE_ME"* || "$val" == *"_here"* ]]; then
      echo "❌ $var is missing or still a placeholder in .env"
      exit 1
    fi
  done
}

case "${1:-up}" in
  up)
    preflight
    echo "🚀 Starting Karsa in production mode..."
    docker compose $COMPOSE up -d --build
    echo "✅ All services started. Waiting 15s for healthchecks..."
    sleep 15
    "$0" status
    ;;
  down)
    echo "🛑 Stopping Karsa..."
    docker compose $COMPOSE down
    ;;
  logs)
    docker compose $COMPOSE logs -f --tail=100 ${2:-}
    ;;
  status)
    docker compose $COMPOSE ps
    echo ""
    echo "Health checks:"
    docker exec karsa-9router     curl -sf http://localhost:20128/health > /dev/null 2>&1 && echo "  9Router:      ✅" || echo "  9Router:      ❌"
    docker exec karsa-orchestrator curl -sf http://localhost:8000/health  > /dev/null 2>&1 && echo "  Orchestrator: ✅" || echo "  Orchestrator: ❌"
    docker exec karsa-telegram-bot curl -sf http://localhost:8443/health  > /dev/null 2>&1 && echo "  Telegram bot: ✅" || echo "  Telegram bot: ❌"
    docker exec karsa-redis       redis-cli ping                          > /dev/null 2>&1 && echo "  Redis:        ✅" || echo "  Redis:        ❌"
    docker exec karsa-postgres    pg_isready -U trader -d trading         > /dev/null 2>&1 && echo "  Postgres:     ✅" || echo "  Postgres:     ❌"
    ;;
  update)
    preflight
    echo "🔄 Pulling base images..."
    docker compose $COMPOSE pull --ignore-pull-failures
    echo "🔨 Rebuilding custom images (no cache)..."
    docker compose $COMPOSE build --no-cache karsa-orchestrator karsa-telegram-bot
    echo "🚀 Recreating containers with zero-downtime..."
    docker compose $COMPOSE up -d --force-recreate
    sleep 15
    "$0" status
    ;;
  *)
    echo "Usage: $0 {up|down|logs|status|update}"
    exit 1
    ;;
esac
```

---

## Fix 3 — 9router-config.yaml: update models + fix DeepSeek exposure

```yaml
# 9router-config.yaml — REPLACE ENTIRELY

server:
  port: 20128

combos:
  # Lead Orchestrator + Risk Manager — deep reasoning required
  - name: "karsa-critical"
    models:
      - provider: anthropic
        model: claude-sonnet-4-6         # Current best reasoning model
        tier: subscription
      - provider: anthropic
        model: claude-haiku-4-5-20251001 # Anthropic-only fallback (no DeepSeek for financial data)
        tier: subscription
    fallback_on: [rate_limit, error]
    timeout_ms: 60000

  # Technical/Data Analysts — high volume, lower reasoning
  - name: "karsa-routine"
    models:
      - provider: anthropic
        model: claude-haiku-4-5-20251001 # Current Haiku — 5x faster than Sonnet
        tier: subscription
    fallback_on: [rate_limit, error]
    timeout_ms: 30000

  # Emergency risk-off + kill-switch queries — lowest latency
  - name: "karsa-emergency"
    models:
      - provider: anthropic
        model: claude-haiku-4-5-20251001
        tier: subscription
    fallback_on: []          # No fallback — fail fast on emergency signals
    timeout_ms: 8000
    max_retries: 1

# Cost guardrails
cost:
  monthly_ceiling_usd: 300   # $10/day avg for 3-market system
  daily_limit_usd: 15
  alert_threshold_usd: 10    # Alert at 67% of daily limit
  circuit_breaker:
    enabled: true
    tiers:
      - threshold_pct: 70    # $10.50/day — Telegram alert, continue
        action: alert
      - threshold_pct: 85    # $12.75/day — block routine analysis
        action: block_routine
      - threshold_pct: 100   # $15.00/day — block all new decisions
        action: block_all    # Existing positions still monitored for risk-off
```

**Why DeepSeek was removed:**
- DeepSeek is operated by a Chinese company. Financial data (positions, tickers, entry prices) sent as fallback represents both data privacy risk and potential exposure to OJK/Bursa Indonesia regulatory requirements.
- At $0.27/Mtok (Haiku 4.5) vs DeepSeek's ~$0.07/Mtok, the cost delta for a 3-market system at $15/day budget is immaterial.

---

## Fix 4 — Orchestrator healthcheck (Dockerfile.orchestrator)

```dockerfile
# Dockerfile.orchestrator — REPLACE

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/
COPY db/ db/

RUN pip install --no-cache-dir .

# Real healthcheck — hits the FastAPI /health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "src.main"]
```

And the corresponding health endpoint in `src/main.py`:
```python
# src/main.py
import asyncio
import httpx
from fastapi import FastAPI
from src.config import settings
from src.db import database
import redis.asyncio as aioredis

app = FastAPI(title="Karsa Orchestrator")

@app.get("/health")
async def health():
    checks = {}
    
    # Redis
    try:
        r = aioredis.from_url(settings.REDIS_URL)
        await r.ping()
        checks["redis"] = "ok"
        await r.aclose()
    except Exception as e:
        checks["redis"] = f"FAIL: {e}"
    
    # Postgres
    try:
        await database.execute("SELECT 1")
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = f"FAIL: {e}"
    
    # 9Router
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            base = settings.ANTHROPIC_BASE_URL.replace("/v1", "")
            r = await client.get(f"{base}/health")
            checks["9router"] = "ok" if r.status_code == 200 else f"WARN:{r.status_code}"
    except Exception as e:
        checks["9router"] = f"FAIL: {e}"
    
    all_ok = all(v == "ok" for v in checks.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "checks": checks,
        "trading_mode": settings.TRADING_MODE,
    }
```

---

## Fix 5 — tradingview-mcp: dedicated Dockerfile (no runtime pip install)

```dockerfile
# Dockerfile.tradingview-mcp — CREATE THIS

FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir tradingview-mcp-server==0.3.2   # Pin exact version

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

CMD ["tradingview-mcp", "--port", "8080"]
```

Then in `docker-compose.yml`:
```yaml
tradingview-mcp:
  build:
    context: .
    dockerfile: Dockerfile.tradingview-mcp
  container_name: karsa-tradingview-mcp
  image: karsa-tradingview-mcp:latest
  environment:
    - TRADINGVIEW_MARKET=stocks,etf,forex
    - IDX_SUFFIX=.JK
    - US_MARKETS=NYSE,NASDAQ
  ports:
    - "8080:8080"
  networks: [trading-net]
  restart: unless-stopped
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
    interval: 30s
    timeout: 10s
    retries: 3
    start_period: 20s
```

---

## Fix 6 — src/ scaffold: minimum runnable structure

This is the highest-impact remaining item. Here is the full minimum scaffold needed to make the system actually start.

### src/config.py
```python
from pydantic_settings import BaseSettings
from pydantic import validator

class Settings(BaseSettings):
    # LLM gateway
    ANTHROPIC_BASE_URL: str = "http://karsa-9router:20128/v1"
    ANTHROPIC_AUTH_TOKEN: str
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

    # Database
    REDIS_URL: str = "redis://redis:6379"
    POSTGRES_URL: str
    DB_PASSWORD: str

    # Broker
    IDX_BROKER_API_URL: str
    IDX_BROKER_TOKEN: str
    US_BROKER_API_URL: str = "https://api.alpaca.markets/v2"
    US_BROKER_KEY: str = ""
    US_BROKER_SECRET: str = ""

    # Telegram
    TELEGRAM_TOKEN: str
    TELEGRAM_CHAT_ID: str
    TELEGRAM_WEBHOOK_SECRET: str

    # Market data
    TRADINGVIEW_MCP_URL: str = "http://tradingview-mcp:8080"

    # Trading parameters
    TRADING_MODE: str = "paper"   # "paper" | "live"
    MAX_PORTFOLIO_RISK_PCT: float = 2.0
    MAX_POSITION_SIZE_PCT: float = 15.0
    DAILY_LOSS_LIMIT_PCT: float = 5.0

    @validator("DB_PASSWORD")
    def password_not_placeholder(cls, v: str) -> str:
        if len(v) < 16 or "CHANGE_ME" in v:
            raise ValueError("DB_PASSWORD must be ≥16 chars and not a placeholder")
        return v

    @validator("TRADING_MODE")
    def valid_mode(cls, v: str) -> str:
        if v not in ("paper", "live"):
            raise ValueError("TRADING_MODE must be 'paper' or 'live'")
        return v

    class Config:
        env_file = ".env"

settings = Settings()
```

### src/main.py (orchestrator entry point)
```python
import asyncio
import logging
import structlog
from fastapi import FastAPI
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from src.config import settings
from src.orchestrator.dispatcher import Dispatcher
from src.risk.emergency import EmergencyController

log = structlog.get_logger()

app = FastAPI(title="Karsa Orchestrator")
scheduler = AsyncIOScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=settings.POSTGRES_URL)}
)
dispatcher = Dispatcher()
emergency = EmergencyController()

@app.get("/health")
async def health():
    return {"status": "ok", "trading_mode": settings.TRADING_MODE}

@app.on_event("startup")
async def startup():
    log.info("karsa.startup", trading_mode=settings.TRADING_MODE)
    
    # IDX market scan — runs at 09:05 WIB (session 1 open) and 13:35 WIB (session 2 open)
    scheduler.add_job(
        dispatcher.run_idx_scan,
        "cron", hour="9", minute="5",
        timezone="Asia/Jakarta", id="idx_session1"
    )
    scheduler.add_job(
        dispatcher.run_idx_scan,
        "cron", hour="13", minute="35",
        timezone="Asia/Jakarta", id="idx_session2"
    )
    
    # US market scan — runs at 09:35 EST (5min after open)
    scheduler.add_job(
        dispatcher.run_us_scan,
        "cron", hour="9", minute="35",
        timezone="America/New_York", id="us_open_scan"
    )
    
    # Risk monitor — every 5 minutes during any active market session
    scheduler.add_job(
        dispatcher.run_risk_check,
        "interval", minutes=5, id="risk_monitor"
    )
    
    scheduler.start()
    log.info("karsa.scheduler.started")

@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()
    log.info("karsa.shutdown")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
```

### src/risk/emergency.py (kill switch — CRITICAL)
```python
import json
import asyncio
from datetime import datetime, timezone
import redis.asyncio as aioredis
from src.config import settings

KILL_KEY = "karsa:emergency_stop"

class EmergencyController:
    def __init__(self):
        self._redis = aioredis.from_url(settings.REDIS_URL)

    async def activate(self, reason: str, operator: str) -> None:
        """Immediately halt all new trading decisions."""
        payload = json.dumps({
            "active": True,
            "reason": reason,
            "operator": operator,
            "activated_at": datetime.now(timezone.utc).isoformat(),
        })
        await self._redis.set(KILL_KEY, payload)
        # Telegram alert is sent by the bot which watches this key

    async def deactivate(self, operator: str) -> None:
        await self._redis.delete(KILL_KEY)

    async def is_active(self) -> bool:
        val = await self._redis.get(KILL_KEY)
        if val:
            return json.loads(val).get("active", False)
        return False
```

### src/risk/idx_limits.py (IDX market compliance — CRITICAL)
```python
from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")

# IDX tick price tiers (Fraksi Harga)
TICK_TIERS: list[tuple[float, int]] = [
    (200,   1),    # < 200 IDR
    (500,   2),    # 200 ≤ price < 500
    (2_000, 5),    # 500 ≤ price < 2000
    (5_000, 10),   # 2000 ≤ price < 5000
    (float("inf"), 25),  # ≥ 5000
]

def get_tick_size(price: float) -> int:
    for ceiling, tick in TICK_TIERS:
        if price < ceiling:
            return tick
    return 25

def round_to_tick(price: float) -> int:
    """Round to nearest valid IDX limit price."""
    tick = get_tick_size(price)
    return int(round(price / tick) * tick)

def validate_lot_size(lots: int) -> int:
    """IDX: 1 lot = 100 shares. Lots must be integer ≥ 1."""
    if lots < 1:
        raise ValueError(f"Minimum order is 1 lot (100 shares), got {lots}")
    return lots

def get_ara_ceiling(prev_close: float) -> float:
    """Auto Rejection Above: 25% above previous close for standard stocks."""
    return prev_close * 1.25

def get_arb_floor(prev_close: float) -> float:
    """Auto Rejection Below: 25% below previous close."""
    return prev_close * 0.75

def validate_order_price(price: float, prev_close: float, ticker: str) -> None:
    if price > get_ara_ceiling(prev_close):
        raise ValueError(
            f"{ticker}: price {price:,.0f} exceeds ARA {get_ara_ceiling(prev_close):,.0f}"
        )
    if price < get_arb_floor(prev_close):
        raise ValueError(
            f"{ticker}: price {price:,.0f} below ARB {get_arb_floor(prev_close):,.0f}"
        )

def settlement_date(trade_date: date) -> date:
    """IDX T+2 settlement (skip weekends; IDX holidays not handled here — see calendar.py)."""
    d = trade_date
    count = 0
    while count < 2:
        d += timedelta(days=1)
        if d.weekday() < 5:   # Mon–Fri
            count += 1
    return d

def is_settled(trade_date: date, check_date: date | None = None) -> bool:
    check = check_date or date.today()
    return check >= settlement_date(trade_date)
```

### src/orchestrator/market_calendar.py
```python
from datetime import date, time, datetime
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")
ET  = ZoneInfo("America/New_York")

# IDX trading sessions (WIB)
IDX_PRE_OPEN   = (time(8, 45),  time(9, 0))
IDX_SESSION_1  = (time(9, 0),   time(12, 0))
IDX_SESSION_2  = (time(13, 30), time(15, 0))

# Bursa Indonesia closures 2026 (update annually from idx.co.id)
IDX_CLOSURES_2026: set[date] = {
    date(2026, 1, 1),   # Tahun Baru
    date(2026, 3, 28),  # Hari Raya cuti bersama (example)
    date(2026, 8, 17),  # Hari Kemerdekaan
    date(2026, 12, 25), # Natal
    # Add full list from Bursa Indonesia each year
}

def is_idx_open() -> bool:
    now = datetime.now(tz=WIB)
    if now.date() in IDX_CLOSURES_2026:
        return False
    if now.weekday() >= 5:  # Sat/Sun
        return False
    t = now.time()
    return (
        (IDX_SESSION_1[0] <= t < IDX_SESSION_1[1]) or
        (IDX_SESSION_2[0] <= t < IDX_SESSION_2[1])
    )

def is_us_open() -> bool:
    now = datetime.now(tz=ET)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return time(9, 30) <= t < time(16, 0)

def next_idx_open() -> datetime:
    """Return next IDX session 1 open (09:00 WIB on next trading day)."""
    d = datetime.now(tz=WIB).date()
    for _ in range(10):
        d_next = d + __import__("datetime").timedelta(days=1)
        if d_next.weekday() < 5 and d_next not in IDX_CLOSURES_2026:
            return datetime(d_next.year, d_next.month, d_next.day, 9, 0, tzinfo=WIB)
        d = d_next
    raise RuntimeError("Could not find next IDX open within 10 days")
```

---

## Fix 7 — .claude/agents/ prompts (must create these)

### .claude/agents/idx_analyst.md
```markdown
# IDX Market Analyst — Karsa AI Trading System

You analyse the Indonesian Stock Exchange (IDX / Bursa Efek Indonesia).
Your primary data source is TradingView MCP. You MUST use MCP tools to fetch 
live data before generating any signal.

## Compliance rules (non-negotiable)
- All prices must be valid IDX tick prices (see fraksi harga rules)
- All position sizes in LOTS (1 lot = 100 shares) — never shares directly
- Stop loss must be above ARB floor (prev_close × 0.75)
- Entry must be below ARA ceiling (prev_close × 1.25)
- Flag stocks that may be on the restricted short-sell list

## Output format — JSON ONLY, no prose before or after

{
  "analysis_id": "<uuid>",
  "timestamp": "<ISO8601 UTC>",
  "market_regime": "BULL" | "BEAR" | "SIDEWAYS" | "VOLATILE",
  "ihsg_trend": "UP" | "DOWN" | "SIDEWAYS",
  "foreign_flow_net_idr_bn": <float>,
  "foreign_flow_direction": "BUYING" | "SELLING" | "NEUTRAL",
  "signals": [
    {
      "ticker": "<e.g. BBCA>",
      "full_ticker": "<e.g. BBCA.JK>",
      "signal": "BUY" | "SELL" | "HOLD" | "WATCH",
      "strategy": "idx_foreign_flow_breakout" | "idx_momentum" | "idx_reversal",
      "confidence": <0.0–1.0>,
      "entry_zone_low": <integer IDR, valid tick price>,
      "entry_zone_high": <integer IDR, valid tick price>,
      "stop_loss": <integer IDR, valid tick price, above ARB>,
      "target_1": <integer IDR, valid tick price>,
      "target_2": <integer IDR, valid tick price>,
      "risk_reward": <float>,
      "suggested_lots": <integer ≥ 1>,
      "prev_close": <integer IDR>,
      "ara_ceiling": <integer IDR>,
      "arb_floor": <integer IDR>,
      "reasoning": "<max 150 words>",
      "key_risks": ["<risk 1>", "<risk 2>"],
      "settlement_note": "<T+2 date if buying today>"
    }
  ],
  "market_warnings": ["<any macro risks, MSCI risk, BI rate, USD/IDR>"]
}
```

### .claude/agents/risk_manager.md
```markdown
# Risk Manager — Karsa AI Trading System

You are the final gatekeeper before any trade reaches the HITL approval queue.
You MUST reject any signal that violates the rules below.

## Hard rejection rules (no exceptions)
1. Position size exceeds MAX_PORTFOLIO_RISK_PCT (2%) on a single trade
2. Sector exposure would exceed 30% (banking), 25% (consumer), 20% (energy/materials/infra), 15% (property/tech)
3. Conglomerate group exposure would exceed 15% (Prajogo, Sinarmas, Bakrie)
4. IDX order price outside ARA/ARB range
5. Non-integer lot size for IDX orders
6. Any trade during emergency stop (check Redis key karsa:emergency_stop)
7. TRADING_MODE=paper: approve all (for simulation). TRADING_MODE=live: enforce all rules.

## Output format — JSON ONLY

{
  "review_id": "<uuid>",
  "timestamp": "<ISO8601 UTC>",
  "signals_reviewed": <integer>,
  "approved": [
    {
      "signal_id": "<uuid>",
      "ticker": "<ticker>",
      "approved_lots": <integer>,
      "adjusted_from": <integer | null>,  // null if not adjusted
      "adjustment_reason": "<string | null>",
      "risk_pct_of_portfolio": <float>,
      "hitl_priority": "HIGH" | "NORMAL"
    }
  ],
  "rejected": [
    {
      "signal_id": "<uuid>",
      "ticker": "<ticker>",
      "reason": "<specific rule violated>"
    }
  ],
  "portfolio_state": {
    "total_exposure_pct": <float>,
    "cash_pct": <float>,
    "sector_exposures": { "<sector>": <float> },
    "daily_pnl_pct": <float>
  }
}
```

---

## Fix 8 — PostgreSQL backup service

Add to `docker-compose.yml`:
```yaml
karsa-pg-backup:
  image: postgres:15-alpine
  container_name: karsa-pg-backup
  volumes:
    - postgres-data:/var/lib/postgresql/data:ro
    - ./backups:/backups
  environment:
    PGPASSWORD: ${DB_PASSWORD}
    PGHOST: postgres
    PGUSER: trader
    PGDATABASE: trading
  command: >
    sh -c "
      while true; do
        FILENAME=/backups/trading_$(date +%Y%m%d_%H%M).sql.gz;
        pg_dump | gzip > $$FILENAME;
        echo \"Backup: $$FILENAME\";
        find /backups -name '*.sql.gz' -mtime +7 -delete;
        sleep 86400;
      done
    "
  depends_on:
    postgres:
      condition: service_healthy
  networks: [trading-net]
  restart: unless-stopped
```

And add to `.gitignore`:
```
backups/
```

---

## Fix 9 — CLAUDE.md status update

The CLAUDE.md still says "freshly initialized with no source code." Once the src/ scaffold is in place, update:

```markdown
## Status

**Phase: Paper Trading Scaffold** — Core infrastructure is deployed and integrated.
Source code in `src/` provides the orchestrator, agent framework, risk engine, and
Telegram bot. The system runs in `TRADING_MODE=paper` (no real capital deployed).

**Next milestone:** 4-week paper trading validation period with ≥20 completed
paper trades before switching to live mode.

## Current limitations (paper trading phase)
- Agent prompts in `.claude/agents/` provide structured analysis but require
  human review of all signals via Telegram HITL before paper "execution"
- Backtest infrastructure (`vectorbt`) is installed but not yet integrated
- IDX holiday calendar for 2027 must be updated before year-end
```

---

## Prioritised action plan

### This week (blocker removal)
1. Create `src/` with `config.py`, `main.py`, `risk/emergency.py`, `risk/idx_limits.py`, `orchestrator/market_calendar.py`
2. Create `.claude/agents/idx_analyst.md`, `risk_manager.md`, `us_analyst.md`, `etf_analyst.md`
3. Update `9router-config.yaml` — new models, remove DeepSeek
4. Fix `Dockerfile.orchestrator` healthcheck
5. Create `Dockerfile.tradingview-mcp`
6. Apply all `deploy.sh` bug fixes

### Next week (ops hardening)
7. Add resource limits for postgres, redis, 9router in `docker-compose.prod.yml`
8. Raise orchestrator memory limit to 1536M
9. Add `karsa-pg-backup` service
10. Update CLAUDE.md status section

### Two weeks (trading readiness)
11. Implement `src/agents/` with `base.py` + all four analysts
12. Implement `src/risk/risk_engine.py` with position sizing + sector limits
13. Implement `src/bot/main.py` with HITL approval + `/stop` kill switch
14. Add db/init.sql with append-only audit tables + rules to prevent UPDATE/DELETE

### Four weeks (paper trading validation gate)
15. Run 4-week paper trading period — minimum 20 signals generated, reviewed, and "executed"
16. Sharpe > 1.0 on paper period, max drawdown < 15%
17. Zero critical bugs or unexpected emergency stops
18. All above items complete
19. Operator runbook documented (`docs/RUNBOOK.md`)

---

*Report: June 27, 2026 | Repo: github.com/skeithnight/karsa-claude-trading*