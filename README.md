<div align="center">
  <img src="assets/karsa_icon.png" alt="Karsa" width="180" />
  <h1>Karsa AI Trading System</h1>
  <p>Multi-market AI trading system for IDX (Indonesia), US Equities, Global ETFs, and Crypto (Bybit perpetuals).</p>
</div>

## Architecture

```
Telegram Bot ←→ Orchestrator ←→ 9Router (API Gateway) ←→ Anthropic/DeepSeek
                     ↓ dispatches
         IDX Analyst / US Analyst / ETF Analyst / Portfolio Analyst / Crypto Analyst
                     ↓ data from
         TradingView TA (direct Python) + Bybit REST (pybit) + Redis cache
                     ↓ state in
         PostgreSQL (audit, trades, signals, positions) + Redis (cache, pub/sub, rate limiting, kill switch)
                     ↓ advisory layer
         RegimeFilter (VIX/SPY/IHSG) + CryptoRegime (Hurst+ADX) + PositionSizer (ATR) + StrategySelector
                     ↓ risk gates
         8 crypto risk gates → SOR (limit→reprice→market) → TrailingStop / PositionManager / Reconciler
```

## Quick Start

```bash
cp .env.example .env
# Required in .env:
#   DB_PASSWORD=<12+ chars, no placeholders>
#   REDIS_PASSWORD=<any>
#   TELEGRAM_TOKEN=<from @BotFather>
#   TELEGRAM_CHAT_ID=<your chat ID>
#   9ROUTER_URL, 9ROUTER_AUTH_TOKEN, 9ROUTER_MODEL (or ANTHROPIC_API_KEY)
docker compose up --build
```

## Services

| Service | Port | Purpose |
|---|---|---|
| `9router` | 20129→20128 | LLM API gateway with fallback routing |
| `redis` | 6379 | Cache, rate limiting, pub/sub, kill switch |
| `postgres` | 5432 | Audit logs, trades, signals, portfolio state |
| `karsa-orchestrator` | 8000 | Agent scheduler, health checks (`/health`, `/health/scheduler`) |
| `karsa-telegram-bot` | 8443 | IDX/US/ETF HITL approval (polling or webhook) |
| `karsa-crypto-bot` | — | Crypto trading bot (separate Telegram bot, polling) |

## Trading Strategies

- **IDX Foreign Flow Breakout** — 3-day foreign net buy > 5% volume + Bollinger breakout + ARA buffer
- **US Relative Strength Momentum** — 60-day RS > SPY by 15% + trend alignment (50 EMA > 200 EMA)
- **ETF Mean Reversion** — RSI < 30 + lower Bollinger Band touch (daily candles)
- **Crypto Trend+Sentiment Convergence** — Price > 20 EMA > 50 EMA + negative funding (contrarian) + rising OI + volume spike. Regime-adaptive (Hurst + ADX on BTC). Max 3x base leverage, tier-based caps.

## Telegram Commands

### IDX/US/ETF Bot

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
| `/idx` | IDX Intelligence dashboard |
| `/stop` | Activate emergency stop |
| `/resume` | Deactivate emergency stop |

### Crypto Bot

| Command | Description |
|---|---|
| `/start` | Welcome & bot status |
| `/status` | Crypto system health & positions |
| `/portfolio` | Open crypto positions with PnL |
| `/scan` | Trigger manual crypto scan |
| `/pnl` | Detailed PnL breakdown & equity curve |
| `/risk` | Risk dashboard (gates, limits, utilization) |
| `/kill` | Emergency halt — flatten all positions |
| `/sellall` | Close all positions + 15min cooldown |
| `/resume` | Resume after kill/sellall |
| `/activity` | Recent trading activity log |
| `/audit_agent` | Agent performance audit |
| `/guide` | Trading guide & strategy reference |
| `/regime` | Crypto regime (BTC Hurst+ADX) |
| `/funding` | Funding rates across universe |
| `/trades` | Closed trades history |

## Development

```bash
pip install -e ".[dev]"
pytest
pytest tests/test_agents/test_idx_analyst.py -v
```

## Key Design Decisions

- **9Router over direct Anthropic** — Secret isolation; agent containers never see API keys.
- **Redis pub/sub for HITL** — Decouples Telegram bot from orchestrator.
- **APScheduler + MemoryJobStore** — Lightweight scheduling; jobs are stateless scans that don't need persistence.
- **Idempotency keys on all trades** — Prevents double execution on retries.
- **Append-only audit logs** — Every decision logged immutably.
- **Paper trading first** — Shadow execution engine simulates trades before real capital deployment.
- **Crypto auto-execute** — Scan → 8 risk gates → SOR → save → notify. No HITL for crypto.
- **Bidirectional reconciliation** — Every 5min, DB ↔ Bybit exchange state drift detection and auto-fix.

## License

Private — all rights reserved.
