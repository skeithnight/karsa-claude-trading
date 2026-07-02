# Risk Profile & Dynamic Crypto Universe — Walkthrough

**Version:** 1.0  
**Date:** 2026-07-02

---

## Overview

Two new features that work together:

1. **Risk Profile Manager** — 3-tier risk profiles (Conservative / Semi-Aggressive / Aggressive) that control confidence thresholds, position sizing, stop/TP multipliers, and max positions across the entire crypto pipeline.

2. **Dynamic Crypto Universe** — Replaces the static 14-coin list with a scored, ranked selection of 8-20 coins refreshed every 4 hours from Bybit data.

---

## Architecture

```
Telegram Bot (/mode, /setmode, /universe, /refresh_universe)
        │
        ▼
┌─────────────────────────────────────────────────┐
│  RiskProfileManager (Redis-backed)              │
│  karsa:state:risk_profile                       │
│  karsa:audit:risk_profile_changes               │
└──────────┬──────────────────┬───────────────────┘
           │                  │
           ▼                  ▼
   ┌───────────────┐  ┌───────────────────┐
   │ Orchestrator   │  │ CryptoAnalyst     │
   │ (signal gate)  │  │ (prompt injection)│
   └───────┬───────┘  └───────────────────┘
           │
           ▼
   ┌───────────────┐
   │CryptoRiskMgr  │
   │(profile-aware │
   │ position size)│
   └───────────────┘

┌─────────────────────────────────────────────────┐
│  UniverseEngine (Bybit-backed)                  │
│  karsa:state:crypto_universe (4h TTL)           │
│                                                 │
│  Fetch 200+ → Filter liquidity → Score → Rank  │
│  Volume 40% + Momentum 30% + Trend 30%          │
└──────────┬──────────────────────────────────────┘
           │
           ▼
   Orchestrator reads universe for scan cycles
```

---

## Risk Profiles

| Parameter | Conservative 🛡️ | Semi-Aggressive ⚖️ | Aggressive 🔥 |
|-----------|:---:|:---:|:---:|
| Min Confidence | 70% | 50% | 35% |
| Max Position Size | 1% | 2.5% | 5% |
| Stop Loss (ATR) | 1.0x | 1.5x | 2.5x |
| Take Profit (ATR) | 2.0x | 3.0x | 4.0x |
| Max Open Positions | 2 | 4 | 6 |
| Max Daily Trades | 3 | 8 | 15 |
| Min 24h Volume | $100M | $50M | $20M |
| Size Multiplier | 0.8x | 1.0x | 1.3x |

**Hard limits** (cannot be overridden by any profile):
- Max position size: 10% of equity
- Daily loss limit: 5%
- Kill switch at 1.5% daily loss

**Cooldown:** 5 minutes between profile changes per user.

---

## Dynamic Universe

**Pipeline:** Bybit API → Liquidity filter ($5M+) → Score → Rank → Cache in Redis (4h TTL)

**Scoring formula (0-100):**
- Volume (40%): log-scale, $100M+ = full score
- Momentum (30%): absolute 24h price change, 5%+ = full score
- Trend/Turnover (30%): volume/OI ratio, >1.0 = full score

**Profile-aware sizing:**
- Conservative: 8 coins
- Semi-Aggressive: 12 coins
- Aggressive: 15 coins

**Core universe** (always included): BTC, ETH, SOL, BNB, XRP

**Fallback:** Static 14-coin list if Bybit API unreachable.

---

## Telegram Commands

### Dashboard (`/dashboard`)
- Shows system vitals, market state, risk profile, universe summary
- **📡 Universe Detail** button → paginated view

### Control Panel (`/control`)
- Risk profile with inline switching buttons (Conservative / Semi-Agg / Aggressive)
- Universe summary with **🔄 Refresh Universe** button
- Emergency controls (Kill, Sell All, Resume)

### `/mode`
- Shows current risk profile with all parameters
- Inline keyboard to switch profiles
- Shows current universe

### `/setmode <profile>`
- Switch profile: `/setmode aggressive`
- Returns cooldown error if <5 min since last change

### `/universe`
- Shows current dynamic universe coin list

### `/refresh_universe`
- Force regenerates the universe (calls Bybit API)

### Universe Detail (inline button)
- Paginated table: 5 coins per page
- Each row: `coin | score | volume | 24h change | last signal`
- Prev/Next navigation buttons

---

## REST API

Base URL: `http://localhost:8000`

```
GET  /api/v1/risk-profile          → current profile config
PUT  /api/v1/risk-profile          → switch profile (body: {profile, reason})
GET  /api/v1/risk-profile/history  → audit log (last 100 changes)
GET  /api/v1/universe              → current universe list
POST /api/v1/universe/refresh      → force regenerate
GET  /api/v1/universe/scores       → universe with scoring details
GET  /metrics                      → Prometheus metrics
```

### Examples

```bash
# Get current profile
curl localhost:8000/api/v1/risk-profile

# Switch to aggressive
curl -X PUT localhost:8000/api/v1/risk-profile \
  -H 'Content-Type: application/json' \
  -d '{"profile":"aggressive","reason":"bull market"}'

# Get universe
curl localhost:8000/api/v1/universe

# Refresh universe
curl -X POST localhost:8000/api/v1/universe/refresh
```

---

## Prometheus Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `karsa_risk_profile_changes_total` | Counter | from_profile, to_profile | Profile change events |
| `karsa_active_risk_profile` | Gauge | — | Current profile (0/1/2) |
| `karsa_signal_rejections_total` | Counter | profile, reason | Signals rejected |
| `karsa_signals_executed_total` | Counter | profile | Signals accepted |
| `karsa_position_size_pct` | Histogram | profile | Position size distribution |
| `karsa_signal_confidence` | Histogram | profile, outcome | Confidence scores |
| `karsa_universe_size` | Gauge | — | Coins in universe |
| `karsa_universe_refresh_total` | Counter | status | Refresh attempts |
| `karsa_universe_refresh_duration_seconds` | Histogram | — | Refresh duration |
| `karsa_universe_coin_score` | Gauge | ticker | Per-coin scores |

---

## Grafana Dashboards

Import from `monitoring/grafana/dashboards/`:

- **karsa-risk-profile.json** — Active profile timeline, profile changes, rejection rate, position size distribution, confidence distribution
- **karsa-crypto-universe.json** — Universe size, refresh status, refresh duration, coin scores, refresh rate

---

## Prometheus Alerts

File: `monitoring/prometheus/alerts/risk_profile_alerts.yml`

| Alert | Condition | Severity |
|-------|-----------|----------|
| RapidProfileSwitching | >10 changes in 5min | warning |
| AggressiveModeActive | >1 hour | info |
| HighRejectionRate | >80% for 30min | warning |
| PositionSizeExceeded | >10% average | critical |
| UniverseRefreshFailed | Failure in 4h window | warning |
| UniverseTooSmall | <5 coins for 30min | warning |
| UniverseRefreshStale | No refresh in >8h | warning |

---

## Files

### New Files
| File | Purpose |
|------|---------|
| `src/risk/profile_manager.py` | RiskProfileManager, profiles, validation, sizing |
| `src/advisory/universe_scorer.py` | Scoring/ranking pure functions |
| `src/metrics/__init__.py` | Metrics package |
| `src/metrics/crypto_metrics.py` | Prometheus metric definitions + helpers |
| `src/api/__init__.py` | API package |
| `src/api/routes.py` | REST API endpoints |
| `db/migrations/add_risk_profile.sql` | risk_profile_audit table + signals column |
| `db/migrations/add_universe_history.sql` | universe_history table |
| `monitoring/grafana/dashboards/karsa-risk-profile.json` | Grafana dashboard |
| `monitoring/grafana/dashboards/karsa-crypto-universe.json` | Grafana dashboard |
| `monitoring/prometheus/alerts/risk_profile_alerts.yml` | Alert rules |

### Modified Files
| File | Changes |
|------|---------|
| `src/config.py` | DEFAULT_RISK_PROFILE, ENABLE_RISK_PROFILE_SWITCHING |
| `src/data/bybit_client.py` | get_all_perps() method |
| `src/advisory/crypto_universe.py` | UniverseEngine class + logger import |
| `src/risk/crypto_risk_manager.py` | Profile-aware evaluate() |
| `src/agents/crypto_analyst.py` | Prompt injection, set_profile() |
| `src/agents/orchestrator.py` | Wire profile_manager + universe_engine |
| `src/bot/crypto_handlers.py` | /mode, /setmode, /universe, /refresh_universe, universe detail |
| `src/bot/crypto_main.py` | Register handlers, wire bot_data |
| `src/main.py` | Init + scheduler + API router + /metrics |
| `pyproject.toml` | prometheus-client dependency |

---

## Deployment

```bash
# 1. Rebuild
docker compose up -d --build

# 2. Run DB migrations (if not auto-applied)
docker exec karsa-postgres psql -U karsa -d karsa -f /app/db/migrations/add_risk_profile.sql
docker exec karsa-postgres psql -U karsa -d karsa -f /app/db/migrations/add_universe_history.sql

# 3. Verify
curl localhost:8000/api/v1/risk-profile
curl localhost:8000/api/v1/universe
curl localhost:8000/metrics | grep karsa_

# 4. Telegram test
# /mode → shows Conservative (default)
# /setmode aggressive → switches
# /dashboard → shows profile + universe
# /control → refresh universe, profile switching
```

---

## How Profile Affects the Pipeline

1. **LLM Prompt** — CryptoAnalyst system prompt includes profile-specific guidance (conservative = high bar, aggressive = lower bar with honesty warning)

2. **Signal Gate** — Orchestrator filters signals by `min_confidence` from active profile (70/50/35)

3. **Risk Manager** — `evaluate()` uses profile's `max_open_positions`, `max_position_size_pct`, `stop_loss_atr_mult`, `take_profit_atr_mult` instead of hardcoded values

4. **Universe Size** — UniverseEngine selects 8/12/15 coins based on profile

5. **Metrics** — All signal accept/reject events tagged with profile name for Grafana analysis
