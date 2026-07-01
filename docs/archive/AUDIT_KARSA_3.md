# Karsa Crypto Trading — Deep Audit & Enhancement Plan

**Date:** June 30, 2026  
**Scope:** Full audit of crypto trading infrastructure with actionable enhancement roadmap  
**Files Analyzed:** 15 crypto-related source files, 3 config files, DB schema, Docker infrastructure

---

## Executive Summary

Karsa's crypto node has a solid architectural foundation — the "Investment Firm" agent topology (Analyst → Auditor → Risk → Execution) is correctly designed. However, the current implementation has **critical safety gaps** that could cause significant losses in live trading, and **multiple areas where the IDX/US side is more mature** than crypto.

> [!CAUTION]
> **3 Critical Issues** must be fixed before any mainnet deployment:
> 1. Kill switch only tracks realized PnL — a position at -40% unrealized won't trigger it
> 2. No liquidation proximity warnings — leverage positions can be liquidated silently
> 3. Funding rate costs not tracked — can bleed 0.3%/day unnoticed

**Overall Maturity Score: 65/100** (compared to IDX node at ~85/100)

---

## Challenges Resolved (Implementation Review)

> [!NOTE]
> 3 issues found during code-level review. All resolved.

**C1 — `liquidation_price` missing from `BybitClient.get_positions()`**
- ✅ Fixed: Added `liquidation_price`, `funding_fee`, `position_idx` using Bybit's `liqPrice`, `curRealisedPnl`, `positionIdx`.

**C2 — Kill switch `_get_positions()` used `asyncio.new_event_loop()` inside async context**
- ✅ Fixed: `check_kill_switch()` now fully async with `await`.

**C3 — Kill switch used `wallet.get("total_equity")` (non-existent key)**
- ✅ Fixed: Uses `wallet.get("balance", 0)`.

**Additional improvements:**
- BybitClient retry with exponential backoff (3 attempts)
- 4 new crypto DB tables
- Crypto universe single source of truth (`src/advisory/crypto_universe.py`)
- Parallel crypto scanning via `asyncio.gather()`
- Signal deduplication (4h window)
- BTC dominance via CoinGecko
- Deterministic TA tools (RSI, BB, MACD, ATR) on CryptoAnalyst
- Auditor pre-filter (extreme RSI, crowded funding)

---

## Audit Findings by Component

### 1. Data Layer — [bybit_client.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/data/bybit_client.py)

| Finding | Severity | Detail |
|---------|----------|--------|
| No WebSocket support | 🔴 HIGH | REST polling only — misses real-time fills, price ticks, liquidation events |
| Error swallowing | 🔴 HIGH | All exceptions return `None` silently — upstream code can't distinguish "no data" from "API failure" |
| No rate limit handling | 🟡 MEDIUM | Bybit enforces strict rate limits; no token bucket or backoff |
| No retry on transient errors | 🟡 MEDIUM | HTTP timeouts immediately return `None` |
| Circuit breaker memory-only | 🟡 MEDIUM | Resets on container restart (should use Redis like emergency stop) |
| Hardcoded to linear perpetuals | 🟢 LOW | No spot/inverse/options — acceptable for V1 |
| No historical funding rates | 🟡 MEDIUM | Only current rate; can't see funding trend |

### 2. Risk Management — [crypto_risk_manager.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/risk/crypto_risk_manager.py)

| Finding | Severity | Detail |
|---------|----------|--------|
| **Only realized PnL in kill switch** | 🔴 CRITICAL | Unrealized losses ignored — position at -50% won't trigger emergency stop |
| **No liquidation price awareness** | 🔴 CRITICAL | Doesn't calculate or warn about liquidation proximity |
| **No funding rate risk** | 🔴 CRITICAL | Funding can cost 0.3%/day (109.5%/year!) — not factored into risk or PnL |
| No correlation analysis | 🟡 MEDIUM | Can go LONG BTC+ETH+SOL simultaneously (all ~0.85 correlated) |
| Leverage cap hardcoded 10x | 🟡 MEDIUM | PEPE at 10x is far riskier than BTC at 10x — needs per-asset config |
| No margin mode awareness | 🟡 MEDIUM | Doesn't distinguish cross vs isolated margin |
| No trailing stop logic | 🟡 MEDIUM | Only static stop-loss; no dynamic risk adjustment |
| Daily PnL resets at midnight UTC | 🟢 LOW | Not timezone-configurable |

### 3. Smart Order Router — [sor.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/risk/sor.py)

| Finding | Severity | Detail |
|---------|----------|--------|
| No order state persistence | 🟡 MEDIUM | Bot crash mid-execution = lost order tracking |
| No TWAP/VWAP for large orders | 🟡 MEDIUM | Full size visible; market impact for larger positions |
| Market order fallback has no size limit | 🟡 MEDIUM | Could market-order into thin liquidity |
| Hardcoded 0.5% slippage | 🟢 LOW | Should be regime-dependent |
| No multi-exchange routing | 🟢 LOW | Acceptable for V1 (Bybit-only) |

### 4. Regime Detection — [crypto_regime.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/advisory/crypto_regime.py)

| Finding | Severity | Detail |
|---------|----------|--------|
| BTC dominance stubbed | 🟡 MEDIUM | `_get_btc_dominance()` returns `None` — alt-season detection broken |
| Single timeframe (4H only) | 🟡 MEDIUM | 1D regime could be TRENDING while 4H is CHOPPY |
| No regime transition detection | 🟡 MEDIUM | Only knows current state, not when it changed |
| No regime history persistence | 🟢 LOW | Can't analyze regime accuracy over time |
| No on-chain metrics | 🟢 LOW | V2 feature — whale tracking, exchange flows |

### 5. Analyst Agent — [crypto_analyst.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/agents/crypto_analyst.py)

| Finding | Severity | Detail |
|---------|----------|--------|
| **No deterministic technical indicators** | 🔴 HIGH | LLM reasons from raw OHLCV — violates project's own "deterministic math, not LLM" principle |
| No regime context passed to agent | 🟡 MEDIUM | Analyst doesn't know current regime when generating signals |
| Hardcoded universe of 10 | 🟡 MEDIUM | No dynamic add/remove; duplicated in orchestrator.py |
| No multi-timeframe analysis | 🟡 MEDIUM | Only 4H candles |
| No volume profile tool | 🟢 LOW | Equity MCP has it, crypto doesn't |

### 6. Auditor Agent — [crypto_auditor.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/agents/crypto_auditor.py)

| Finding | Severity | Detail |
|---------|----------|--------|
| Double LLM cost with same data | 🟡 MEDIUM | Auditor uses identical tools — no new information source |
| No deterministic pre-filter | 🟡 MEDIUM | Should reject obviously bad signals before LLM call |
| Auditor can rubber-stamp | 🟡 MEDIUM | LLM might agree without independent analysis |
| No accuracy tracking | 🟢 LOW | Can't evaluate if auditor improves outcomes |

### 7. Orchestrator — [orchestrator.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/agents/orchestrator.py) (Crypto Section)

| Finding | Severity | Detail |
|---------|----------|--------|
| Sequential pair scanning | 🟡 MEDIUM | 10 pairs scanned one-by-one (IDX/US use `asyncio.gather`) |
| 4H scan interval too slow | 🟡 MEDIUM | Crypto moves fast; significant moves happen in minutes |
| No event-driven triggers | 🟡 MEDIUM | Should react to funding spikes, OI surges, large liquidations |
| No signal deduplication | 🟡 MEDIUM | Duplicate signals across consecutive scans |
| No priority scanning | 🟢 LOW | BTC/ETH should be scanned more frequently |

### 8. Bot/UX — [crypto_handlers.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/bot/crypto_handlers.py)

| Finding | Severity | Detail |
|---------|----------|--------|
| No proactive alerts | 🟡 MEDIUM | No push notifications for funding spikes, liquidation proximity, price targets |
| No inline keyboards | 🟡 MEDIUM | Main bot has interactive navigation; crypto is text-only |
| `/cscan` blocks for 30s+ | 🟡 MEDIUM | Can timeout; should run async with callback |
| No position management from Telegram | 🟡 MEDIUM | Can't set trailing stop, partial close, adjust leverage |
| No `/ctrades` command | 🟢 LOW | Can't view trade history |
| No `/crisk` dashboard | 🟢 LOW | No dedicated risk view |

### 9. Database Schema — [tables.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/models/tables.py)

| Finding | Severity | Detail |
|---------|----------|--------|
| No local position persistence | 🟡 MEDIUM | Live positions only from Bybit API — if API down, data lost |
| No funding payment tracking | 🟡 MEDIUM | Funding costs invisible |
| PaperPosition not crypto-aware | 🟡 MEDIUM | Missing: leverage, margin_mode, liquidation_price, funding_cost |
| No regime history table | 🟢 LOW | Can't analyze regime transitions |
| No crypto PnL snapshot table | 🟢 LOW | Daily PnL only in Redis (volatile) |

### 10. Infrastructure & Config

| Finding | Severity | Detail |
|---------|----------|--------|
| Crypto universe duplicated | 🟡 MEDIUM | Hardcoded in both [crypto_analyst.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/agents/crypto_analyst.py) and [orchestrator.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/agents/orchestrator.py) |
| No per-pair config | 🟡 MEDIUM | Can't set different leverage/risk for BTC vs PEPE |
| No crypto-specific scheduler jobs | 🟡 MEDIUM | Only 4H scan — missing funding monitoring, position health check, PnL snapshot |
| No health endpoint for crypto bot | 🟢 LOW | Unlike orchestrator, no `/health` |
| AGENTS.md outdated | 🟢 LOW | Doesn't mention any crypto agents |
| No `market_hours.py` crypto support | 🟢 LOW | Missing `is_crypto_open()` (should always return True, but useful for maintenance windows) |
| No test coverage | 🔴 HIGH | Zero tests for any crypto component |

---

## Enhancement Roadmap

### Phase 1: Critical Safety Fixes (Week 1) 🔴

> [!CAUTION]
> These must be completed before any mainnet deployment. They prevent catastrophic losses.

#### 1.1 Unrealized PnL in Kill Switch
#### [MODIFY] [crypto_risk_manager.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/risk/crypto_risk_manager.py)
- Add `get_unrealized_pnl()` that queries Bybit positions API
- `get_daily_pnl()` → sum of realized + unrealized
- Kill switch triggers on total PnL (not just realized)
- Add `check_position_health()` method: scans all open positions for >X% loss

#### 1.2 Liquidation Proximity Warnings
#### [MODIFY] [crypto_risk_manager.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/risk/crypto_risk_manager.py)
- Add `calculate_liquidation_price(entry, leverage, margin_mode)` 
- Add `check_liquidation_proximity(position)` — warn at 20%, alert at 10%, force-close at 5%
- Configurable thresholds: `CRYPTO_LIQUIDATION_WARN_PCT`, `CRYPTO_LIQUIDATION_ALERT_PCT`, `CRYPTO_LIQUIDATION_FORCE_CLOSE_PCT`

#### 1.3 Funding Rate Cost Tracking
#### [NEW] `src/risk/funding_tracker.py`
- Track funding payments per position (8-hour intervals)
- Calculate cumulative funding cost
- Include in total PnL calculation
- Alert when daily funding cost exceeds threshold (e.g., 0.1%)

#### [MODIFY] [config.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/config.py)
- Add `CRYPTO_MAX_LEVERAGE: int = 10`
- Add `CRYPTO_SCAN_INTERVAL_MINUTES: int = 240`
- Add `CRYPTO_FUNDING_ALERT_THRESHOLD: float = 0.05`
- Add `CRYPTO_LIQUIDATION_WARN_PCT: float = 20.0`
- Add `CRYPTO_LIQUIDATION_FORCE_CLOSE_PCT: float = 5.0`
- Add `BYBIT_PROXY: str = ""`

#### 1.4 Deterministic Technical Indicators for Crypto
#### [NEW] `src/advisory/crypto_technicals.py`
- Pure Python RSI, Bollinger Bands, EMA, MACD, ATR calculations
- Operates on kline data from BybitClient
- Tools: `get_crypto_rsi()`, `get_crypto_bollinger()`, `get_crypto_ema()`, `get_crypto_macd()`
- Registered as tools in crypto_analyst — LLM calls these instead of reasoning from raw OHLCV

#### [MODIFY] [crypto_analyst.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/agents/crypto_analyst.py)
- Register deterministic technical tools
- Update system prompt: "NEVER calculate RSI/BB/EMA yourself. Use the provided tools."
- Pass regime context from `crypto_regime.py` into agent prompt

---

### Phase 2: Data & Risk Improvements (Week 2) 🟡

#### 2.1 BybitClient Error Handling & Rate Limits
#### [MODIFY] [bybit_client.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/data/bybit_client.py)
- Return `Result[T, Error]` pattern instead of `dict | None` — distinguish "no data" from "API error"
- Add exponential backoff retry (3 attempts, 1s/2s/4s)
- Add rate limit awareness: respect Bybit's `X-Bapi-Limit-Status` header
- Persist circuit breaker state in Redis
- Add `get_funding_history()` for historical funding rates

#### 2.2 Crypto Database Schema
#### [MODIFY] [tables.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/models/tables.py)
```python
# New tables:
class CryptoPosition(Base):
    """Local mirror of Bybit positions — survives API outages"""
    ticker, side, size, entry_price, leverage, margin_mode,
    liquidation_price, unrealized_pnl, funding_cost_cumulative,
    last_synced_at

class CryptoFundingPayment(Base):
    """Funding payment log — tracks 8-hour funding costs"""
    ticker, funding_rate, payment_amount, timestamp

class CryptoRegimeHistory(Base):
    """Regime transition log for analysis"""
    regime, hurst, adx, volatility_regime, btc_dominance, timestamp

class CryptoPnLSnapshot(Base):
    """Daily PnL snapshots — replaces volatile Redis storage"""
    date, realized_pnl, unrealized_pnl, funding_costs, 
    total_pnl, equity
```

#### 2.3 Correlation-Aware Risk
#### [MODIFY] [crypto_risk_manager.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/risk/crypto_risk_manager.py)
- Add correlation matrix (hardcoded tiers):
  - Tier 1 (BTC, ETH): max 2 positions, max 15% combined
  - Tier 2 (SOL, AVAX, LINK, SUI): max 2, max 10% combined
  - Tier 3 (DOGE, XRP, ADA, PEPE): max 1, max 5% each
- `validate_trade()` checks sector concentration before approving
- Per-asset leverage caps: BTC/ETH → 10x, Alt-L1 → 5x, Meme → 3x

#### 2.4 Implement BTC Dominance
#### [MODIFY] [crypto_regime.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/advisory/crypto_regime.py)
- Implement `_get_btc_dominance()` via CoinGecko free API (or TradingView `BTC.D`)
- Add alt-season / BTC-season classification
- Regime strategy adjustment: in BTC-season, weight BTC/ETH higher; in alt-season, spread across alts

---

### Phase 3: Operational Excellence (Week 3) 🟢

#### 3.1 Crypto Scheduled Jobs
#### [MODIFY] [main.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/main.py)
Add crypto-specific scheduler jobs:
- **Position Health Check** — every 15 min: check unrealized PnL, liquidation proximity, funding accumulation
- **Funding Rate Monitor** — 3x/day at funding times (00:00, 08:00, 16:00 UTC): alert on extreme rates
- **Daily PnL Snapshot** — midnight UTC: persist PnL to `CryptoPnLSnapshot` table
- **Position Sync** — every 5 min: sync Bybit positions to local `CryptoPosition` table

#### 3.2 Parallel Crypto Scanning
#### [MODIFY] [orchestrator.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/agents/orchestrator.py)
- Use `asyncio.gather()` for crypto pairs (like IDX/US already do)
- Add configurable scan interval (move from 4H hardcode to config)
- Add signal deduplication: skip if same ticker+direction signal exists within 4 hours
- Check kill switch between each pair (not just at scan start)

#### 3.3 Deterministic Pre-Filter for Auditor
#### [MODIFY] [crypto_auditor.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/agents/crypto_auditor.py)
- Before LLM call, run deterministic checks:
  - Funding rate > 0.1% → auto-reject LONG
  - RSI > 85 → auto-reject LONG
  - RSI < 15 → auto-reject SHORT
  - OI dropping + price rising → warn (potential squeeze)
- Only call LLM auditor for signals that pass deterministic filter
- Track auditor accuracy in DB

#### 3.4 Proactive Telegram Alerts
#### [MODIFY] [crypto_handlers.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/bot/crypto_handlers.py)
- Add push alert system:
  - 🚨 Liquidation proximity (< 10%)
  - ⚠️ Extreme funding rate (> 0.05%)
  - 📊 Large OI change (> 10% in 4h)
  - 💰 Significant unrealized PnL change (> 5%)
- Add inline keyboards for navigation between views
- Make `/cscan` async with progress callback

#### 3.5 Crypto Universe Config
#### [NEW] `src/advisory/crypto_universe.py`
- Single source of truth for crypto universe (eliminate duplication)
- Per-pair config: max_leverage, risk_tier, min_order_size
- Configurable via JSON or env vars

---

### Phase 4: Alpha & Testing (Week 4) 📊

#### 4.1 Multi-Timeframe Analysis
#### [MODIFY] [crypto_analyst.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/agents/crypto_analyst.py)
- Add `get_crypto_kline_mtf()` tool — fetches 1H, 4H, 1D simultaneously
- System prompt update: "Analyze confluence across timeframes. 4H for entry, 1D for trend."
- Regime per timeframe: can be TRENDING on 1D but CHOPPY on 4H

#### 4.2 Crypto Backtest Integration
#### [MODIFY] [engine.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/backtest/engine.py)
- Add crypto-specific backtester:
  - Funding rate simulation (8-hour deductions)
  - Leverage-adjusted returns
  - Liquidation simulation
  - Slippage model based on orderbook depth
- Add `/cbacktest` Telegram command

#### 4.3 Test Coverage
#### [NEW] `tests/test_agents/test_crypto_analyst.py`
#### [NEW] `tests/test_risk/test_crypto_risk_manager.py`
#### [NEW] `tests/test_data/test_bybit_client.py`
#### [NEW] `tests/test_advisory/test_crypto_regime.py`
- Unit tests for all crypto components
- Mock BybitClient responses
- Test risk validation edge cases (liquidation, funding, correlation)
- Test regime classification with known datasets

#### 4.4 Documentation Update
#### [MODIFY] [AGENTS.md](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/AGENTS.md)
#### [MODIFY] [CLAUDE.md](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/CLAUDE.md)
- Add crypto agents: CryptoAnalyst, CryptoAuditor, CryptoRegime
- Document crypto signal flow
- Document all `/c*` commands
- Add crypto architecture diagram

---

## Comparison: Crypto vs IDX/US Maturity

| Capability | IDX/US | Crypto | Gap |
|-----------|--------|--------|-----|
| Deterministic TA tools | ✅ RSI, BB, EMA via MCP | ❌ LLM reasons from raw OHLCV | 🔴 Critical |
| Regime detection | ✅ VIX/SPY/200SMA + composite | ✅ Hurst/ADX (but BTC.D stubbed) | 🟡 Partial |
| Kill switch (unrealized) | ✅ Paper positions tracked | ❌ Only realized PnL | 🔴 Critical |
| Market intelligence | ✅ IDXMarketIntelligence composite | ❌ No equivalent | 🟡 Missing |
| Position sizing | ✅ ATR-based + regime adjustment | ⚠️ Basic ATR (no crypto adjustments) | 🟡 Partial |
| Scanning frequency | ✅ 4x/day market hours | ⚠️ Every 4H (too slow for 24/7) | 🟡 Slow |
| Parallel scanning | ✅ asyncio.gather | ❌ Sequential | 🟡 Missing |
| Inline keyboards | ✅ Full navigation | ❌ Text-only commands | 🟡 Missing |
| Proactive alerts | ✅ Pre-market battle plan, EOD | ❌ No push alerts | 🟡 Missing |
| Test coverage | ⚠️ Minimal (idx_limits, sizing) | ❌ Zero tests | 🔴 None |
| DB schema | ✅ Full (signals, paper, audit) | ❌ No crypto-specific tables | 🟡 Missing |
| HITL approval flow | ✅ Approve/Reject buttons | ❌ Signals auto-execute | 🟡 Missing |
| Documentation | ✅ AGENTS.md, CLAUDE.md | ❌ Not documented | 🟡 Missing |

---

### Phase 5: Crypto Bot UX Redesign (Week 3-4) 🎨

> [!IMPORTANT]
> The crypto bot's responses are currently **data dumps without context**. Users see numbers but don't understand what they mean, what's safe, or what to do next. The stock bot has a polished `/guide` walkthrough — crypto has nothing equivalent.

#### 5.0 Current State Audit — [crypto_handlers.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/bot/crypto_handlers.py)

| Command | Current Issues |
|---------|---------------|
| `/start` (L51-68) | Bare command list, no explanation of what the bot does, no onboarding flow, no link to `/guide` |
| `/status` (L71-162) | Raw system health dump — user sees "DB: 🟢, Redis: 🟢" but has no idea what these mean or when to worry |
| `/portfolio` (L165-201) | Flat table with no summary context — no leverage shown, no liquidation distance, no % of portfolio per position |
| `/scan` (L204-265) | Blocking (30s+), no progress indicator, reasoning text dumped raw without formatting, no "what should I do?" guidance |
| `/pnl` (L268-298) | Only 4 lines — no breakdown by day/week, no Sharpe, no win rate, no funding costs, no comparison vs benchmark |
| `/risk` (L301-336) | Shows limits but not how close you are to hitting them — no progress bars, no "you have X capacity left" |
| `/kill` (L339-366) | No confirmation prompt — one tap kills everything. No summary of what was closed and at what loss |
| `/sellall` (L369-398) | Same — no confirmation, no summary of PnL impact |
| `/resume` (L401-416) | No post-resume checklist or status summary |
| `/activity` (L576-664) | Good structure but signals show truncated reasoning with no "why" context for non-traders |
| `/audit_agent` (L667-769) | Best-designed command currently, but still missing risk-adjusted metrics (Sharpe, drawdown, expectancy) |
| **Missing** | No `/guide` command, no `/regime` command (exists in stock bot), no `/trades` command, no `/funding` command |

#### 5.1 New `/guide` Command — Full Crypto Trading Walkthrough
#### [MODIFY] [crypto_handlers.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/bot/crypto_handlers.py)

Add a comprehensive `/guide` command (modeled after the stock bot's [guide_cmd](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/bot/handlers.py#L94-L185)) covering:

```
📖 KARSA CRYPTO 101 — Your AI Trading Desk
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🤖 What is Karsa Crypto?
Karsa Crypto is an AI-powered perpetual futures trading
desk that auto-executes signals on Bybit Testnet.

Unlike the stock bot (which advises), the crypto bot
EXECUTES trades automatically after AI analysis + risk
validation. You maintain kill-switch control at all times.

⚡ HOW IT WORKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔍 Step 1: AI Scans Markets (every hour)
  The CryptoAnalyst agent scans 10 perpetual pairs:
  BTC, ETH, SOL, BNB, XRP, ADA, DOGE, AVAX, DOT, LINK

  It analyzes: price action, funding rates, open interest,
  orderbook depth, and long/short ratios.

🛡️ Step 2: Risk Validation (deterministic — no AI)
  Every signal passes through 6 risk gates:
  • Max 1% risk per trade
  • Max 10% in any single position
  • Max 5 concurrent positions
  • 3% daily loss limit
  • Duplicate position check
  • Cooldown after /sellall (15 min)

📈 Step 3: Smart Execution
  Approved signals execute via Smart Order Router:
  • Post-Only limit orders (maker rebates)
  • Auto re-pricing up to 3 times
  • Market order fallback after 30s
  • Automatic stop-loss & take-profit

🌡️ UNDERSTANDING REGIME
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The regime classifier uses BTC as benchmark:

  🟢 TREND_BULL — Strong uptrend detected
     → Full position sizing (1.2x)
     → Momentum strategies active

  🔴 TREND_BEAR — Downtrend detected
     → Reduced sizing (0.5x)
     → Defensive mode

  🟡 MEAN_REVERSION — Range-bound market
     → Moderate sizing (0.8x)
     → Fade extremes

  ⚪ CHOP — No clear direction
     → Minimal sizing (0.5x), higher confidence needed
     → Scans may be skipped entirely

📊 KEY METRICS EXPLAINED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Funding Rate: Fee paid/received every 8 hours.
    Positive → longs pay shorts (crowded long)
    Negative → shorts pay longs (crowded short)
    ⚠️ Above 0.05% = expensive, watch for reversal

  Open Interest: Total outstanding contracts.
    Rising OI + rising price → new money entering bullish
    Falling OI + falling price → longs liquidating
    Rising OI + falling price → new shorts entering

  Unrealized PnL (uPnL): Profit/loss if you closed now.
    Does NOT include funding costs.

  Leverage: Multiplier on your margin.
    Karsa uses max 3x (conservative).
    Higher leverage → closer liquidation price.

🚨 EMERGENCY CONTROLS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  /kill    — NUCLEAR OPTION
             Closes ALL positions immediately
             Activates global halt (no new trades)
             Use when: market crash, fat finger, system issue

  /sellall — Soft exit
             Closes all positions
             Wipes agent memory
             15-minute cooldown (prevents re-entry)
             Use when: taking profits, going away

  /resume  — Reactivate after /kill or /sellall

📋 FULL COMMAND REFERENCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Monitoring:
    /status    — System health + wallet + regime
    /portfolio — Open positions + uPnL
    /pnl       — Performance stats
    /risk      — Risk limits + margin usage
    /regime    — Market regime deep-dive

  Trading:
    /scan [ticker] — Manual scan (+ auto-execute)
    /activity      — Recent signals + closed trades

  Analysis:
    /audit_agent   — 7-day performance review + AI recs
    /funding       — Current funding rates all pairs
    /trades        — Closed trade history

  Safety:
    /kill    — Emergency halt + flatten
    /sellall — Flatten + cooldown
    /resume  — Resume after halt

⚠️ IMPORTANT NOTES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Currently running on Bybit TESTNET (paper money)
• AI generates signals, but risk gates are deterministic
• Kill switch triggers at -1.5% daily P&L
• Max leverage: 3x | Max positions: 5
```

With inline keyboard navigation:
```python
keyboard = build_nav_keyboard([
    [("📊 Status", "cmd_status"), ("💼 Portfolio", "cmd_portfolio")],
    [("🛡️ Risk", "cmd_risk"), ("📋 Activity", "cmd_activity")],
])
```

#### 5.2 Redesign `/start` — Welcoming Onboarding
**Current (L51-68):** Bare command list, feels like a man page.

**New design:**
```
🤖 Karsa Crypto Desk
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AI-powered perpetual futures trading on Bybit.

Signals are generated by AI analyst → audited by AI
risk officer → executed via Smart Order Router.

You retain full kill-switch control at all times.

📊 Quick Start:
  /status    — Check system health & wallet
  /portfolio — View open positions
  /guide     — Full walkthrough (start here!)

🔍 Current State:
  Mode   : Testnet ✅
  Regime : 🟢 TREND_BULL
  Wallet : $10,234.50
  Halt   : Inactive

Type /guide for the complete Karsa Crypto 101.
```

**Key change:** `/start` now shows live state (regime, wallet, halt status) so the user immediately knows if the system is healthy, plus a clear pointer to `/guide`.

#### 5.3 Redesign `/status` — Contextual Health Dashboard
**Current (L71-162):** Raw "DB: 🟢, Redis: 🟢" — meaningless to non-engineers.

**New design:**
```
📊 SYSTEM STATUS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚡ System Health: ALL SYSTEMS GO
  Exchange : 🟢 Bybit Testnet (uid: 12345)
  Database : 🟢 Connected
  Cache    : 🟢 Connected
  Halt     : 🟢 Inactive

💰 Wallet
  Balance  : $10,234.56
  Available: $ 8,100.23   ← can open new trades
  Margin   : $ 2,134.33   (20.9% used)
  uPnL     : $  +156.78

🌡️ Market Regime: 🟢 TREND_BULL
  Hurst: 0.62 (trending) | ADX: 32 (strong)
  → Full sizing active (1.2x multiplier)
  💡 Momentum strategies favored

⏱️ Last scan: 12 min ago | Next: in 48 min
```

**Key changes:**
- "ALL SYSTEMS GO" / "DEGRADED" / "HALTED" summary headline
- Available margin explained as "← can open new trades"
- Regime includes the multiplier and strategy recommendation
- Last/next scan times for operational awareness

#### 5.4 Redesign `/portfolio` — Rich Position Cards
**Current (L165-201):** Flat table, no leverage/liquidation/funding info.

**New design:**
```
💼 CRYPTO PORTFOLIO — 3 positions
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🟢 BTCUSDT  LONG  3x
  Size   : 0.0150 BTC ($975.00)
  Entry  : 65,000.00
  Current: 65,450.00  (+0.69%)
  uPnL   : 🟢 +$6.75
  Liq    : 58,500 (10.0% away)  ← safe
  SL/TP  : 63,500 / 68,000

🔴 ETHUSDT  SHORT  2x
  Size   : 0.5000 ETH ($1,750.00)
  Entry  : 3,500.00
  Current: 3,520.00  (-0.57%)
  uPnL   : 🔴 -$10.00
  Liq    : 5,250 (49.1% away)  ← safe
  SL/TP  : 3,570 / 3,300

  ─── Portfolio Summary ───
  Total Value: $2,725.00 (26.6% of wallet)
  Total uPnL : 🔴 -$3.25
  Margin Used: $908.33
  Capacity   : 2 more positions available

  💡 Tip: /risk for full risk breakdown
```

**Key changes:**
- Each position as a "card" with leverage, liquidation distance, and SL/TP
- Liquidation annotated with "← safe" / "← WARNING" / "← DANGER"
- Portfolio summary shows capacity remaining
- Contextual tip pointing to related commands

#### 5.5 Redesign `/pnl` — Performance Dashboard
**Current (L268-298):** Only 4 lines (open count, uPnL, closed count, realized PnL).

**New design:**
```
📊 CRYPTO PERFORMANCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💰 Current Session
  Open Positions: 3
  Unrealized    : 🟢 +$156.78

📈 Realized (All Time)
  Total Trades : 47
  Win Rate     : 61.7%  (29W / 18L)
  Total P&L    : 🟢 +$1,234.56
  Avg Win      : +2.3%
  Avg Loss     : -1.1%
  Profit Factor: 2.09
  Expectancy   : +$26.27/trade

📅 Recent Performance
  Today : 🟢 +$45.20  (3 trades)
  7 Day : 🟢 +$312.00 (12 trades, 67% win)
  30 Day: 🟢 +$890.00 (35 trades, 60% win)

🏆 Notable Trades
  Best  : SOLUSDT +4.8% (+$96.00)
  Worst : DOGEUSDT -2.1% (-$42.00)

  💡 What these numbers mean:
  Profit Factor > 1.5 = good system
  Win Rate > 55% with 2:1 R/R = strong edge
  Expectancy = avg profit per trade

  📋 /activity for trade-by-trade detail
  🔍 /audit_agent for AI performance review
```

**Key changes:**
- Added profit factor, expectancy, time-bucketed performance
- "What these numbers mean" section educates the user
- Cross-links to related commands

#### 5.6 Redesign `/risk` — Visual Risk Dashboard
**Current (L301-336):** Static limits + raw margin numbers.

**New design:**
```
🛡️ RISK DASHBOARD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Risk Capacity
  Positions  : ███░░ 3/5 (2 slots free)
  Daily P&L  : ████████░░ -1.8% / -3.0% limit
  Margin Used: ████░░░░░░ 38.2%

⚙️ Risk Parameters
  Risk/Trade : 1.0% ($102.35 per trade)
  Max Position: 10.0% ($1,023.45)
  Max Leverage: 3x
  Daily Loss  : 3.0% cap

🔒 Safety Status
  Kill Switch: 🟢 Inactive
  Cooldown   : 🟢 Clear
  Regime Gate: 🟢 TREND_BULL (full sizing)

⚠️ Risk Alerts
  • Daily P&L at -1.8% (60% of limit)
  • ETHUSDT position at -0.57%

  💡 Risk Guide:
  Kill switch auto-triggers at -1.5% daily P&L.
  /kill to manually halt. /resume to restart.
```

**Key changes:**
- Visual progress bars (█░) for capacity utilization
- Risk/Trade shows dollar amount alongside percentage
- Active risk alerts section
- Embedded guide explaining kill switch

#### 5.7 Redesign `/kill` and `/sellall` — Confirmation + Summary
**Current:** No confirmation — one tap executes immediately.

**New design for `/kill`:**
```
🚨 EMERGENCY KILL ACTIVATED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Operator: @dwiki

📊 Positions Closed: 3

  BTCUSDT  LONG  → Closed at 65,450
    PnL: 🟢 +$6.75 (+0.69%)

  ETHUSDT  SHORT → Closed at 3,520
    PnL: 🔴 -$10.00 (-0.57%)

  SOLUSDT  LONG  → Closed at 155.20
    PnL: 🟢 +$12.50 (+1.60%)

  ─── Session Impact ───
  Net Realized: 🟢 +$9.25
  Funding Paid: -$3.40

🔒 Global halt: ACTIVE
  All trading suspended. No new scans.
  Use /resume to reactivate.
```

**Key change:** Shows exactly what was closed and the PnL impact — not just "Positions closed: 3".

#### 5.8 Redesign `/scan` — Async Progress + Structured Result
**Current (L204-265):** Blocking 30s+, raw reasoning dump.

**New design:**
```
🔍 SCAN RESULT: BTCUSDT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ AUTO-EXECUTED

📈 Signal
  Direction  : 🟢 LONG
  Confidence : 75/100 ████████░░
  Leverage   : 3x

💰 Execution
  Entry Fill : $65,123.50
  Stop Loss  : $63,500.00 (-2.5%)
  Take Profit: $68,000.00 (+4.4%)
  Risk/Reward: 1:1.8

🛡️ Risk
  Position   : $975.00 (9.5% of wallet)
  Risk Amount: $102.35 (1.0%)
  Liquidation: $58,500 (10.2% away) ← safe

🧠 AI Reasoning
  Bullish trend structure confirmed:
  • Price above 20/50 EMA on 4H
  • Funding rate negative (-0.02%) — contrarian bullish
  • Rising OI (+5.2%) with price momentum
  • Volume 1.8x above 20-period average

🌡️ Regime: 🟢 TREND_BULL (size: 1.2x)
```

**Key changes:**
- Confidence as visual bar
- Risk/Reward ratio calculated
- Liquidation distance shown immediately
- Reasoning structured as bullet points, not raw LLM dump
- Regime context included

#### 5.9 New Commands to Add

| New Command | Purpose | Priority |
|------------|---------|----------|
| `/guide` | Full crypto 101 walkthrough (see 5.1) | 🔴 HIGH |
| `/regime` | Dedicated regime deep-dive (Hurst, ADX, BTC.D, volatility, strategy recommendation) | 🟡 MEDIUM |
| `/trades` | Closed trade history with PnL (last 20 trades, sortable) | 🟡 MEDIUM |
| `/funding` | Current funding rates for all universe pairs with cost projection | 🟡 MEDIUM |

#### 5.10 Cross-Cutting UX Improvements

**Every command response should include:**

1. **Section title with separator line** — `━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`
2. **Contextual tip at the bottom** — `💡 Tip: /risk for risk breakdown`
3. **Inline keyboard navigation** — every response gets 2-3 relevant nav buttons

**Inline keyboard matrix:**
```
/status    → [Portfolio] [Risk] [Activity]
/portfolio → [P&L] [Risk] [Scan]
/pnl       → [Portfolio] [Activity] [Audit]
/risk      → [Portfolio] [Status] [Activity]
/activity  → [P&L] [Risk] [Portfolio]
/scan      → [Portfolio] [Activity] [Risk]
/regime    → [Status] [Scan] [Portfolio]
/guide     → [Status] [Portfolio] [Risk]
```

**Consistent emoji vocabulary:**
```
🟢 = positive/long/healthy/bullish
🔴 = negative/short/error/bearish
🟡 = warning/mean-reversion/caution
⚪ = neutral/choppy/unknown
🚨 = critical/halt/emergency
💡 = tip/explanation
📊 = data/stats
💼 = portfolio
🛡️ = risk
🔍 = scan/search
📋 = activity/list
🏆 = achievement/best
```

#### 5.11 Implementation Details

#### [MODIFY] [crypto_handlers.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/bot/crypto_handlers.py)

Changes:
1. Add `guide_cmd()` function (~80 lines) — modeled after stock bot's guide
2. Redesign `start_cmd()` — include live state + pointer to /guide
3. Redesign `status_cmd()` — contextual health headline + regime recommendation
4. Redesign `portfolio_cmd()` — position cards with leverage/liquidation/SL-TP
5. Redesign `pnl_cmd()` — add time-bucketed performance, profit factor, expectancy
6. Redesign `risk_cmd()` — add visual progress bars, dollar amounts, alerts
7. Redesign `scan_cmd()` — structured result with confidence bar, risk/reward
8. Redesign `kill_cmd()` — add position-by-position close summary
9. Redesign `sellall_cmd()` — same as kill with cooldown info
10. Redesign `resume_cmd()` — add post-resume status summary
11. Add `regime_cmd()` — dedicated regime deep-dive
12. Add `trades_cmd()` — closed trade history
13. Add `funding_cmd()` — funding rate overview
14. Add inline keyboard navigation to ALL commands
15. Add contextual tips to ALL responses

#### [MODIFY] [crypto_main.py](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/bot/crypto_main.py)
- Register new command handlers: `guide`, `regime`, `trades`, `funding`
- Add callback routing for new inline keyboard buttons

#### [MODIFY] [button_callback](file:///Users/dwiki.nugraha/dwikicode/karsa-claude-trading/src/bot/crypto_handlers.py#L550-L573)
- Add routing for: `cmd_regime`, `cmd_trades`, `cmd_funding`, `cmd_guide`

---

## Verification Plan

### Automated Tests
```bash
# After implementation
pytest tests/test_risk/test_crypto_risk_manager.py -v
pytest tests/test_agents/test_crypto_analyst.py -v
pytest tests/test_data/test_bybit_client.py -v
pytest tests/test_advisory/test_crypto_regime.py -v

# Full test suite
pytest tests/ -v --tb=short
```

### Manual Verification
- Deploy to Bybit **Testnet** (never mainnet without full Phase 1)
- Run 7-day shadow trading validation:
  - Verify kill switch triggers on unrealized PnL
  - Verify liquidation warnings fire correctly
  - Verify funding cost tracking matches Bybit's funding history
  - Verify deterministic TA tools match TradingView values
- Monitor LLM token usage for analyst+auditor costs
- **UX Verification** (Phase 5):
  - Verify every command renders correctly on Telegram mobile (Android + iOS)
  - Verify inline keyboards route correctly between all views
  - Verify `/guide` content is accurate and complete
  - Verify long messages split correctly without breaking HTML tags
  - Test all 14 commands end-to-end with real Bybit testnet data

---

## Open Questions

> [!IMPORTANT]
> **Q1: Phase priority** — Should we implement all of Phase 1 before moving to Phase 2, or can some Phase 2 items (like DB schema) be done in parallel?

> [!IMPORTANT]
> **Q2: WebSocket** — Adding WebSocket support (for real-time fills, prices, liquidation events) is a significant undertaking. Should we prioritize it in Phase 2, or defer to a dedicated V2 sprint?

> [!IMPORTANT]
> **Q3: HITL for crypto** — IDX/US has an approval flow (APPROVE/REJECT buttons). Should crypto signals also go through HITL, or is the Analyst→Auditor→Risk pipeline sufficient for automated execution?

> [!IMPORTANT]
> **Q4: Exchange expansion** — Should the architecture plan for multi-exchange support (Binance, OKX) in V2, or stay Bybit-only?

> [!IMPORTANT]
> **Q5: On-chain data** — Should we scope whale tracking / exchange flow monitoring for Phase 4, or is it out of scope for this cycle?
