# Plan: Karsa V3 Audit — Implementation

**Source**: `docs/AUDIT_CLAUDE_2.md`
**Selected Milestone**: All infrastructure fixes + missing risk module + integration
**Complexity**: Medium-Large

## Summary

The audit correctly identifies "80% infrastructure, 0% business logic." The 5 config/Docker fixes are real. The proposed `src/` skeleton would delete working code — but much of that code is stubs. Plan: apply infrastructure fixes, add risk module, wire it into the real gaps (4 empty scheduler jobs, kill switch that doesn't kill, orchestrator with zero risk checks), and reject only the audit proposals where existing code is genuinely better.

## Honest codebase assessment

| Component | Audit says | Reality |
|---|---|---|
| `src/main.py` scheduler | "Replace with 35-line FastAPI" | Has 9 jobs — but **4 are empty stubs**, kill switch detects but doesn't act |
| `src/bot/handlers.py` | "Replace with 3-cmd bot" | Genuinely rich — 14 commands, inline keyboards, formatted tables. Keep. |
| `src/models/database.py` | "Replace with raw asyncpg" | SQLAlchemy async is correct. Keep. |
| `src/data/cache.py` | "Replace with standalone functions" | CacheManager has pub/sub + portfolio + OHLCV. Keep. |
| `db/init.sql` | "Replace with 3-table schema" | 8 tables, more complete. But **missing append-only rules** — add them. |
| Health server | "Use FastAPI on port 8000" | `threading.Thread` + `http.server` hack on 8080. FastAPI is better. |
| `src/data/mcp_client.py` | "Add tradingview-mcp container" | Uses `tradingview_ta` directly, not MCP. MCP server is unnecessary. |

## What the audit gets right

| Item | Current state | Fix |
|---|---|---|
| Fix 1: Dockerfile healthcheck | Dummy `python -c "sys.exit(0)"` | Real curl healthcheck |
| Fix 2: Postgres password | `${DB_PASSWORD:-changeme}` insecure | `${DB_PASSWORD}` required |
| Fix 3: 9router models | Outdated claude-3-5-sonnet + DeepSeek | Current models, Anthropic-only |
| Fix 4: .env model reference | `claude-3-5-sonnet-20241022` | `claude-sonnet-4-6` |
| Fix 5: pyproject deps | psycopg2-binary in main deps | Split optional groups |
| Missing: Risk module | `src/risk/` doesn't exist | Emergency stop + IDX limits |
| Missing: Kill switch integration | Detects breach, doesn't act | Wire into Redis + Telegram |
| Missing: Append-only DB rules | No protection on audit_logs/trades | CREATE RULE |

## What the audit gets wrong (reject)

| Audit proposal | Why reject |
|---|---|
| Replace `src/bot/main.py` + `handlers.py` | 14 real commands with inline keyboards, formatted tables, portfolio management. Severe regression. |
| Replace `src/models/database.py` | SQLAlchemy async engine + session factory is correct architecture. |
| Replace `src/data/cache.py` | CacheManager has pub/sub, portfolio cache, OHLCV cache — superset of audit proposal. |
| Replace `db/init.sql` with 3-table schema | Existing 8-table schema covers paper trading, HITL, OHLCV, cash balance. Add append-only rules instead. |
| Add tradingview-mcp container | `src/data/mcp_client.py` uses `tradingview_ta` directly. MCP server adds unnecessary complexity. |
| Add `src/orchestrator/market_calendar.py` | `src/utils/market_hours.py` already has `is_idx_open()` and `is_us_open()`. |

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| Config | `src/config.py:11` | `Field(default=..., alias="9ROUTER_*")` with `populate_by_name=True` |
| Logging | `src/utils/logging.py` | `get_logger("module_name")` via structlog |
| Error handling | `src/agents/base.py` | try/except with `logger.error("context", error=str(e))` |
| DB access | `src/models/database.py:35` | `async with async_session() as session:` context manager |
| Redis | `src/data/cache.py:30` | `self._key(*parts)` prefix pattern, `setex` with TTL |
| Agent loop | `src/agents/base.py:47` | Anthropic SDK tool-use loop with `combo_name` routing |

## Files to Change

| File | Action | Why |
|---|---|---|
| `Dockerfile.orchestrator` | UPDATE | Fix 1: real healthcheck |
| `docker-compose.yml` | UPDATE | Fix 2: remove :-changeme, update healthcheck port |
| `9router-config.yaml` | UPDATE | Fix 3: current models, remove DeepSeek, add cost tiers |
| `.env.example` | UPDATE | Fix 4: update model reference |
| `pyproject.toml` | UPDATE | Fix 5: clean deps, split optional groups |
| `src/config.py` | UPDATE | Add TRADING_MODE + DB_PASSWORD validator |
| `src/main.py` | UPDATE | Replace threading health server with FastAPI, integrate emergency stop into kill switch job |
| `src/risk/__init__.py` | CREATE | Module init |
| `src/risk/emergency.py` | CREATE | Redis-backed emergency stop |
| `src/risk/idx_limits.py` | CREATE | IDX tick sizes, ARA/ARB, order validation |
| `src/agents/orchestrator.py` | UPDATE | Add emergency stop gate + signal validation |
| `db/init.sql` | UPDATE | Add append-only rules for audit_logs + closed_paper_trades |
| `.claude/agents/idx_analyst.md` | CREATE | IDX analyst system prompt |
| `.claude/agents/risk_manager.md` | CREATE | Risk manager system prompt |

## Tasks

### Task 1: Fix 1 — Dockerfile.orchestrator healthcheck
- **Action**: Replace `HEALTHCHECK` line 13 with `curl -f http://localhost:8000/health || exit 1` (port 8000 — matches FastAPI migration in Task 7)
- **Mirror**: `Dockerfile.bot:12` already uses curl healthcheck
- **Validate**: `docker build -f Dockerfile.orchestrator .` succeeds

### Task 2: Fix 2 — docker-compose.yml postgres password
- **Action**: Line 34: `${DB_PASSWORD:-changeme}` → `${DB_PASSWORD}`. Line 57: same for POSTGRES_URL. Add `DB_PASSWORD` to required env vars documentation.
- **Validate**: `docker compose config` fails without DB_PASSWORD set (correct — forces explicit value)

### Task 3: Fix 3 — 9router-config.yaml
- **Action**: Replace entire file. claude-sonnet-4-6 for critical, haiku-4-5 for routine. Remove all DeepSeek. Add karsa-emergency combo (8s timeout, no fallback). Add 3-tier cost circuit breaker (70% alert, 85% block_routine, 100% block_all).
- **Validate**: No `deepseek` or `claude-3` references in file

### Task 4: Fix 4 — .env.example model reference
- **Action**: Line 6: `claude-3-5-sonnet-20241022` → `claude-sonnet-4-6`. Also update `9ROUTER_MODEL` line 10 if present.
- **Validate**: `grep "claude-3" .env.example` returns nothing

### Task 5: Fix 5 — pyproject.toml cleanup
- **Action**: Bump `anthropic>=0.56.0`. Add `[standard]` to uvicorn, `[hiredis]` to redis. Move `psycopg2-binary` to `[migrations]` optional. Remove `tradingview-mcp-server` from main deps (code uses `tradingview_ta` directly, not MCP). Add dev tools (ruff, mypy, pytest-mock).
- **Validate**: `pip install -e .` succeeds without psycopg2 or tradingview-mcp-server

### Task 6: Config — add TRADING_MODE + DB_PASSWORD validator
- **Action**: Add to `src/config.py`:
  - `TRADING_MODE: str = "paper"` field
  - `@field_validator("DB_PASSWORD")` — reject placeholders (CHANGE_ME, CHANGEME, PASSWORD), enforce ≥12 chars
  - `@field_validator("TRADING_MODE")` — must be "paper" or "live"
  - Import `field_validator` from pydantic
- **Mirror**: Existing Field pattern in `src/config.py`
- **BREAKING**: Existing `.env` has `DB_PASSWORD=trader_pass` (11 chars). Update `.env` to ≥12 chars simultaneously.
- **Validate**: `python -c "from src.config import settings; print(settings.TRADING_MODE)"` prints "paper"

### Task 7: Health server — migrate from threading to FastAPI
- **Action**: In `src/main.py`:
  - Import FastAPI + uvicorn
  - Create `app = FastAPI(title="Karsa Orchestrator")`
  - Move `/health` endpoint to FastAPI route (include DB + Redis checks like current `/health` handler)
  - Move `/health/scheduler` to FastAPI route
  - Remove `threading.Thread` + `HTTPServer` block (lines 74-118)
  - Run uvicorn in a background task from the async run loop, or run FastAPI as the main server with APScheduler attached
- **Approach**: Keep `KarsaApp` class structure. Add FastAPI app as attribute. Use `uvicorn.Server` programmatically in background task so APScheduler + signal handlers still work.
- **Mirror**: Existing FastAPI usage in `src/bot/main.py`
- **Validate**: `curl http://localhost:8000/health` returns `{"status": "ok"}`

### Task 8: Create risk module — emergency stop
- **Action**: Create `src/risk/__init__.py` and `src/risk/emergency.py`:
  - `activate(reason, operator)` — set `karsa:emergency_stop` key in Redis with JSON payload
  - `deactivate(operator)` — delete key
  - `is_active()` — check key, return bool
  - Standalone redis client (not CacheManager) — works from any process
- **Mirror**: `src/data/cache.py` redis connection pattern
- **Validate**: `python -c "from src.risk import emergency"` imports without error

### Task 9: Create risk module — IDX limits
- **Action**: Create `src/risk/idx_limits.py`:
  - `_TIERS` — IDX Fraksi Harga tick size table
  - `tick_size(price)` — returns tick size for price level
  - `round_to_tick(price)` — rounds to nearest valid IDX price
  - `ara_ceiling(prev_close)` / `arb_floor(prev_close)` — 25% auto rejection bounds
  - `validate_order(ticker, price, prev_close, lots)` — raises ValueError on violation
  - `settlement_date(trade_date)` / `is_settled(trade_date)` — T+2 settlement
- **Note**: Audit code is correct. Copy as-is.
- **Validate**: `python -c "from src.risk.idx_limits import tick_size; assert tick_size(9500) == 50; assert tick_size(200) == 1"` passes

### Task 10: Wire emergency stop into kill switch job
- **Action**: In `src/main.py` `_job_kill_switch()` (line 292):
  - Import `src.risk.emergency`
  - When `daily_pnl_pct <= -1.5`: call `await emergency.activate(reason=f"Daily loss {daily_pnl_pct}%", operator="system")`
  - Send Telegram alert via bot token (import httpx, POST to Telegram API)
- **Mirror**: Existing error handling pattern in `src/main.py`
- **Validate**: Manually trigger with mock P&L → verify Redis key `karsa:emergency_stop` is set

### Task 11: Wire emergency stop into orchestrator
- **Action**: In `src/agents/orchestrator.py`:
  - In `scan_all_markets()` (line 44): check `emergency.is_active()` at top, return empty list if active
  - In `scan_single()` (line 110): same check — ad-hoc Telegram scans should also be blocked
  - In `_scan_market()` (line 72): check before each ticker iteration (allows partial completion if stop activated mid-scan)
  - Log `emergency_stop_blocked` when blocked
- **Mirror**: Existing logger pattern
- **Validate**: Set Redis key manually, verify `scan_all_markets()` returns `[]`

### Task 12: Wire emergency stop into Telegram bot
- **Action**: In `src/bot/handlers.py`:
  - Add `stop_cmd` handler: calls `emergency.activate()`, requires authorization check
  - Add `resume_cmd` handler: calls `emergency.deactivate()`, requires authorization check
  - Register both in `src/bot/main.py` command handlers
  - In `status_cmd`: check `emergency.is_active()` and show HALTED status
- **Mirror**: Existing command handler pattern (see `start_cmd` at line 57)
- **Validate**: `/stop` sets Redis key, `/resume` clears it, `/status` shows state

### Task 13: Add append-only DB rules
- **Action**: In `db/init.sql`, add after table definitions:
  ```sql
  CREATE RULE no_update_audit AS ON UPDATE TO audit_logs DO INSTEAD NOTHING;
  CREATE RULE no_delete_audit AS ON DELETE TO audit_logs DO INSTEAD NOTHING;
  CREATE RULE no_update_closed_trade AS ON UPDATE TO closed_paper_trades DO INSTEAD NOTHING;
  CREATE RULE no_delete_closed_trade AS ON DELETE TO closed_paper_trades DO INSTEAD NOTHING;
  ```
- **Note**: Only for `audit_logs` and `closed_paper_trades` — not `signals` or `paper_positions` (those need UPDATE for status changes and price updates).
- **Validate**: `UPDATE audit_logs SET action='test' WHERE id=1` should silently no-op

### Task 14: Create agent prompt files
- **Action**: Create `.claude/agents/idx_analyst.md` and `.claude/agents/risk_manager.md` per audit spec. Reference prompt docs for the agent system.
- **Validate**: Files exist in `.claude/agents/`

### Task 15: Signal validation in orchestrator
- **Action**: In `src/agents/orchestrator.py` `_scan_market()`:
  - After agent returns result, validate JSON structure before persisting
  - Check: ticker exists, confidence is int 0-100, entry_price > 0, stop_loss > 0, direction is valid enum
  - Reject malformed signals with `logger.warning("invalid_signal", ticker=ticker, issues=...)`
  - In `_save_signal()`: use `src/risk.idx_limits.validate_order()` for IDX signals (check ARA/ARB, tick size)
- **Mirror**: Existing validation patterns
- **Validate**: Agent returning malformed JSON → signal not persisted, warning logged

## Validation

```bash
# 1. Config files parse
docker compose config
python -c "from src.config import settings; print(settings.TRADING_MODE)"

# 2. Risk module imports
python -c "from src.risk import emergency; from src.risk.idx_limits import tick_size, validate_order"

# 3. No stale model references
grep -r "claude-3-5-sonnet" . --include="*.yaml" --include="*.env*" --include="*.py"
grep -r "deepseek" . --include="*.yaml" --include="*.env*"

# 4. No tradingview-mcp-server in main deps
grep "tradingview-mcp" pyproject.toml | grep -v "optional"

# 5. Dependency install clean
pip install -e . 2>&1 | grep -i error

# 6. Health endpoint responds
curl http://localhost:8000/health

# 7. Append-only rules work
psql -c "UPDATE audit_logs SET action='test' WHERE id=1"  # should no-op

# 8. Emergency stop flow
redis-cli SET karsa:emergency_stop '{"active":true}'
python -c "import asyncio; from src.risk.emergency import is_active; print(asyncio.run(is_active()))"  # True
redis-cli DEL karsa:emergency_stop

# 9. Agent prompts exist
ls -la .claude/agents/
```

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| DB_PASSWORD validator breaks existing `.env` (`trader_pass` = 11 chars, needs ≥12) | **HIGH** | Update `.env` password simultaneously with Task 6. Document in commit message. |
| FastAPI migration changes health endpoint port (8080 → 8000) | **HIGH** | Update compose port mapping, Dockerfile HEALTHCHECK, and bot's `status_cmd` orchestrator URL together in Task 7 |
| tradingview-mcp-server removal may break something not visible | LOW | Code uses `tradingview_ta` directly; MCP server was never wired in |
| Emergency stop standalone redis vs CacheManager inconsistency | LOW | Document why standalone; consolidate later |

## Acceptance

- [ ] All 5 audit infrastructure fixes applied
- [ ] Health server migrated from threading to FastAPI
- [ ] Risk module created (emergency + idx_limits)
- [ ] Emergency stop wired into: kill switch job, orchestrator scans, Telegram bot (/stop + /resume)
- [ ] Signal validation added to orchestrator
- [ ] Append-only DB rules on audit_logs + closed_paper_trades
- [ ] Agent prompt files created
- [ ] No DeepSeek references remain
- [ ] No outdated model references remain
- [ ] `DB_PASSWORD` in `.env` ≥ 12 chars
- [ ] `docker compose config` succeeds
- [ ] Python imports succeed for all new modules
