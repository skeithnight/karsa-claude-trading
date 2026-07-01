# Agents

## Orchestrator (`orchestrator`)
**Role**: Schedules scans, dispatches analysts in parallel (`asyncio.gather`), manages combo routing via 9Router.
**File**: `src/agents/orchestrator.py`
**Key**: Universe lists (IDX/US/ETF/CRYPTO), `scan_all_markets()` parallel dispatch with emergency stop gate + signal persistence. IDX gated by composite score. CRYPTO: 24/7 auto-execute pipeline (scan → risk → SOR → save → notify). `_scan_crypto_parallel()` scans all pairs concurrently. `_auto_execute_crypto()` risk-checks and executes via SOR. Signal deduplication (4h window). `_save_crypto_position()` persists to DB. `scan_single()` for ad-hoc commands, `_validate_signal()` checks JSON structure.

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

## Crypto Analyst (`crypto_analyst`)
**Role**: Scans crypto perpetual pairs on Bybit (10 pairs: BTC, ETH, SOL, BNB, XRP, ADA, DOGE, AVAX, DOT, LINK).
**Strategy**: Trend + Sentiment Convergence. Entry: Price > 20 EMA > 50 EMA + negative funding (contrarian) + rising OI + volume > 1.5x avg. Max 3x leverage. Signals valid 4h.
**Tools**: `get_crypto_quote`, `get_crypto_ohlcv`, `get_funding_rate`, `get_open_interest`, `get_crypto_rsi`, `get_crypto_bollinger`, `get_crypto_macd`, `get_crypto_atr`, `get_crypto_full_analysis` (all deterministic TA via `crypto_technicals.py`).
**File**: `src/agents/crypto_analyst.py`

## Crypto Auditor (`crypto_auditor`)
**Role**: Reviews crypto trading performance and recommends improvements. No tools — receives pre-computed metrics.
**Deterministic pre-filter**: Rejects RSI > 85 for LONG, RSI < 15 for SHORT, funding rate > 0.1% for LONG, < -0.1% for SHORT. Only calls LLM for signals that pass.
**File**: `src/agents/crypto_auditor.py`

## Risk Module (not agents — deterministic modules)

### Emergency Stop (`src/risk/emergency.py`)
Redis-backed kill switch. `activate(reason, operator)` halts all trading decisions. `is_active()` checked by orchestrator before every scan. `activate_global_halt()` sets both `karsa:global_halt` and `karsa:emergency_stop` keys. Used by `/kill` and `/resume` commands. Triggered by kill switch job at `CRYPTO_DAILY_LOSS_LIMIT_PCT` or manual `/stop` command.

### IDX Limits (`src/risk/idx_limits.py`)
IDX market compliance: tick size tiers (Fraksi Harga), `validate_order()` enforces ARA/ARB bounds, `settlement_date()` calculates T+2. `max_lots_by_adv()` enforces 10% ADV liquidity gate. Dynamic ARA/ARB per-ticker (`ara_ceiling_dynamic`, `arb_floor_dynamic`). IHSG circuit breaker (`ihsg_circuit_breaker_level`: ±5%→30min halt, ±10%→halted). Forced sell triggers (`check_forced_sell_triggers`: 3x lower limit, 10x ADV, T+2 failure, IDX suspension). Used by orchestrator `_save_signal()` for IDX signals.

### Crypto Risk Manager (`src/risk/crypto_risk_manager.py`)
Evaluates crypto signals against 8 risk gates: basic validation, daily loss limit (unrealized PnL), max concurrent positions (5), duplicate ticker, correlation tier limits (3 tiers: BTC/ETH, alt-L1, meme), cooldown, funding rate (crowded trade rejection), max position cap (10%). ATR-based stop-loss (2x ATR), 3:1 R/R take-profit, tier-based leverage caps (tier1=10x, tier2=5x, tier3=3x). Kill switch checks both in-memory state and Redis emergency stop. `check_liquidation_proximity()` with warn (20%), alert (10%), force-close (5%) thresholds.

### Smart Order Router (`src/risk/sor.py`)
Executes approved signals on Bybit. Post-Only limit orders at bid/ask for maker rebates. Re-price loop (3 attempts, 30s timeout). Falls back to market order. `flatten_all()` closes all positions (used by `/kill`, `/sellall`). Sets stop-loss and take-profit after fill.

### Funding Tracker (`src/risk/funding_tracker.py`)
Tracks per-position funding payments (8h intervals). `get_current_rates()` for all universe pairs. `calculate_position_funding_cost()` for cost projection. `get_alerts()` flags extreme rates. Annualized cost calculation (rate × 3 × 365).

## Advisory Layer (not agents — deterministic modules)

### Regime Filters (`src/advisory/regime.py`)
`USRegimeFilter` and `IDXRegimeFilter` classify market regime as BULL/BEAR/NEUTRAL based on VIX level, SPY/IHSG price vs 200-day SMA. Hard veto: ETF mean reversion disabled in BEAR regime. Used by `/briefing` and `/regime` commands.

### IDX Intelligence (`src/advisory/idx_intelligence.py`)
`IDXMarketIntelligence`: composite regime scoring (-100 to +100) from breadth (30%), sector rotation (25%), foreign flow proxy (20%), price structure (25%). `FlowTracker`: volume-based foreign activity proxy per ticker. `EarningsCalendar`: static JSON calendar with blackout windows (5-day buffer). Composite gate in orchestrator: ≤-50 skips IDX scan, ≤-20 reduces sizing. Used by `/idx` dashboard, `/briefing`, and IDX agent tools.
**Data**: `src/advisory/earnings_calendar.json` — static earnings dates for IDX_UNIVERSE, updated quarterly.

### PositionSizer (`src/advisory/sizing.py`)
Calculates volatility-target position sizes using ATR. Used for paper trade sizing in the execution pipeline.

### Crypto Regime Filter (`src/advisory/crypto_regime.py`)
Deterministic crypto regime classifier using BTC as benchmark. Hurst Exponent (trend persistence) + ADX (trend strength) on 4H/1D data. States: TREND_BULL, TREND_BEAR, MEAN_REVERSION, CHOP. BTC dominance via CoinGecko (>55% = BTC season, <45% = alt season). Size multipliers: TREND_BULL=1.2x, TREND_BEAR=0.5x, MEAN_REVERSION=0.8x, CHOP=0.5x. CHOP regime skips crypto scan entirely. 5-minute in-memory cache.

### Crypto Technicals (`src/advisory/crypto_technicals.py`)
Pure Python deterministic indicators — LLM calls these tools instead of doing math. RSI (Wilder smoothing), Bollinger Bands (2σ), EMA, MACD (12/26/9), ATR (14-period). `full_analysis()` runs all at once. Self-test included (`__main__`).

### Crypto Universe (`src/advisory/crypto_universe.py`)
Single source of truth for crypto trading universe (eliminates duplication). 10 pairs with per-pair config: `min_order_usd`, `tick_size`, correlation tier. `get_max_leverage()` respects both tier caps and `CRYPTO_MAX_LEVERAGE` config.

### Crypto Audit (`src/advisory/crypto_audit.py`)
`CryptoAuditMetrics.gather()` queries Signal + ClosedPaperTrade tables for deterministic performance metrics. Win rate, by-ticker, by-direction, confidence calibration, best/worst trades. Returns structured dict for LLM auditor consumption.

## Utilities (not agents)

### MCPClient (`src/data/mcp_client.py`)
Wraps `tradingview_ta.TA_Handler` with 3-tier fallback (TradingView → Massive → Finnhub). Methods: `get_quote()`, `get_ohlcv()`, `get_technical()`, `get_volume_profile()`. Circuit breaker blocks failing providers for 10min. Uses `asyncio.to_thread()` for non-blocking I/O. CRYPTO market delegates to `BybitClient`.

### BybitClient (`src/data/bybit_client.py`)
Bybit REST API client for crypto perpetuals. Data: `get_ticker()`, `get_ohlcv()`, `get_funding_rate()`, `get_funding_history()`, `get_open_interest()`, `get_orderbook()`. Execution: `place_order()`, `cancel_order()`, `get_positions()`, `set_stop_loss()`, `set_take_profit()`, `get_wallet_balance()`. Retry with exponential backoff (3 attempts, 1s/2s/4s). Fatal/retryable error classification. Rate limiting (100ms throttle + semaphore(5)). In-memory + Redis caching. `validate_api_key()` for health checks.

### Approval Flow (`src/bot/_approval.py`)
`send_signal_alert()`: sends Telegram alert with APPROVE/REJECT inline buttons for signals with confidence >= 60.
`handle_approval()`: on APPROVE, creates PaperPosition and marks signal APPROVED. On REJECT, marks signal REJECTED.

### Format Engine (`src/utils/format.py`)
Composable Telegram HTML formatters (GramIO style). `HTML` marker class prevents double-escaping. `bold()`, `italic()`, `code()`, `pre()`, `fmt()`, `join()` auto-escape plain text. Used by all 16 bot commands.

### Input Validation (`src/utils/validation.py`)
`validate_ticker()`: regex alphanumeric + dots, max 20 chars. `validate_market()`: IDX/US/ETF allowlist. `sanitize_for_prompt()`: strips non-alphanumeric for LLM prompts.

### MarketHours (`src/utils/market_hours.py`)
`is_idx_open()` and `is_us_open()` — used by scheduler jobs to skip scans when markets are closed.
