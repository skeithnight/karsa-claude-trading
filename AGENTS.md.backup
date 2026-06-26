# Agents

## Portfolio Analyst (`portfolio_analyst`)
**Role**: Analyzes current holdings against live market data.
**Action**: Suggests HOLD/ADD/TRIM/CUT based on technicals (RSI, BB, EMA) and risk flags. Does NOT execute trades.
**Trigger**: `/portfolio` command (in Telegram).

## IDX Analyst (`idx_analyst`)
**Role**: Scans Indonesian (IDX) market universe.
**Strategy**: Foreign flow breakout + Bollinger + ARA buffer.

## US Analyst (`us_analyst`)
**Role**: Scans US Equities market universe.
**Strategy**: Relative strength momentum vs SPY + trend alignment.

## ETF Analyst (`etf_analyst`)
**Role**: Scans Global ETF universe.
**Strategy**: Mean reversion (RSI < 30 + BB touch).

## Orchestrator (`orchestrator`)
**Role**: Schedules scans, dispatches analysts in parallel (`asyncio.gather`), manages combo routing via 9Router.
