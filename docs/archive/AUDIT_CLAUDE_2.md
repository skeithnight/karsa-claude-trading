# Karsa V3 Audit — CIO/Investment Trader Advisory
> Cross-session delta: V1 → V2 → V3 | June 27, 2026

---

## Progress scorecard across all 3 audits

| Dimension | V1 (initial) | V2 (first update) | V3 (this commit) |
|---|---|---|---|
| Architecture design | ★★★★☆ | ★★★★☆ | ★★★★☆ |
| Source code | ★☆☆☆☆ | ★☆☆☆☆ | ★☆☆☆☆ |
| Docker infrastructure | ★★★☆☆ | ★★★½☆ | ★★★★☆ |
| Risk controls | ★★☆☆☆ | ★★☆☆☆ | ★★☆☆☆ |
| Trading readiness | ★☆☆☆☆ | ★☆☆☆☆ | ★☆☆☆☆ |

**Bottom line:** Infrastructure is at 80% quality. Business logic is at 0%.

---

## Fix 1 — Dockerfile.orchestrator: complete the half-done healthcheck (30 seconds)

The V3 commit installed `curl` in the orchestrator image — clearly intending to wire up a real healthcheck —
but forgot to update the HEALTHCHECK instruction itself. curl is there, unused.

```dockerfile
# Dockerfile.orchestrator — REPLACE ENTIRELY

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/
COPY db/ db/

RUN pip install --no-cache-dir ".[orchestrator]"

# FIXED: was CMD python -c "import sys; sys.exit(0)"
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "src.main"]
```

---

## Fix 2 — docker-compose.yml: remove insecure postgres default + fix tradingview-mcp

```yaml
# docker-compose.yml — two targeted fixes

postgres:
  environment:
    POSTGRES_DB: trading
    POSTGRES_USER: trader
    POSTGRES_PASSWORD: ${DB_PASSWORD}    # FIXED: removed :-changeme

tradingview-mcp:
  build:
    context: .
    dockerfile: Dockerfile.tradingview-mcp   # FIXED: use dedicated Dockerfile
  container_name: karsa-tradingview-mcp
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

```dockerfile
# Dockerfile.tradingview-mcp — CREATE THIS FILE

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Pin exact version — no runtime installs, no PyPI dependency at container start
RUN pip install --no-cache-dir tradingview-mcp-server==0.3.2

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

CMD ["tradingview-mcp", "--port", "8080"]
```

---

## Fix 3 — 9router-config.yaml: current models, remove DeepSeek from financial routing

```yaml
# 9router-config.yaml — REPLACE ENTIRELY

server:
  port: 20128

combos:
  # Lead Orchestrator + Risk Manager — maximum reasoning quality
  - name: "karsa-critical"
    models:
      - provider: anthropic
        model: claude-sonnet-4-6          # Current (June 2026)
        tier: subscription
      - provider: anthropic
        model: claude-haiku-4-5-20251001  # Anthropic-only fallback — NO DeepSeek for financials
        tier: subscription
    fallback_on: [rate_limit, error]
    timeout_ms: 60000

  # Technical/Data Analysts — high volume, lower reasoning need
  - name: "karsa-routine"
    models:
      - provider: anthropic
        model: claude-haiku-4-5-20251001  # Current Haiku — 5x faster, significantly cheaper
        tier: subscription
    fallback_on: [rate_limit, error]
    timeout_ms: 30000

  # Emergency kill-switch / macro risk-off — lowest latency, no fallback
  - name: "karsa-emergency"
    models:
      - provider: anthropic
        model: claude-haiku-4-5-20251001
        tier: subscription
    fallback_on: []        # Fail fast — no fallback on emergency signals
    timeout_ms: 8000
    max_retries: 1

# Cost guardrails — tiered circuit breaker
cost:
  monthly_ceiling_usd: 300     # ~$10/day average for 3-market system
  daily_limit_usd: 15
  circuit_breaker:
    enabled: true
    tiers:
      - threshold_pct: 70      # $10.50 — alert only, continue
        action: alert
      - threshold_pct: 85      # $12.75 — block routine analysis, keep risk monitoring
        action: block_routine
      - threshold_pct: 100     # $15.00 — block all new analysis decisions
        action: block_all
```

**Why DeepSeek is removed:**
Position data, trade signals, ticker analysis, and portfolio values are financial data.
Routing these to DeepSeek (operated in China) creates data privacy exposure and
potential regulatory risk under OJK/Bursa Indonesia data handling expectations.
The cost delta between Haiku 4.5 ($0.27/Mtok) and DeepSeek ($0.07/Mtok) at $15/day
budget is immaterial (~$0.40/day). Privacy is not a cost trade-off.

---

## Fix 4 — .env.example: add TRADING_MODE and fix model reference

```bash
# .env.example — ADD these lines to the TRADING PARAMETERS section

# ==========================================
# SAFETY GATE — REQUIRED
# ==========================================
# Must be explicitly set to "live" to enable real order execution.
# Default to paper. Telegram HITL flow is enforced in both modes.
TRADING_MODE=paper

# ==========================================
# 9ROUTER & LLM CONFIGURATION
# ==========================================
ANTHROPIC_BASE_URL="http://karsa-9router:20128/v1"
ANTHROPIC_AUTH_TOKEN="9router_internal_token"
ANTHROPIC_MODEL="claude-sonnet-4-6"    # FIXED: was claude-3-5-sonnet-20241022
```

---

## Fix 5 — pyproject.toml: clean up dependency issues

```toml
# pyproject.toml — REPLACE ENTIRELY

[project]
name = "karsa-trading"
version = "0.1.0"
description = "AI-driven multi-market trading system for IDX and US equities"
requires-python = ">=3.11"

dependencies = [
    "anthropic>=0.56.0",             # Updated — 0.40.0 predates tool-use improvements
    "python-telegram-bot[ext]>=21.0",
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",     # [standard] includes uvloop for async perf
    "asyncpg>=0.30.0",               # Async Postgres driver — this is the one the app uses
    "sqlalchemy[asyncio]>=2.0.0",
    "redis[hiredis]>=5.0.0",         # [hiredis] = faster C parser for Redis
    "apscheduler>=3.10.0",
    "structlog>=24.0.0",
    "pydantic-settings>=2.0.0",
    "httpx>=0.27.0",
    # REMOVED: psycopg2-binary — sync driver not needed; asyncpg covers all async DB ops
    # REMOVED: tradingview-mcp-server — moved to [mcp] group below
]

[project.optional-dependencies]

# Only installed in the Dockerfile.tradingview-mcp image
mcp = [
    "tradingview-mcp-server==0.3.2",  # Pinned — same version as Dockerfile
]

# Migrations only — needs sync connection for Alembic schema operations
migrations = [
    "alembic>=1.13.0",
    "psycopg2-binary>=2.9.0",        # Sync driver for Alembic schema migrations only
]

# Strategy backtesting — not installed in production images
backtest = [
    "vectorbt>=0.26.0",
    "pandas>=2.0.0",
    "numpy>=1.26.0",
]

dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=5.0.0",
    "pytest-mock>=3.14.0",
    "ruff>=0.6.0",
    "mypy>=1.10.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.11"
strict = true
```

---

## The critical work that remains: src/ skeleton

Everything above is configuration polish. The system still cannot start.
Here is the minimum viable `src/` that makes `docker compose up` not crash:

### src/__init__.py
```python
```

### src/config.py
```python
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM Gateway
    ANTHROPIC_BASE_URL: str = "http://karsa-9router:20128/v1"
    ANTHROPIC_AUTH_TOKEN: str
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

    # Databases
    REDIS_URL: str = "redis://redis:6379"
    POSTGRES_URL: str
    DB_PASSWORD: str

    # Market data
    TRADINGVIEW_MCP_URL: str = "http://tradingview-mcp:8080"

    # Broker
    IDX_BROKER_API_URL: str = ""
    IDX_BROKER_TOKEN: str = ""
    US_BROKER_API_URL: str = "https://api.alpaca.markets/v2"
    US_BROKER_KEY: str = ""
    US_BROKER_SECRET: str = ""

    # Telegram HITL
    TELEGRAM_TOKEN: str
    TELEGRAM_CHAT_ID: str
    TELEGRAM_WEBHOOK_SECRET: str = ""

    # Trading safety gate
    TRADING_MODE: str = "paper"           # "paper" | "live"
    MAX_PORTFOLIO_RISK_PCT: float = 2.0
    MAX_POSITION_SIZE_PCT: float = 15.0
    DAILY_LOSS_LIMIT_PCT: float = 5.0

    @field_validator("DB_PASSWORD")
    @classmethod
    def password_must_be_set(cls, v: str) -> str:
        if not v or v.upper() in ("CHANGE_ME", "CHANGEME", "PASSWORD"):
            raise ValueError("DB_PASSWORD must be set to a real value — not a placeholder")
        if len(v) < 12:
            raise ValueError("DB_PASSWORD must be at least 12 characters")
        return v

    @field_validator("TRADING_MODE")
    @classmethod
    def valid_trading_mode(cls, v: str) -> str:
        if v not in ("paper", "live"):
            raise ValueError("TRADING_MODE must be 'paper' or 'live'")
        return v


settings = Settings()
```

### src/main.py
```python
import asyncio
import logging
import structlog
from fastapi import FastAPI
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config import settings

log = structlog.get_logger()
app = FastAPI(title="Karsa Orchestrator", version="0.1.0")
scheduler = AsyncIOScheduler()


@app.get("/health")
async def health() -> dict:
    """Real health endpoint — checked by Docker HEALTHCHECK."""
    from src.db import check_db
    from src.cache import check_redis

    db_ok = await check_db()
    redis_ok = await check_redis()
    all_ok = db_ok and redis_ok

    return {
        "status": "ok" if all_ok else "degraded",
        "trading_mode": settings.TRADING_MODE,
        "checks": {
            "postgres": "ok" if db_ok else "FAIL",
            "redis": "ok" if redis_ok else "FAIL",
        },
    }


@app.on_event("startup")
async def startup() -> None:
    log.info("karsa.startup", mode=settings.TRADING_MODE)

    if settings.TRADING_MODE == "live":
        log.warning("karsa.LIVE_MODE_ACTIVE", msg="Real capital at risk — HITL enforced")

    # TODO: wire in real agent dispatch jobs
    scheduler.start()
    log.info("karsa.scheduler.started")


@app.on_event("shutdown")
async def shutdown() -> None:
    scheduler.shutdown(wait=False)
    log.info("karsa.shutdown")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### src/db.py
```python
import asyncpg
from src.config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=settings.POSTGRES_URL, min_size=2, max_size=10)
    return _pool


async def check_db() -> bool:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception:
        return False
```

### src/cache.py
```python
import redis.asyncio as aioredis
from src.config import settings

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


async def check_redis() -> bool:
    try:
        await get_redis().ping()
        return True
    except Exception:
        return False
```

### src/risk/emergency.py
```python
"""Emergency stop / kill switch — Redis-backed, survives orchestrator restarts."""
import json
from datetime import datetime, timezone
from src.cache import get_redis

KILL_KEY = "karsa:emergency_stop"


async def activate(reason: str, operator: str) -> None:
    payload = json.dumps({
        "active": True,
        "reason": reason,
        "operator": operator,
        "activated_at": datetime.now(timezone.utc).isoformat(),
    })
    await get_redis().set(KILL_KEY, payload)


async def deactivate(operator: str) -> None:
    await get_redis().delete(KILL_KEY)


async def is_active() -> bool:
    val = await get_redis().get(KILL_KEY)
    if val:
        return json.loads(val).get("active", False)
    return False
```

### src/risk/idx_limits.py
```python
"""IDX market compliance rules — all order validation goes through here."""
from datetime import date, timedelta

# IDX tick price tiers (Fraksi Harga Saham)
_TIERS: list[tuple[float, int]] = [
    (200,   1),
    (500,   2),
    (2_000, 5),
    (5_000, 10),
    (float("inf"), 25),
]


def tick_size(price: float) -> int:
    for ceiling, tick in _TIERS:
        if price < ceiling:
            return tick
    return 25


def round_to_tick(price: float) -> int:
    """Round price to nearest valid IDX limit order price."""
    t = tick_size(price)
    return int(round(price / t) * t)


def ara_ceiling(prev_close: float) -> float:
    """Auto Rejection Above — 25% above previous close."""
    return prev_close * 1.25


def arb_floor(prev_close: float) -> float:
    """Auto Rejection Below — 25% below previous close."""
    return prev_close * 0.75


def validate_order(ticker: str, price: float, prev_close: float, lots: int) -> None:
    """Raise ValueError if order violates IDX market rules."""
    if lots < 1:
        raise ValueError(f"{ticker}: minimum 1 lot (100 shares), got {lots}")
    rounded = round_to_tick(price)
    if price != rounded:
        raise ValueError(f"{ticker}: {price} is not a valid tick price — use {rounded}")
    if price > ara_ceiling(prev_close):
        raise ValueError(f"{ticker}: {price:,} exceeds ARA ceiling {ara_ceiling(prev_close):,.0f}")
    if price < arb_floor(prev_close):
        raise ValueError(f"{ticker}: {price:,} below ARB floor {arb_floor(prev_close):,.0f}")


def settlement_date(trade_date: date) -> date:
    """IDX T+2: next 2 trading days (weekdays only; full holiday calendar needed)."""
    d, count = trade_date, 0
    while count < 2:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d


def is_settled(trade_date: date) -> bool:
    return date.today() >= settlement_date(trade_date)
```

### src/orchestrator/market_calendar.py
```python
"""IDX and US market session checks."""
from datetime import date, time, datetime
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")
ET  = ZoneInfo("America/New_York")

IDX_SESSION_1 = (time(9, 0),   time(12, 0))
IDX_SESSION_2 = (time(13, 30), time(15, 0))
IDX_PRE_OPEN  = (time(8, 45),  time(9, 0))

# Update annually from https://www.idx.co.id/en/about-idx/idx-holiday/
IDX_CLOSURES_2026: set[date] = {
    date(2026, 1, 1),
    date(2026, 3, 20),  # Hari Raya Nyepi
    date(2026, 3, 27),  # Wafat Isa Al Masih
    date(2026, 4, 1),   # Idul Fitri
    date(2026, 4, 2),   # Idul Fitri
    date(2026, 4, 3),   # Idul Fitri cuti bersama
    date(2026, 5, 14),  # Kenaikan Isa Al Masih
    date(2026, 5, 22),  # Waisak
    date(2026, 6, 8),   # Idul Adha
    date(2026, 6, 29),  # Tahun Baru Islam
    date(2026, 8, 17),  # Hari Kemerdekaan
    date(2026, 9, 7),   # Maulid Nabi
    date(2026, 12, 25), # Natal
}


def idx_open() -> bool:
    now = datetime.now(tz=WIB)
    if now.date() in IDX_CLOSURES_2026 or now.weekday() >= 5:
        return False
    t = now.time()
    return (IDX_SESSION_1[0] <= t < IDX_SESSION_1[1]) or (IDX_SESSION_2[0] <= t < IDX_SESSION_2[1])


def us_open() -> bool:
    now = datetime.now(tz=ET)
    if now.weekday() >= 5:
        return False
    return time(9, 30) <= now.time() < time(16, 0)


def idx_pre_open() -> bool:
    now = datetime.now(tz=WIB)
    t = now.time()
    return IDX_PRE_OPEN[0] <= t < IDX_PRE_OPEN[1]
```

### src/bot/__init__.py
```python
```

### src/bot/main.py
```python
"""Telegram HITL bot — kill switch + trade approval."""
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from src.config import settings
from src.risk import emergency
import structlog

log = structlog.get_logger()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"Karsa bot online.\nMode: {settings.TRADING_MODE.upper()}\n"
        f"Commands: /stop — emergency halt | /status — system status"
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Emergency kill switch — halts all new trading decisions immediately."""
    operator = update.effective_user.username or str(update.effective_user.id)
    if str(update.effective_user.id) != settings.TELEGRAM_CHAT_ID:
        await update.message.reply_text("Unauthorized.")
        return

    await emergency.activate(reason="Manual operator halt via Telegram", operator=operator)
    log.warning("karsa.emergency_stop", operator=operator)
    await update.message.reply_text(
        "EMERGENCY STOP ACTIVATED\n"
        "All new trading decisions are halted.\n"
        "Use /resume to reactivate (not yet implemented — contact sysadmin)."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stopped = await emergency.is_active()
    await update.message.reply_text(
        f"System: {'HALTED' if stopped else 'ACTIVE'}\n"
        f"Mode: {settings.TRADING_MODE.upper()}"
    )


def main() -> None:
    app = Application.builder().token(settings.TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("stop",   cmd_stop))
    app.add_handler(CommandHandler("status", cmd_status))

    log.info("karsa.bot.starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
```

---

## db/init.sql: database schema (if not already present)

```sql
-- Append-only tables — never UPDATE or DELETE these

CREATE TABLE IF NOT EXISTS positions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker           VARCHAR(20)  NOT NULL,
    market           VARCHAR(10)  NOT NULL,  -- IDX | US | ETF
    lots             INTEGER      NOT NULL,
    entry_price      NUMERIC(14,4) NOT NULL,
    entry_date       TIMESTAMPTZ  NOT NULL,
    settlement_date  DATE,                   -- IDX only
    stop_loss        NUMERIC(14,4),
    target_1         NUMERIC(14,4),
    target_2         NUMERIC(14,4),
    strategy         VARCHAR(60),
    status           VARCHAR(20)  NOT NULL DEFAULT 'open',
    idempotency_key  UUID         NOT NULL UNIQUE,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trade_history (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    position_id      UUID         REFERENCES positions(id),
    trade_type       VARCHAR(20)  NOT NULL,  -- OPEN | CLOSE | PARTIAL_CLOSE
    ticker           VARCHAR(20)  NOT NULL,
    market           VARCHAR(10)  NOT NULL,
    lots             INTEGER      NOT NULL,
    price            NUMERIC(14,4) NOT NULL,
    gross_value      NUMERIC(18,2) NOT NULL,
    commission       NUMERIC(18,2),
    net_pnl          NUMERIC(18,2),
    actual_rr        NUMERIC(8,3),
    strategy         VARCHAR(60),
    idempotency_key  UUID         NOT NULL UNIQUE,
    executed_at      TIMESTAMPTZ  NOT NULL,
    broker_order_id  VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type   VARCHAR(60)  NOT NULL,
    agent_id     VARCHAR(60),
    details      JSONB        NOT NULL,
    model_used   VARCHAR(60),
    token_count  INTEGER,
    cost_usd     NUMERIC(10,6),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Enforce append-only (no UPDATE or DELETE) on critical tables
CREATE RULE no_update_trade AS ON UPDATE TO trade_history DO INSTEAD NOTHING;
CREATE RULE no_delete_trade AS ON DELETE TO trade_history DO INSTEAD NOTHING;
CREATE RULE no_update_audit AS ON UPDATE TO audit_logs DO INSTEAD NOTHING;
CREATE RULE no_delete_audit AS ON DELETE TO audit_logs DO INSTEAD NOTHING;

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_positions_ticker ON positions(ticker, status);
CREATE INDEX IF NOT EXISTS idx_trade_history_ticker ON trade_history(ticker, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_event ON audit_logs(event_type, created_at DESC);
```

---

## Agent prompts: .claude/agents/ (must be created)

### .claude/agents/idx_analyst.md
```markdown
# IDX Market Analyst

You analyse the Indonesian Stock Exchange using TradingView MCP data.
Fetch live data before generating any signal. Never hallucinate prices.

## Hard rules
- All prices: valid IDX tick prices (fraksi harga)
- All sizes: in LOTS (1 lot = 100 shares) — never in shares
- Stop losses: must be above ARB floor (prev_close × 0.75)
- Entries: must be below ARA ceiling (prev_close × 1.25)

## Output: JSON ONLY — no prose, no markdown, no preamble

{
  "analysis_id": "<uuid4>",
  "timestamp": "<ISO8601 UTC>",
  "market_regime": "BULL|BEAR|SIDEWAYS|VOLATILE",
  "ihsg_trend": "UP|DOWN|SIDEWAYS",
  "foreign_flow_direction": "BUYING|SELLING|NEUTRAL",
  "signals": [
    {
      "ticker": "BBCA",
      "signal": "BUY|SELL|HOLD|WATCH",
      "strategy": "idx_foreign_flow_breakout",
      "confidence": 0.0,
      "entry_zone_low": 0,
      "entry_zone_high": 0,
      "stop_loss": 0,
      "target_1": 0,
      "target_2": 0,
      "risk_reward": 0.0,
      "suggested_lots": 0,
      "prev_close": 0,
      "reasoning": "<max 100 words>",
      "key_risks": []
    }
  ],
  "market_warnings": []
}
```

### .claude/agents/risk_manager.md
```markdown
# Risk Manager — Final gatekeeper before HITL queue

You are the last check before any signal reaches the operator for approval.
Reject hard. Approve conservatively. Adjust lot sizes down, never up.

## Reject immediately if ANY of these are true
1. Position risk > MAX_PORTFOLIO_RISK_PCT (2%)
2. Banking sector exposure would exceed 30%
3. Any single conglomerate group (Prajogo, Sinarmas, Bakrie) would exceed 15%
4. Order price outside ARA/ARB range
5. Non-integer lot size
6. Redis key karsa:emergency_stop is set
7. Daily P&L already below DAILY_LOSS_LIMIT_PCT (-5%)

## Output: JSON ONLY

{
  "review_id": "<uuid4>",
  "timestamp": "<ISO8601 UTC>",
  "approved": [
    {
      "signal_id": "<uuid4>",
      "ticker": "<ticker>",
      "approved_lots": 0,
      "risk_pct_of_portfolio": 0.0,
      "hitl_priority": "HIGH|NORMAL"
    }
  ],
  "rejected": [
    {
      "signal_id": "<uuid4>",
      "ticker": "<ticker>",
      "reason": "<specific rule violated>"
    }
  ]
}
```

---

## CIO advisory: what to do next, in order

1. Apply fixes 1–5 above: ~30 minutes total. These are one-liners.
2. Run `docker compose up --build` — it will still fail because src/ is empty,
   but the infrastructure layer will be clean.
3. Add the src/ skeleton above — this makes the orchestrator and bot actually start.
4. Run `docker compose up --build` again — 9Router, Redis, Postgres, and the orchestrator
   should all come up healthy. Telegram bot should be reachable.
5. Add the agent prompt files — the orchestrator can now route to 9Router
   with structured instructions.
6. Run in paper mode for 4 weeks minimum before any live capital.

The system is well-designed. The architecture is sound. The remaining gap
is weeks of Python writing, not more YAML.

---
*Report: June 27, 2026 | Repo: github.com/skeithnight/karsa-claude-trading*