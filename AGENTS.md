# Agents

## Orchestrator (`orchestrator`)
**Role**: Schedules scans, dispatches analysts in parallel (`asyncio.gather`), manages combo routing via 9Router.
**File**: `src/agents/orchestrator.py`
**Key**: Universe lists (IDX/US/ETF), `scan_all_markets()` parallel dispatch with emergency stop gate + signal persistence, `scan_single()` for ad-hoc commands (also gated), `analyze_portfolio()` delegates to PortfolioAnalyst, `_save_signal()` persists signals with IDX order validation, `_validate_signal()` checks JSON structure before persisting.

## IDX Analyst (`idx_analyst`)
**Role**: Scans Indonesian (IDX) market universe (30 stocks across 8 sectors).
**Strategy**: Foreign flow breakout + Bollinger + ARA buffer. Enhanced with sector rotation awareness, flow signals, earnings blackout logic, dynamic ARA/ARB.
**Tools**: `get_idx_quote`, `get_idx_ohlcv`, `get_bollinger`, `get_idx_flow`, `get_idx_breadth`, `check_earnings`, `get_dynamic_ara_arb`.
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

## Risk Module (not agents — deterministic modules)

### Emergency Stop (`src/risk/emergency.py`)
Redis-backed kill switch. `activate(reason, operator)` halts all trading decisions. `is_active()` checked by orchestrator before every scan. Triggered by kill switch job at -1.5% daily P&L or manual `/stop` command.

### IDX Limits (`src/risk/idx_limits.py`)
IDX market compliance: tick size tiers (Fraksi Harga), `validate_order()` enforces ARA/ARB bounds, `settlement_date()` calculates T+2. `max_lots_by_adv()` enforces 10% ADV liquidity gate. Dynamic ARA/ARB per-ticker (`ara_ceiling_dynamic`, `arb_floor_dynamic`). IHSG circuit breaker (`ihsg_circuit_breaker_level`: ±5%→30min halt, ±10%→halted). Forced sell triggers (`check_forced_sell_triggers`: 3x lower limit, 10x ADV, T+2 failure, IDX suspension). Used by orchestrator `_save_signal()` for IDX signals.

## Advisory Layer (not agents — deterministic modules)

### Regime Filters (`src/advisory/regime.py`)
`USRegimeFilter` and `IDXRegimeFilter` classify market regime as BULL/BEAR/NEUTRAL based on VIX level, SPY/IHSG price vs 200-day SMA. Hard veto: ETF mean reversion disabled in BEAR regime. Used by `/briefing` and `/regime` commands.

### IDX Intelligence (`src/advisory/idx_intelligence.py`)
`IDXMarketIntelligence`: composite regime scoring (-100 to +100) from breadth (30%), sector rotation (25%), foreign flow proxy (20%), price structure (25%). `FlowTracker`: volume-based foreign activity proxy per ticker. `EarningsCalendar`: static JSON calendar with blackout windows (5-day buffer). Composite gate in orchestrator: ≤-50 skips IDX scan, ≤-20 reduces sizing. Used by `/idx` dashboard, `/briefing`, and IDX agent tools.
**Data**: `src/advisory/earnings_calendar.json` — static earnings dates for IDX_UNIVERSE, updated quarterly.

### PositionSizer (`src/advisory/sizing.py`)
Calculates volatility-target position sizes using ATR. Used for paper trade sizing in the execution pipeline.

## Utilities (not agents)

### MCPClient (`src/data/mcp_client.py`)
Wraps `tradingview_ta.TA_Handler` with 3-tier fallback (TradingView → Massive → Finnhub). Methods: `get_quote()`, `get_ohlcv()`, `get_technical()`, `get_volume_profile()`. Circuit breaker blocks failing providers for 10min. Uses `asyncio.to_thread()` for non-blocking I/O.

### Approval Flow (`src/bot/_approval.py`)
`send_signal_alert()`: sends Telegram alert with APPROVE/REJECT inline buttons for signals with confidence >= 60.
`handle_approval()`: on APPROVE, creates PaperPosition and marks signal APPROVED. On REJECT, marks signal REJECTED.

### Format Engine (`src/utils/format.py`)
Composable Telegram HTML formatters (GramIO style). `HTML` marker class prevents double-escaping. `bold()`, `italic()`, `code()`, `pre()`, `fmt()`, `join()` auto-escape plain text. Used by all 16 bot commands.

### Input Validation (`src/utils/validation.py`)
`validate_ticker()`: regex alphanumeric + dots, max 20 chars. `validate_market()`: IDX/US/ETF allowlist. `sanitize_for_prompt()`: strips non-alphanumeric for LLM prompts.

### MarketHours (`src/utils/market_hours.py`)
`is_idx_open()` and `is_us_open()` — used by scheduler jobs to skip scans when markets are closed.
