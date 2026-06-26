# Karsa AI Trading System

Multi-market AI trading system for IDX (Indonesia), US Equities, and Global ETFs.

## Architecture

```
Telegram Bot ←→ Orchestrator ←→ 9Router (API Gateway) ←→ Anthropic/DeepSeek
                     ↓ dispatches
         IDX Analyst / US Analyst / ETF Analyst / Portfolio Analyst
                     ↓ data from
         TradingView TA (direct Python) + Redis cache
                     ↓ state in
         PostgreSQL (audit, trades, signals) + Redis (cache, pub/sub, rate limiting)
                     ↓ advisory layer
         MacroRegimeFilter (VIX/SPY) + PositionSizer (ATR)
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
| `9router` | 20129→20128 | LLM API gateway with fallback routing |
| `redis` | 6379 | Cache, rate limiting, pub/sub |
| `postgres` | 5432 | Audit logs, trades, signals, portfolio state |
| `karsa-orchestrator` | 8080 | Agent scheduler, health checks (`/health`, `/health/scheduler`) |
| `karsa-telegram-bot` | 8443 | HITL approval (polling or webhook) |

## Trading Strategies

- **IDX Foreign Flow Breakout**: 3-day foreign net buy > 5% volume + Bollinger breakout + ARA buffer
- **US Relative Strength Momentum**: 60-day RS > SPY by 15% + trend alignment (50 EMA > 200 EMA)
- **ETF Mean Reversion**: RSI < 30 + lower Bollinger Band touch (daily candles)

## Telegram Commands

| Command | Description |
|---|---|
| `/portfolio` | View full portfolio & cash |
| `/add <market> <ticker> <qty> <price>` | Add position |
| `/edit <market> <ticker> qty\|price <value>` | Edit position |
| `/remove <market> <ticker>` | Remove position |
| `/analyze` | AI portfolio analysis |
| `/scan <market> <ticker>` | Quick market readout |
| `/audit <ticker>` | AI reasoning & risk check |
| `/briefing` | Morning dashboard & regime |
| `/regime` | Current market regime (BULL/BEAR/NEUTRAL) |
| `/pnl` | Shadow portfolio performance |
| `/trades` | Paper trading history |
| `/status` | System health & scheduler |

## Development

```bash
pip install -e ".[dev]"
pytest
pytest tests/test_agents/test_idx_analyst.py -v
```

## Key Design Decisions

- **9Router over direct Anthropic**: Secret isolation — agent containers never see API keys.
- **Redis pub/sub for HITL**: Decouples Telegram bot from orchestrator.
- **APScheduler + MemoryJobStore**: Lightweight scheduling; jobs are stateless scans that don't need persistence.
- **Idempotency keys on all trades**: Prevents double execution on retries.
- **Append-only audit logs**: Every decision logged immutably.
- **Paper trading first**: Shadow execution engine simulates trades before real capital deployment.

## License

Private — all rights reserved.
