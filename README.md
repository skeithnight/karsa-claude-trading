# Karsa AI Trading System

Multi-market AI trading system for IDX (Indonesia), US Equities, and Global ETFs.

## Architecture

```
Telegram Bot ←→ Lead Orchestrator ←→ 9Router (API Gateway) ←→ Anthropic/DeepSeek
                     ↓ dispatches
         IDX Analyst / US Analyst / ETF Analyst / Risk Manager
                     ↓ data from
         TradingView MCP + IDX Data Adapter (Stockbit/RTI)
                     ↓ state in
         PostgreSQL (audit, trades) + Redis (cache, pub/sub)
                     ↓ executes via
         IDX Broker (IPOT/Mirae) + US Broker (Alpaca)
```

## Quick Start

```bash
cp .env.example .env
# Edit .env with your API keys

docker compose up --build
```

## Services

| Service | Port | Purpose |
|---|---|---|
| `karsa-9router` | 20128 | LLM API gateway with fallback routing |
| `karsa-redis` | 6379 | Cache, rate limiting, pub/sub |
| `karsa-postgres` | 5432 | Audit logs, trades, portfolio state |
| `karsa-tradingview-mcp` | 8080 | Unified market data |
| `karsa-orchestrator` | — | Agent scheduler and orchestrator |
| `karsa-telegram-bot` | 8443 | HITL approval webhook |

## Trading Strategies

- **IDX Foreign Flow Breakout**: 3-day foreign net buy > 5% volume + Bollinger breakout + ARA buffer
- **US Relative Strength Momentum**: 60-day RS > SPY by 15% + trend alignment (50 EMA > 200 EMA)
- **ETF Mean Reversion**: RSI < 30 + lower Bollinger Band touch (daily candles)

## Development

```bash
pip install -e ".[dev]"
pytest
pytest tests/test_agents/test_idx_analyst.py -v
ruff check src/
ruff format src/
mypy src/ --strict
```

## Key Design Decisions

- **9Router over direct Anthropic**: Secret isolation — agent containers never see API keys.
- **Redis pub/sub for HITL**: Decouples Telegram bot from orchestrator.
- **APScheduler + Postgres job store**: Jobs survive container restarts.
- **Idempotency keys on all trades**: Prevents double execution on retries.
- **Append-only audit logs**: Every decision logged immutably.

## License

Private — all rights reserved.
