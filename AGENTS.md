# Agents

## Orchestrator (`orchestrator`)
**Role**: Schedules scans, dispatches analysts in parallel (`asyncio.gather`), manages combo routing via 9Router.
**File**: `src/agents/orchestrator.py`
**Key**: Universe lists (IDX/US/ETF), `scan_all_markets()` parallel dispatch, `scan_single()` for ad-hoc commands, `analyze_portfolio()` delegates to PortfolioAnalyst, `_save_signal()` persists signals to DB.

## IDX Analyst (`idx_analyst`)
**Role**: Scans Indonesian (IDX) market universe.
**Strategy**: Foreign flow breakout + Bollinger + ARA buffer.
**File**: `src/agents/idx_analyst.py`

## US Analyst (`us_analyst`)
**Role**: Scans US Equities market universe.
**Strategy**: Relative strength momentum vs SPY + trend alignment.
**File**: `src/agents/us_analyst.py`

## ETF Analyst (`etf_analyst`)
**Role**: Scans Global ETF universe.
**Strategy**: Mean reversion (RSI < 30 + BB touch).
**File**: `src/agents/etf_analyst.py`

## Portfolio Analyst (`portfolio_analyst`)
**Role**: Analyzes current holdings against live market data.
**Action**: Suggests HOLD/ADD/TRIM/CUT based on technicals (RSI, BB, EMA) and risk flags. Does NOT execute trades.
**Trigger**: `/portfolio` and `/analyze` commands (in Telegram).
**File**: `src/agents/portfolio_analyst.py`

## Advisory Layer (not agents — deterministic modules)

### MacroRegimeFilter (`src/advisory/regime.py`)
Classifies market regime as BULL/BEAR/NEUTRAL based on VIX level, SPY price vs 200-day SMA. Used by `/briefing` and `/regime` commands.

### PositionSizer (`src/advisory/sizing.py`)
Calculates volatility-target position sizes using ATR. Used for paper trade sizing in the execution pipeline.

## Utilities (not agents)

### MCPClient (`src/data/mcp_client.py`)
Wraps `tradingview_ta.TA_Handler`. Methods: `get_quote()`, `get_ohlcv()`, `get_indicators()`. IDX uses `screener='indonesia', exchange='IDX'`. US/ETF tries NASDAQ → NYSE → AMEX fallback.

### MarketHours (`src/utils/market_hours.py`)
`is_idx_open()` and `is_us_open()` — used by scheduler jobs to skip scans when markets are closed.
