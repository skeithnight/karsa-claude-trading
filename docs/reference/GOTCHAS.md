# Full Gotchas List

(The most-frequently-relevant ones are kept in CLAUDE.md directly. This is the complete list.)

- Dockerfiles `COPY src/` — must `--build` after code changes, `restart` alone won't pick up new code.
- `tradingview_ta` is imported lazily inside the container (not at module level) to avoid startup failures if the package isn't installed yet.
- Containers run as non-root user `karsa`. If volume permissions break, check file ownership.
- Redis requires authentication (`REDIS_PASSWORD`). All containers must use `redis://:${REDIS_PASSWORD}@redis:6379`.
- `DB_PASSWORD` must be ≥12 chars and not a placeholder. The config validator rejects common weak values at startup.
- IDX lot size is always 100 shares. `IDXBroker` enforces this.
- The `karsa-9router` service exists in `docker-compose.yml` but the system expects the user's own 9Router instance via `host.docker.internal:20128` for local dev. The compose 9router is on port 20129→20128.
- Kill switch threshold uses `CRYPTO_DAILY_LOSS_LIMIT_PCT` (default 3%) for crypto, checked against unrealized PnL from positions. Also checks Redis emergency stop (survives restarts). `/kill` sets both `karsa:global_halt` and `karsa:emergency_stop` Redis keys.
- APScheduler uses `MemoryJobStore` — jobs are stateless and don't survive container restarts.
- `_VALID_DIRECTIONS` in orchestrator is `{"LONG", "SHORT", "CLOSE"}` — matches DB CHECK constraint. Agents returning BUY/SELL/HOLD/WATCH are rejected by validation.
- Postgres uses `pgvector/pgvector:pg15` image (not `postgres:15-alpine`). Required for `trade_memory` table with vector embeddings. `CREATE EXTENSION IF NOT EXISTS vector` in init.sql.
- `src/metrics/crypto_metrics.py` must be imported at startup for Prometheus metrics to register. Both `main.py` and `main_crypto.py` import it in `startup()`.
- `CircuitBreakerManager` (not `CircuitBreaker`) is the correct class name in `src/risk/circuit_breaker.py`.
- `FundingTracker.__init__` takes `(bybit_client)` — not `(bybit, redis)`. Methods: `get_current_rates()`, `get_cumulative_costs()`. No `check_limits()` or `sync_all()`.
- `TrailingStopManager.update_trailing_stops(positions)` requires a `list[CryptoPosition]` argument.
- `BaseAgent.run()` returns `dict` — if LLM returns JSON array, it's parsed as single object. Batch prompt in orchestrator handles this with fallback `batch_result.get("signals", [batch_result])`.
- `sentence-transformers` not in Dockerfile — RAG memory gracefully degrades (returns empty string). Add `pip install sentence-transformers` to Dockerfile for full RAG support.
- Universe engine config: `MAX_UNIVERSE_SIZE=40`, `UNIVERSE_TTL=30min` (buffer for 15min scheduler), aggressive profile scans 30 coins, `BATCH_SIZE=5` per LLM call, `min_volume_usd=250_000` absolute floor. Scheduler runs `_job_refresh_universe` every 15min.
- **Correlation tiers**: Tier1 (BTC/ETH) max 2 pos 15%, Tier2 (SOL/AVAX/SUI/LINK/BNB/NEAR) max 2 pos 15%, Tier3 (DOGE/XRP/ADA/PEPE/DOT/MATIC + others) max 2 pos 10%. Relaxed from original to support small capital.
- **Risk profile min_confidence**: conservative=70, moderate=50, aggressive=35. Aggressive profile scans 30 coins per cycle.
- `WS_LAST_MESSAGE_TIMESTAMP` initializes to `time.time()` at import (not 0) — prevents Grafana "53 years" display before first WS tick.