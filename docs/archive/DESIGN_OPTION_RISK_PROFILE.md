# Karsa Trading System - Risk Profile Feature Design

**Document Version:** 1.0  
**Status:** Draft  
**Last Updated:** 2026-07-02  
**Author:** System Architecture Team

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Goals & Non-Goals](#2-goals--non-goals)
3. [Risk Profile Definitions](#3-risk-profile-definitions)
4. [System Architecture](#4-system-architecture)
5. [Component Design](#5-component-design)
6. [Data Models & State Management](#6-data-models--state-management)
7. [API & Interface Design](#7-api--interface-design)
8. [Configuration Schema](#8-configuration-schema)
9. [Security & Safety Constraints](#9-security--safety-constraints)
10. [Testing Strategy](#10-testing-strategy)
11. [Deployment & Migration](#11-deployment--migration)
12. [Monitoring & Observability](#12-monitoring--observability)
13. [Appendix](#13-appendix)

---

## 1. Executive Summary

This document outlines the design for implementing a **multi-tier Risk Profile System** in the Karsa AI Trading Platform. The feature enables dynamic adjustment of trading parameters through three predefined profiles: **Conservative**, **Semi-Aggressive**, and **Aggressive**.

### Key Benefits
- **Adaptive Risk Management:** Adjust exposure based on market conditions without code changes
- **User Control:** Telegram-based profile switching with immediate effect
- **Safety-First:** Hard-coded limits prevent reckless behavior even in Aggressive mode
- **Audit Trail:** All profile changes logged immutably to PostgreSQL

---

## 2. Goals & Non-Goals

### 2.1 Goals ✅

- [ ] Implement three distinct risk profiles with configurable parameters
- [ ] Enable runtime profile switching via Telegram commands
- [ ] Persist active profile state in Redis for sub-millisecond access
- [ ] Log all profile changes to PostgreSQL audit trail
- [ ] Inject profile context into LLM agent prompts
- [ ] Enforce hard safety limits regardless of profile selection
- [ ] Maintain backward compatibility with existing signals

### 2.2 Non-Goals ❌

- [ ] Custom user-defined profiles (v2.0)
- [ ] Automatic profile switching based on market regime (v2.0)
- [ ] Per-asset risk profiles (v2.0)
- [ ] Machine learning optimization of profile parameters (v3.0)

---

## 3. Risk Profile Definitions

### 3.1 Parameter Matrix

| Parameter | Conservative | Semi-Aggressive | Aggressive |
|-----------|--------------|-----------------|------------|
| **Min LLM Confidence** | 70% | 50% | 35% |
| **Max Position Size (% Equity)** | 1.0% | 2.5% | 5.0% |
| **Stop Loss (ATR Multiplier)** | 1.0x | 1.5x | 2.5x |
| **Take Profit (ATR Multiplier)** | 2.0x | 3.0x | 4.0x |
| **Max Open Positions** | 2 | 4 | 6 |
| **Max Daily Trades** | 3 | 8 | 15 |
| **Regime Veto Strictness** | Strict | Moderate | Loose |
| **Correlation Limit** | 0.7 | 0.85 | 0.95 |
| **Min Volume (24h USD)** | $100M | $50M | $20M |

### 3.2 Profile Behavior Specifications

#### Conservative (🛡️)
- **Philosophy:** Capital preservation over growth
- **Target User:** Risk-averse traders, bear markets, uncertain conditions
- **Expected Win Rate:** >65%
- **Expected Trade Frequency:** 1-3 trades/week
- **Max Drawdown Tolerance:** -5% monthly

#### Semi-Aggressive (⚖️)
- **Philosophy:** Balanced risk-reward optimization
- **Target User:** Standard operation, trending markets
- **Expected Win Rate:** >55%
- **Expected Trade Frequency:** 5-10 trades/week
- **Max Drawdown Tolerance:** -10% monthly

#### Aggressive (🔥)
- **Philosophy:** Maximum capital deployment on high-conviction setups
- **Target User:** Bull markets, high-volatility opportunities
- **Expected Win Rate:** >45%
- **Expected Trade Frequency:** 10-20 trades/week
- **Max Drawdown Tolerance:** -15% monthly

---

## 4. System Architecture

### 4.1 High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         TELEGRAM BOT                             │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  /mode                    │  /setmode <profile>           │  │
│  │  /setmode conservative    │  /setmode aggressive          │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                         REDIS STATE LAYER                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Key: karsa:state:risk_profile                           │  │
│  │  Value: "conservative" | "semi_aggressive" | "aggressive"│  │
│  │  TTL: None (persistent until changed)                    │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Key: karsa:audit:risk_profile_changes                   │  │
│  │  Value: JSON array of change events                      │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      ORCHESTRATOR SERVICE                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  RiskProfileManager                                      │  │
│  │  ├── get_active_profile()                                │  │
│  │  ├── validate_signal(signal, profile)                    │  │
│  │  └── calculate_position_size(equity, profile)            │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
              ▼                             ▼
┌─────────────────────┐         ┌─────────────────────┐
│   LLM AGENT LAYER   │         │  EXECUTION ENGINE   │
│                     │         │                     │
│  Prompt Injection:  │         │  ├── PositionSizer  │
│  "Current Profile:  │         │  ├── StopLossCalc   │
│  AGGRESSIVE"        │         │  └── OrderValidator │
└─────────────────────┘         └─────────────────────┘
              │                             │
              └──────────────┬──────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      POSTGRESQL AUDIT LOG                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Table: risk_profile_audit                               │  │
│  │  - id, timestamp, previous_profile, new_profile,         │  │
│  │    changed_by, reason, ip_address                        │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 Component Interaction Flow

```
┌──────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────┐
│ Telegram │     │    Redis     │     │Orchestrator │     │  LLM     │
│   Bot    │     │    State     │     │   Service   │     │  Agent   │
└────┬─────┘     └─────────────┘     ──────┬──────┘     └────┬─────┘
     │                  │                    │                 │
     │ /setmode agg     │                    │                 │
     │─────────────────>│                    │                 │
     │                  │                    │                 │
     │                  │ GET risk_profile   │                 │
     │                  │<───────────────────│                 │
     │                  │                    │                 │
     │                  │ SET risk_profile   │                 │
     │                  │───────────────────>│                 │
     │                  │                    │                 │
     │                  │ PUBLISH profile_   │                 │
     │                  │ _change_event      │                 │
     │                  │───────────────────>│                 │
     │                  │                    │                 │
     │                  │                    │ Inject profile  │
     │                  │                    │ into prompt     │
     │                  │                    │────────────────>│
     │                  │                    │                 │
     │                  │                    │                 │
     │ OK, mode changed │                    │                 │
     │<─────────────────│                    │                 │
     │                  │                    │                 │
```

---

## 5. Component Design

### 5.1 RiskProfileManager Class

**File:** `src/risk/profile_manager.py`

```python
from enum import Enum
from dataclasses import dataclass
from typing import Optional
import redis
import json
from datetime import datetime

class RiskProfile(Enum):
    CONSERVATIVE = "conservative"
    SEMI_AGGRESSIVE = "semi_aggressive"
    AGGRESSIVE = "aggressive"

@dataclass
class RiskProfileConfig:
    name: str
    min_confidence: float
    max_position_size_pct: float
    stop_loss_atr_mult: float
    take_profit_atr_mult: float
    max_open_positions: int
    max_daily_trades: int
    max_correlation: float
    min_volume_24h_usd: float
    regime_veto_strictness: str  # "strict", "moderate", "loose"

class RiskProfileManager:
    """
    Manages risk profile state and validation logic.
    Thread-safe, Redis-backed configuration manager.
    """
    
    REDIS_KEY = "karsa:state:risk_profile"
    REDIS_AUDIT_KEY = "karsa:audit:risk_profile_changes"
    
    PROFILES = {
        RiskProfile.CONSERVATIVE: RiskProfileConfig(
            name="conservative",
            min_confidence=0.70,
            max_position_size_pct=0.01,
            stop_loss_atr_mult=1.0,
            take_profit_atr_mult=2.0,
            max_open_positions=2,
            max_daily_trades=3,
            max_correlation=0.7,
            min_volume_24h_usd=100_000_000,
            regime_veto_strictness="strict"
        ),
        RiskProfile.SEMI_AGGRESSIVE: RiskProfileConfig(
            name="semi_aggressive",
            min_confidence=0.50,
            max_position_size_pct=0.025,
            stop_loss_atr_mult=1.5,
            take_profit_atr_mult=3.0,
            max_open_positions=4,
            max_daily_trades=8,
            max_correlation=0.85,
            min_volume_24h_usd=50_000_000,
            regime_veto_strictness="moderate"
        ),
        RiskProfile.AGGRESSIVE: RiskProfileConfig(
            name="aggressive",
            min_confidence=0.35,
            max_position_size_pct=0.05,
            stop_loss_atr_mult=2.5,
            take_profit_atr_mult=4.0,
            max_open_positions=6,
            max_daily_trades=15,
            max_correlation=0.95,
            min_volume_24h_usd=20_000_000,
            regime_veto_strictness="loose"
        )
    }
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self._ensure_default_profile()
    
    def get_active_profile(self) -> RiskProfileConfig:
        """
        Retrieves the currently active risk profile configuration.
        Falls back to Conservative if not set.
        """
        profile_name = self.redis.get(self.REDIS_KEY)
        if profile_name is None:
            return self.PROFILES[RiskProfile.CONSERVATIVE]
        
        profile_enum = RiskProfile(profile_name.decode('utf-8'))
        return self.PROFILES[profile_enum]
    
    def set_profile(self, profile: RiskProfile, changed_by: str, reason: str = None) -> bool:
        """
        Atomically updates the active risk profile.
        Logs change to audit trail.
        """
        old_profile = self.get_active_profile().name
        new_profile = profile.value
        
        # Update Redis
        self.redis.set(self.REDIS_KEY, new_profile)
        
        # Audit log
        audit_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "previous_profile": old_profile,
            "new_profile": new_profile,
            "changed_by": changed_by,
            "reason": reason,
            "ip_address": self._get_client_ip()  # Optional
        }
        
        self.redis.rpush(self.REDIS_AUDIT_KEY, json.dumps(audit_entry))
        self.redis.ltrim(self.REDIS_AUDIT_KEY, -100, -1)  # Keep last 100
        
        # Publish event for real-time updates
        self.redis.publish("karsa:events:profile_change", json.dumps(audit_entry))
        
        return True
    
    def validate_signal(self, signal: dict) -> tuple[bool, str]:
        """
        Validates a trading signal against current risk profile.
        Returns (is_valid, rejection_reason)
        """
        profile = self.get_active_profile()
        
        # Confidence check
        if signal['confidence'] < profile.min_confidence:
            return False, f"Confidence {signal['confidence']:.2%} below threshold {profile.min_confidence:.2%}"
        
        # Volume check
        if signal.get('volume_24h', 0) < profile.min_volume_24h_usd:
            return False, f"24h volume ${signal['volume_24h']:,.0f} below minimum ${profile.min_volume_24h_usd:,.0f}"
        
        # Open positions check
        open_positions = self._count_open_positions(signal['market'])
        if open_positions >= profile.max_open_positions:
            return False, f"Max open positions ({profile.max_open_positions}) reached"
        
        # Daily trade count check
        daily_trades = self._count_daily_trades(signal['market'])
        if daily_trades >= profile.max_daily_trades:
            return False, f"Max daily trades ({profile.max_daily_trades}) reached"
        
        return True, "OK"
    
    def calculate_position_size(self, equity: float, atr: float, entry_price: float) -> dict:
        """
        Calculates position size and stop-loss based on active profile.
        Returns dict with size, stop_loss, take_profit, risk_amount
        """
        profile = self.get_active_profile()
        
        # Calculate position size
        risk_amount = equity * profile.max_position_size_pct
        
        # Calculate stop loss distance
        stop_loss_distance = atr * profile.stop_loss_atr_mult
        stop_loss_price = entry_price - stop_loss_distance  # For long
        
        # Calculate position quantity
        quantity = risk_amount / stop_loss_distance
        
        # Calculate take profit
        take_profit_distance = atr * profile.take_profit_atr_mult
        take_profit_price = entry_price + take_profit_distance  # For long
        
        return {
            "quantity": quantity,
            "notional_value": quantity * entry_price,
            "stop_loss": stop_loss_price,
            "take_profit": take_profit_price,
            "risk_amount": risk_amount,
            "risk_reward_ratio": profile.take_profit_atr_mult / profile.stop_loss_atr_mult
        }
    
    def _ensure_default_profile(self):
        """Sets Conservative as default if no profile exists."""
        if not self.redis.exists(self.REDIS_KEY):
            self.redis.set(self.REDIS_KEY, RiskProfile.CONSERVATIVE.value)
    
    def _count_open_positions(self, market: str) -> int:
        """Counts currently open positions for a market."""
        # Implementation: Query PostgreSQL or Redis cache
        pass
    
    def _count_daily_trades(self, market: str) -> int:
        """Counts trades executed today for a market."""
        # Implementation: Query PostgreSQL audit log
        pass
    
    def _get_client_ip(self) -> str:
        """Extracts client IP from request context (if available)."""
        # Implementation: Extract from Flask/FastAPI request
        return "unknown"
```

### 5.2 Telegram Bot Commands

**File:** `src/bot/commands/risk_profile.py`

```python
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from src.risk.profile_manager import RiskProfileManager, RiskProfile

async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Displays current risk profile and parameters.
    Command: /mode
    """
    profile_mgr = context.bot_data['risk_profile_manager']
    profile = profile_mgr.get_active_profile()
    
    # Format profile info
    mode_emoji = {
        "conservative": "🛡️",
        "semi_aggressive": "⚖️",
        "aggressive": "🔥"
    }
    
    message = (
        f"<b>{mode_emoji[profile.name]} Current Risk Profile</b>\n\n"
        f"<b>Mode:</b> {profile.name.upper().replace('_', ' ')}\n\n"
        f"<b>Parameters:</b>\n"
        f"├ Min Confidence: {profile.min_confidence:.0%}\n"
        f"├ Max Position Size: {profile.max_position_size_pct:.2%}\n"
        f"├ Stop Loss: {profile.stop_loss_atr_mult}x ATR\n"
        f"├ Take Profit: {profile.take_profit_atr_mult}x ATR\n"
        f"├ Max Open Positions: {profile.max_open_positions}\n"
        f"├ Max Daily Trades: {profile.max_daily_trades}\n"
        f"└ Min 24h Volume: ${profile.min_volume_24h_usd:,.0f}\n\n"
        f"<i>Use /setmode to change profile</i>"
    )
    
    # Create inline keyboard for quick switching
    keyboard = [
        [
            InlineKeyboardButton("🛡️ Conservative", callback_data="mode_conservative"),
            InlineKeyboardButton("⚖️ Semi-Agg", callback_data="mode_semi_aggressive"),
            InlineKeyboardButton("🔥 Aggressive", callback_data="mode_aggressive")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, parse_mode='HTML', reply_markup=reply_markup)

async def cmd_setmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Changes the active risk profile.
    Command: /setmode <conservative|semi_aggressive|aggressive>
    """
    if not context.args:
        await update.message.reply_text(
            "Usage: /setmode <conservative|semi_aggressive|aggressive>\n\n"
            "Examples:\n"
            "/setmode conservative\n"
            "/setmode aggressive"
        )
        return
    
    profile_name = context.args[0].lower()
    
    try:
        profile = RiskProfile(profile_name)
    except ValueError:
        await update.message.reply_text(
            f"❌ Invalid profile: {profile_name}\n"
            "Valid options: conservative, semi_aggressive, aggressive"
        )
        return
    
    # Get user info for audit
    user = update.effective_user
    changed_by = f"telegram_user_{user.id}"
    reason = f"Manual change via Telegram by @{user.username}" if user.username else f"User {user.id}"
    
    # Update profile
    profile_mgr = context.bot_data['risk_profile_manager']
    old_profile = profile_mgr.get_active_profile().name
    profile_mgr.set_profile(profile, changed_by, reason)
    
    # Confirmation message
    mode_emoji = {
        "conservative": "🛡️",
        "semi_aggressive": "⚖️",
        "aggressive": "🔥"
    }
    
    warning = ""
    if profile == RiskProfile.AGGRESSIVE:
        warning = (
            "\n\n⚠️ <b>WARNING:</b> Aggressive mode increases risk exposure.\n"
            "Ensure you understand the implications before proceeding."
        )
    
    await update.message.reply_text(
        f"✅ <b>Risk Profile Updated</b>\n\n"
        f"From: {old_profile.upper()}\n"
        f"To: {mode_emoji[profile.value]} {profile.name.upper()}\n"
        f"{warning}",
        parse_mode='HTML'
    )

async def callback_mode_switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles inline keyboard profile switching.
    """
    query = update.callback_query
    await query.answer()
    
    # Extract profile from callback data
    # Format: "mode_conservative"
    profile_name = query.data.replace("mode_", "")
    
    try:
        profile = RiskProfile(profile_name)
    except ValueError:
        await query.edit_message_text("❌ Invalid profile selection")
        return
    
    # Update profile
    user = query.from_user
    profile_mgr = context.bot_data['risk_profile_manager']
    profile_mgr.set_profile(profile, f"telegram_user_{user.id}", "Inline keyboard switch")
    
    await query.edit_message_text(f"✅ Switched to {profile.name.upper()} mode")

# Handler registration
def register_handlers(application):
    application.add_handler(CommandHandler("mode", cmd_mode))
    application.add_handler(CommandHandler("setmode", cmd_setmode))
    application.add_handler(CallbackQueryHandler(callback_mode_switch, pattern="^mode_"))
```

### 5.3 LLM Prompt Injection

**File:** `src/agents/prompt_builder.py`

```python
def build_crypto_analyst_prompt(signal_request: dict, profile: RiskProfileConfig) -> str:
    """
    Builds the system prompt for the crypto analyst agent,
    injecting risk profile context.
    """
    
    profile_guidance = {
        "conservative": (
            "You are operating in CONSERVATIVE mode. Your priority is capital preservation.\n"
            "- ONLY recommend trades with very high confidence (>70%)\n"
            "- Require multiple confirming indicators (trend, volume, momentum)\n"
            "- Be highly skeptical of breakouts without volume confirmation\n"
            "- Prefer established trends over early reversals\n"
            "- If uncertain, recommend NO TRADE\n"
            "- Risk/reward ratio must be at least 1:2"
        ),
        "semi_aggressive": (
            "You are operating in SEMI-AGGRESSIVE mode. Balance risk and opportunity.\n"
            "- Look for trades with moderate-to-high confidence (>50%)\n"
            "- Accept trend continuation setups with solid momentum\n"
            "- Consider mean reversion in strong trends\n"
            "- Volume confirmation is important but not mandatory\n"
            "- Risk/reward ratio should be at least 1:2.5"
        ),
        "aggressive": (
            "You are operating in AGGRESSIVE mode. Maximize opportunity capture.\n"
            "- Consider trades with lower confidence thresholds (>35%)\n"
            "- Look for early momentum shifts and breakout setups\n"
            "- Accept higher volatility and wider stop losses\n"
            "- Mean reversion strategies are acceptable in ranging markets\n"
            "- Focus on asymmetric risk/reward opportunities (>1:3)\n"
            "- ⚠️ IMPORTANT: Do NOT artificially inflate confidence scores.\n"
            "  Provide honest assessment even in aggressive mode."
        )
    }
    
    base_prompt = f"""
You are Karsa, an expert cryptocurrency trading analyst.

{profile_guidance[profile.name]}

Current Market Context:
- Regime: {signal_request.get('market_regime', 'UNKNOWN')}
- VIX: {signal_request.get('vix', 'N/A')}
- BTC Dominance: {signal_request.get('btc_dominance', 'N/A')}

Analyze the following asset and provide a trading recommendation.

Asset: {signal_request['ticker']}
Timeframe: {signal_request.get('timeframe', '4h')}

Technical Indicators:
{format_indicators(signal_request['indicators'])}

Provide your analysis in the following JSON format:
{{
    "recommendation": "LONG" | "SHORT" | "NO_TRADE",
    "confidence": 0.0-1.0,
    "reasoning": "Detailed explanation...",
    "entry_zone": {{
        "min": price,
        "max": price
    }},
    "stop_loss": price,
    "take_profit": [price1, price2],
    "time_horizon": "hours" | "days" | "weeks",
    "risk_factors": ["factor1", "factor2"]
}}
"""
    
    return base_prompt
```

---

## 6. Data Models & State Management

### 6.1 Redis Schema

```
# Active Profile (String)
Key: karsa:state:risk_profile
Value: "conservative" | "semi_aggressive" | "aggressive"
TTL: None (persistent)

# Audit Log (List)
Key: karsa:audit:risk_profile_changes
Value: [JSON entries, max 100]
Structure: {
    "timestamp": "2026-07-02T10:30:00Z",
    "previous_profile": "conservative",
    "new_profile": "aggressive",
    "changed_by": "telegram_user_123456",
    "reason": "Manual change via Telegram",
    "ip_address": "192.168.1.1"
}
TTL: None (managed by LTRIM)

# Pub/Sub Channel
Channel: karsa:events:profile_change
Payload: Same JSON structure as audit log
```

### 6.2 PostgreSQL Schema

**Table:** `risk_profile_audit`

```sql
CREATE TABLE risk_profile_audit (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    previous_profile VARCHAR(20) NOT NULL,
    new_profile VARCHAR(20) NOT NULL,
    changed_by VARCHAR(100) NOT NULL,
    reason TEXT,
    ip_address INET,
    user_agent TEXT,
    session_id UUID,
    
    -- Constraints
    CHECK (previous_profile IN ('conservative', 'semi_aggressive', 'aggressive')),
    CHECK (new_profile IN ('conservative', 'semi_aggressive', 'aggressive')),
    CHECK (previous_profile != new_profile)
);

-- Indexes for fast querying
CREATE INDEX idx_risk_profile_audit_timestamp ON risk_profile_audit(timestamp DESC);
CREATE INDEX idx_risk_profile_audit_changed_by ON risk_profile_audit(changed_by);

-- Add comment
COMMENT ON TABLE risk_profile_audit IS 'Immutable audit trail of risk profile changes';
```

**Table:** `trade_signals` (existing, enhanced)

```sql
-- Add column to existing table
ALTER TABLE trade_signals 
ADD COLUMN risk_profile_at_generation VARCHAR(20) NOT NULL DEFAULT 'conservative',
ADD COLUMN risk_profile_at_execution VARCHAR(20),
ADD COLUMN position_size_calculated DECIMAL(18, 8),
ADD COLUMN stop_loss_calculated DECIMAL(18, 8),
ADD COLUMN take_profit_calculated DECIMAL(18, 8);

-- Index for analysis
CREATE INDEX idx_trade_signals_risk_profile ON trade_signals(risk_profile_at_generation);
```

---

## 7. API & Interface Design

### 7.1 REST API Endpoints (Optional)

For programmatic control via API:

```yaml
openapi: 3.0.0
info:
  title: Karsa Risk Profile API
  version: 1.0.0

paths:
  /api/v1/risk-profile:
    get:
      summary: Get current risk profile
      operationId: getCurrentRiskProfile
      responses:
        '200':
          description: Current profile configuration
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/RiskProfileConfig'
    
    put:
      summary: Update risk profile
      operationId: updateRiskProfile
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                profile:
                  type: string
                  enum: [conservative, semi_aggressive, aggressive]
                reason:
                  type: string
                  description: Reason for profile change
      responses:
        '200':
          description: Profile updated successfully
        '400':
          description: Invalid profile or validation error

  /api/v1/risk-profile/history:
    get:
      summary: Get risk profile change history
      operationId: getRiskProfileHistory
      parameters:
        - name: limit
          in: query
          schema:
            type: integer
            default: 50
            maximum: 1000
      responses:
        '200':
          description: List of profile changes
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: '#/components/schemas/ProfileChangeAudit'

components:
  schemas:
    RiskProfileConfig:
      type: object
      properties:
        name:
          type: string
          enum: [conservative, semi_aggressive, aggressive]
        min_confidence:
          type: number
          format: float
          minimum: 0
          maximum: 1
        max_position_size_pct:
          type: number
          format: float
        stop_loss_atr_mult:
          type: number
          format: float
        take_profit_atr_mult:
          type: number
          format: float
        max_open_positions:
          type: integer
        max_daily_trades:
          type: integer
        max_correlation:
          type: number
          format: float
        min_volume_24h_usd:
          type: number
          format: float
    
    ProfileChangeAudit:
      type: object
      properties:
        id:
          type: integer
        timestamp:
          type: string
          format: date-time
        previous_profile:
          type: string
        new_profile:
          type: string
        changed_by:
          type: string
        reason:
          type: string
```

### 7.2 Telegram UI Mockup

```
┌─────────────────────────────────────┐
│  🛡️ Current Risk Profile           │
│                                     │
│  Mode: CONSERVATIVE                 │
│                                     │
│  Parameters:                        │
│  ├ Min Confidence: 70%             │
│  ├ Max Position Size: 1.00%        │
│  ├ Stop Loss: 1.0x ATR             │
│  ├ Take Profit: 2.0x ATR           │
│  ├ Max Open Positions: 2           │
│  ├ Max Daily Trades: 3             │
│  └ Min 24h Volume: $100,000,000    │
│                                     │
│  [🛡️ Conservative] [⚖️ Semi-Agg]   │
│           [🔥 Aggressive]           │
│                                     │
│  Use /setmode to change profile     │
└─────────────────────────────────────┘
```

---

## 8. Configuration Schema

### 8.1 Environment Variables

```bash
# .env.example

# Risk Profile Defaults
DEFAULT_RISK_PROFILE=conservative
ENABLE_RISK_PROFILE_SWITCHING=true

# Hard Limits (cannot be overridden by profiles)
HARD_MAX_POSITION_SIZE=0.10          # 10% absolute maximum
HARD_MAX_DAILY_LOSS=0.05             # 5% daily loss limit
HARD_MAX_LEVERAGE=3                  # 3x maximum leverage
KILL_SWITCH_DAILY_LOSS_THRESHOLD=0.015  # 1.5% auto kill switch

# Profile Override Flags (for fine-tuning)
PROFILE_CONSERVATIVE_MIN_CONFIDENCE=0.70
PROFILE_SEMI_AGGRESSIVE_MIN_CONFIDENCE=0.50
PROFILE_AGGRESSIVE_MIN_CONFIDENCE=0.35

# Audit & Logging
RISK_PROFILE_AUDIT_ENABLED=true
RISK_PROFILE_AUDIT_RETENTION_DAYS=365
```

### 8.2 YAML Configuration (Optional)

```yaml
# config/risk_profiles.yaml

risk_profiles:
  conservative:
    display_name: "Conservative 🛡️"
    parameters:
      min_confidence: 0.70
      max_position_size_pct: 0.01
      stop_loss_atr_mult: 1.0
      take_profit_atr_mult: 2.0
      max_open_positions: 2
      max_daily_trades: 3
      max_correlation: 0.7
      min_volume_24h_usd: 100000000
      regime_veto_strictness: "strict"
    description: "Capital preservation focused. High confidence trades only."
  
  semi_aggressive:
    display_name: "Semi-Aggressive ⚖️"
    parameters:
      min_confidence: 0.50
      max_position_size_pct: 0.025
      stop_loss_atr_mult: 1.5
      take_profit_atr_mult: 3.0
      max_open_positions: 4
      max_daily_trades: 8
      max_correlation: 0.85
      min_volume_24h_usd: 50000000
      regime_veto_strictness: "moderate"
    description: "Balanced approach for trending markets."
  
  aggressive:
    display_name: "Aggressive 🔥"
    parameters:
      min_confidence: 0.35
      max_position_size_pct: 0.05
      stop_loss_atr_mult: 2.5
      take_profit_atr_mult: 4.0
      max_open_positions: 6
      max_daily_trades: 15
      max_correlation: 0.95
      min_volume_24h_usd: 20000000
      regime_veto_strictness: "loose"
    description: "Maximum opportunity capture. Higher risk tolerance."

hard_limits:
  max_position_size_pct: 0.10
  max_daily_loss_pct: 0.05
  max_leverage: 3
  kill_switch_daily_loss_pct: 0.015
```

---

## 9. Security & Safety Constraints

### 9.1 Immutable Safety Rules

**These rules CANNOT be overridden by any risk profile:**

1. **Kill Switch Activation:** 
   - Auto-triggers at -1.5% daily loss (hardcoded)
   - Requires manual reset via Telegram admin command

2. **Maximum Position Size:**
   - Absolute cap: 10% of equity (even in Aggressive mode)
   - Per-trade limit enforced at execution engine level

3. **Leverage Cap:**
   - Maximum 3x leverage regardless of profile
   - Enforced at exchange API wrapper level

4. **Daily Loss Limit:**
   - Hard stop at -5% daily loss
   - Prevents Aggressive mode from catastrophic loss

5. **Correlation Check:**
   - Prevents >95% correlated positions even in Aggressive mode
   - Example: Cannot hold LONG BTC and LONG ETH simultaneously if correlation >0.95

### 9.2 Profile Change Authorization

```python
# In Telegram bot middleware
AUTHORIZED_PROFILES = {
    "conservative": "all_users",
    "semi_aggressive": "all_users",
    "aggressive": "admin_only"  # Requires admin role
}

async def check_profile_change_authorization(user_id: int, target_profile: str) -> bool:
    """
    Checks if user is authorized to switch to target profile.
    """
    if target_profile == "aggressive":
        # Check if user is admin
        admin_users = get_admin_user_ids()  # From config or database
        if user_id not in admin_users:
            return False
    
    return True
```

### 9.3 Rate Limiting Profile Changes

```python
# Prevent rapid profile switching (gaming the system)
PROFILE_CHANGE_COOLDOWN_SECONDS = 300  # 5 minutes

async def check_profile_change_cooldown(user_id: int) -> bool:
    """
    Ensures users cannot switch profiles more than once per 5 minutes.
    """
    redis_key = f"karsa:cooldown:profile_change:{user_id}"
    if redis_client.exists(redis_key):
        return False
    
    redis_client.setex(redis_key, PROFILE_CHANGE_COOLDOWN_SECONDS, "1")
    return True
```

---

## 10. Testing Strategy

### 10.1 Unit Tests

```python
# tests/test_risk_profile_manager.py

import pytest
from src.risk.profile_manager import RiskProfileManager, RiskProfile

class TestRiskProfileManager:
    
    @pytest.fixture
    def profile_manager(self, redis_client):
        return RiskProfileManager(redis_client)
    
    def test_default_profile_is_conservative(self, profile_manager):
        """Ensures default profile is Conservative on initialization."""
        profile = profile_manager.get_active_profile()
        assert profile.name == "conservative"
        assert profile.min_confidence == 0.70
    
    def test_set_profile_updates_redis(self, profile_manager, redis_client):
        """Tests that profile changes are persisted to Redis."""
        profile_manager.set_profile(
            RiskProfile.AGGRESSIVE, 
            "test_user", 
            "Test change"
        )
        
        stored_profile = redis_client.get("karsa:state:risk_profile")
        assert stored_profile.decode('utf-8') == "aggressive"
    
    def test_validate_signal_below_confidence(self, profile_manager):
        """Tests signal rejection due to low confidence."""
        signal = {
            "confidence": 0.30,
            "volume_24h": 150_000_000,
            "market": "crypto"
        }
        
        is_valid, reason = profile_manager.validate_signal(signal)
        assert is_valid == False
        assert "below threshold" in reason
    
    def test_calculate_position_size_conservative(self, profile_manager):
        """Tests position size calculation in Conservative mode."""
        profile_manager.set_profile(RiskProfile.CONSERVATIVE, "test")
        
        result = profile_manager.calculate_position_size(
            equity=10000,
            atr=50,
            entry_price=2000
        )
        
        assert result["risk_amount"] == 100  # 1% of 10000
        assert result["stop_loss"] == 1950   # 2000 - (50 * 1.0)
        assert result["take_profit"] == 2100 # 2000 + (50 * 2.0)
    
    def test_audit_log_created_on_profile_change(self, profile_manager, redis_client):
        """Tests that profile changes are logged to audit trail."""
        profile_manager.set_profile(
            RiskProfile.SEMI_AGGRESSIVE,
            "user_123",
            "Test reason"
        )
        
        audit_logs = redis_client.lrange("karsa:audit:risk_profile_changes", 0, -1)
        assert len(audit_logs) > 0
        
        import json
        latest_log = json.loads(audit_logs[-1])
        assert latest_log["new_profile"] == "semi_aggressive"
        assert latest_log["changed_by"] == "user_123"
```

### 10.2 Integration Tests

```python
# tests/integration/test_risk_profile_flow.py

@pytest.mark.integration
class TestRiskProfileIntegration:
    
    def test_full_signal_flow_with_profile_filtering(self):
        """
        Tests end-to-end flow:
        Signal Generation -> Profile Validation -> Execution Decision
        """
        # 1. Set Conservative profile
        profile_mgr.set_profile(RiskProfile.CONSERVATIVE, "test")
        
        # 2. Generate low-confidence signal (40%)
        signal = generate_test_signal(confidence=0.40)
        
        # 3. Validate against Conservative profile
        is_valid, reason = profile_mgr.validate_signal(signal)
        assert is_valid == False  # Should be rejected
        
        # 4. Switch to Aggressive profile
        profile_mgr.set_profile(RiskProfile.AGGRESSIVE, "test")
        
        # 5. Validate same signal against Aggressive profile
        is_valid, reason = profile_mgr.validate_signal(signal)
        assert is_valid == True  # Should pass
        
        # 6. Calculate position size
        position = profile_mgr.calculate_position_size(10000, 50, 2000)
        assert position["risk_amount"] == 500  # 5% of 10000
```

### 10.3 Load Testing

```python
# tests/load/test_profile_switching.py

import asyncio
import time

async def test_rapid_profile_switching():
    """
    Tests system behavior under rapid profile switching.
    Should handle 100 switches/second without degradation.
    """
    profile_mgr = RiskProfileManager(redis_client)
    profiles = [RiskProfile.CONSERVATIVE, RiskProfile.AGGRESSIVE, RiskProfile.SEMI_AGGRESSIVE]
    
    start_time = time.time()
    
    for i in range(1000):
        profile = profiles[i % 3]
        profile_mgr.set_profile(profile, f"load_test_{i}", "Load test")
    
    elapsed = time.time() - start_time
    ops_per_second = 1000 / elapsed
    
    assert ops_per_second > 100, f"Expected >100 ops/sec, got {ops_per_second}"
```

### 10.4 Test Coverage Requirements

| Component | Minimum Coverage | Critical Paths |
|-----------|-----------------|----------------|
| `RiskProfileManager` | 95% | Profile switching, validation, position sizing |
| `Telegram Commands` | 90% | Command parsing, authorization, error handling |
| `Prompt Builder` | 85% | Profile injection, JSON formatting |
| `Audit Logging` | 100% | All profile changes must be logged |

---

## 11. Deployment & Migration

### 11.1 Database Migration

```sql
-- migrations/20260702_add_risk_profile_support.sql

-- 1. Create risk_profile_audit table
CREATE TABLE risk_profile_audit (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    previous_profile VARCHAR(20) NOT NULL,
    new_profile VARCHAR(20) NOT NULL,
    changed_by VARCHAR(100) NOT NULL,
    reason TEXT,
    ip_address INET,
    user_agent TEXT,
    session_id UUID,
    
    CHECK (previous_profile IN ('conservative', 'semi_aggressive', 'aggressive')),
    CHECK (new_profile IN ('conservative', 'semi_aggressive', 'aggressive')),
    CHECK (previous_profile != new_profile)
);

CREATE INDEX idx_risk_profile_audit_timestamp ON risk_profile_audit(timestamp DESC);
CREATE INDEX idx_risk_profile_audit_changed_by ON risk_profile_audit(changed_by);

-- 2. Enhance trade_signals table
ALTER TABLE trade_signals 
ADD COLUMN IF NOT EXISTS risk_profile_at_generation VARCHAR(20) NOT NULL DEFAULT 'conservative',
ADD COLUMN IF NOT EXISTS risk_profile_at_execution VARCHAR(20),
ADD COLUMN IF NOT EXISTS position_size_calculated DECIMAL(18, 8),
ADD COLUMN IF NOT EXISTS stop_loss_calculated DECIMAL(18, 8),
ADD COLUMN IF NOT EXISTS take_profit_calculated DECIMAL(18, 8);

CREATE INDEX IF NOT EXISTS idx_trade_signals_risk_profile ON trade_signals(risk_profile_at_generation);

-- 3. Initialize Redis with default profile
-- (This is done programmatically on application startup)

-- 4. Grant permissions
GRANT SELECT, INSERT ON risk_profile_audit TO karsa_app;
GRANT SELECT, UPDATE ON trade_signals TO karsa_app;

COMMENT ON TABLE risk_profile_audit IS 'Immutable audit trail of risk profile changes';
```

### 11.2 Docker Compose Updates

```yaml
# docker-compose.yml

services:
  orchestrator:
    build: ./orchestrator
    environment:
      - DEFAULT_RISK_PROFILE=conservative
      - ENABLE_RISK_PROFILE_SWITCHING=true
      - REDIS_URL=redis://redis:6379/0
      - DATABASE_URL=postgresql://karsa:password@postgres:5432/karsa
    volumes:
      - ./config/risk_profiles.yaml:/app/config/risk_profiles.yaml:ro
    depends_on:
      - redis
      - postgres
  
  telegram-bot:
    build: ./telegram-bot
    environment:
      - REDIS_URL=redis://redis:6379/0
      - ADMIN_USER_IDS=123456789,987654321  # Comma-separated Telegram user IDs
    depends_on:
      - redis
  
  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
  
  postgres:
    image: postgres:15-alpine
    environment:
      - POSTGRES_DB=karsa
      - POSTGRES_USER=karsa
      - POSTGRES_PASSWORD=password
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./migrations:/docker-entrypoint-initdb.d

volumes:
  redis_data:
  postgres_data:
```

### 11.3 Rollout Plan

**Phase 1: Preparation (Day 1-2)**
- [ ] Run database migrations on staging
- [ ] Deploy updated code to staging environment
- [ ] Verify Redis connectivity and key structure
- [ ] Test all Telegram commands in staging

**Phase 2: Staging Validation (Day 3-5)**
- [ ] Run integration tests against staging
- [ ] Perform load testing (1000 profile switches)
- [ ] Validate audit logging to PostgreSQL
- [ ] Test LLM prompt injection with all profiles
- [ ] Verify position sizing calculations

**Phase 3: Production Deployment (Day 6)**
- [ ] Schedule maintenance window (low-activity period)
- [ ] Backup PostgreSQL database
- [ ] Backup Redis data
- [ ] Run database migrations
- [ ] Deploy updated containers (rolling update)
- [ ] Verify default profile is Conservative
- [ ] Test /mode command in production

**Phase 4: Monitoring (Day 7-14)**
- [ ] Monitor error rates in logs
- [ ] Track profile change frequency
- [ ] Analyze signal rejection rates per profile
- [ ] Gather user feedback from Telegram

**Phase 5: Feature Enablement (Day 15+)**
- [ ] Enable Aggressive mode for admin users only
- [ ] Review audit logs for unauthorized attempts
- [ ] Consider expanding access based on usage patterns

### 11.4 Rollback Plan

If critical issues arise:

```bash
# 1. Revert to previous profile (Conservative)
docker exec -it karsa-redis-1 redis-cli SET karsa:state:risk_profile conservative

# 2. Disable profile switching via environment variable
docker-compose exec orchestrator sh -c "echo 'ENABLE_RISK_PROFILE_SWITCHING=false' >> .env"
docker-compose restart orchestrator

# 3. Rollback database schema (if needed)
docker-compose exec postgres psql -U karsa -d karsa << EOF
  ALTER TABLE trade_signals DROP COLUMN IF EXISTS risk_profile_at_generation;
  ALTER TABLE trade_signals DROP COLUMN IF EXISTS risk_profile_at_execution;
  DROP TABLE IF EXISTS risk_profile_audit CASCADE;
EOF

# 4. Revert Docker images
docker-compose pull orchestrator telegram-bot
docker-compose up -d orchestrator telegram-bot
```

---

## 12. Monitoring & Observability

### 12.1 Key Metrics to Track

```python
# src/metrics/risk_profile_metrics.py

from prometheus_client import Counter, Gauge, Histogram

# Counters
PROFILE_CHANGE_COUNT = Counter(
    'karsa_risk_profile_changes_total',
    'Total number of risk profile changes',
    ['from_profile', 'to_profile', 'changed_by_type']
)

SIGNAL_REJECTION_COUNT = Counter(
    'karsa_signal_rejections_total',
    'Total number of signals rejected by risk profile',
    ['profile', 'rejection_reason']
)

# Gauges
ACTIVE_PROFILE = Gauge(
    'karsa_active_risk_profile',
    'Currently active risk profile (encoded as number)',
    ['profile_name']
)
# 0=conservative, 1=semi_aggressive, 2=aggressive

OPEN_POSITIONS = Gauge(
    'karsa_open_positions',
    'Number of currently open positions',
    ['profile', 'market']
)

# Histograms
POSITION_SIZE_DISTRIBUTION = Histogram(
    'karsa_position_size_pct',
    'Distribution of position sizes as % of equity',
    ['profile'],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.075, 0.1]
)

CONFIDENCE_DISTRIBUTION = Histogram(
    'karsa_signal_confidence',
    'Distribution of signal confidence scores',
    ['profile', 'executed_or_rejected'],
    buckets=[0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
)
```

### 12.2 Grafana Dashboard Panels

**Dashboard: Risk Profile Analytics**

```
Panel 1: Active Profile Over Time
- Type: State timeline
- Query: karsa_active_risk_profile

Panel 2: Profile Changes (24h)
- Type: Bar chart
- Query: rate(karsa_risk_profile_changes_total[1h])

Panel 3: Signal Rejection Rate by Profile
- Type: Pie chart
- Query: sum by (profile, rejection_reason) (karsa_signal_rejections_total)

Panel 4: Position Size Distribution
- Type: Histogram
- Query: karsa_position_size_pct

Panel 5: Confidence Score vs Execution
- Type: Scatter plot
- X-axis: Confidence score
- Y-axis: Executed (1) or Rejected (0)
- Color: Profile

Panel 6: Daily P&L by Profile
- Type: Time series
- Query: Sum of P&L grouped by risk_profile_at_execution
```

### 12.3 Alerting Rules

```yaml
# prometheus/alerts/risk_profile_alerts.yml

groups:
  - name: risk_profile_alerts
    interval: 30s
    rules:
      - alert: RapidProfileSwitching
        expr: rate(karsa_risk_profile_changes_total[5m]) > 10
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "Rapid risk profile switching detected"
          description: "Profile changed {{ $value }} times in 5 minutes"
      
      - alert: AggressiveModeActive
        expr: karsa_active_risk_profile == 2
        for: 1h
        labels:
          severity: info
        annotations:
          summary: "Aggressive mode has been active for >1 hour"
          description: "Current profile: AGGRESSIVE. Monitor closely."
      
      - alert: HighRejectionRate
        expr: |
          sum(rate(karsa_signal_rejections_total[1h])) 
          / 
          sum(rate(karsa_signal_rejections_total[1h])) + sum(rate(karsa_signals_executed_total[1h]))
          > 0.8
        for: 30m
        labels:
          severity: warning
        annotations:
          summary: "Signal rejection rate >80%"
          description: "Most signals are being rejected. Check profile settings."
      
      - alert: PositionSizeExceeded
        expr: karsa_position_size_pct > 0.10
        labels:
          severity: critical
        annotations:
          summary: "Position size exceeded 10% hard limit"
          description: "Position size: {{ $value }}% of equity"
```

### 12.4 Logging Schema

```python
# src/logging/risk_profile_logger.py

import structlog
from datetime import datetime

logger = structlog.get_logger(__name__)

def log_profile_change(previous: str, new: str, changed_by: str, reason: str):
    logger.info(
        "risk_profile_changed",
        event_type="profile_change",
        timestamp=datetime.utcnow().isoformat(),
        previous_profile=previous,
        new_profile=new,
        changed_by=changed_by,
        reason=reason,
        severity="info" if new != "aggressive" else "warning"
    )

def log_signal_validation(signal_id: str, profile: str, confidence: float, 
                          is_valid: bool, rejection_reason: str = None):
    logger.info(
        "signal_validated",
        event_type="signal_validation",
        signal_id=signal_id,
        profile=profile,
        confidence=confidence,
        is_valid=is_valid,
        rejection_reason=rejection_reason
    )

def log_position_sized(signal_id: str, profile: str, equity: float, 
                       position_size_pct: float, stop_loss: float):
    logger.info(
        "position_sized",
        event_type="position_sizing",
        signal_id=signal_id,
        profile=profile,
        equity=equity,
        position_size_pct=position_size_pct,
        stop_loss=stop_loss,
        risk_amount=equity * position_size_pct
    )
```

---

## 13. Appendix

### 13.1 Glossary

| Term | Definition |
|------|------------|
| **ATR** | Average True Range - volatility indicator |
| **Confidence Score** | LLM-generated probability (0-1) of trade success |
| **Kill Switch** | Emergency circuit breaker that halts all trading |
| **Position Sizing** | Calculation of trade quantity based on risk parameters |
| **Regime Veto** | Strategy disabling based on market regime (Bull/Bear/Neutral) |
| **Risk/Reward Ratio** | Take profit distance divided by stop loss distance |

### 13.2 References

- [Karsa Original Repository](https://github.com/skeithnight/karsa-claude-trading/)
- [Redis Data Structures](https://redis.io/docs/data-types/)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Prometheus Metrics](https://prometheus.io/docs/concepts/metric_types/)

### 13.3 Future Enhancements (v2.0)

- [ ] **Auto-Profile Switching:** ML model to automatically switch profiles based on market regime
- [ ] **Custom Profiles:** User-defined profile creation via UI
- [ ] **Per-Asset Profiles:** Different risk settings for BTC vs altcoins
- [ ] **Time-Based Profiles:** Aggressive during Asian session, Conservative during US session
- [ ] **Profile Backtesting:** Historical simulation of different profile performance
- [ ] **Multi-User Profiles:** Separate profiles for different Telegram users

### 13.4 Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-07-02 | Three predefined profiles | Simplicity over flexibility for v1.0 |
| 2026-07-02 | Redis for state storage | Sub-millisecond access required for validation |
| 2026-07-02 | PostgreSQL for audit trail | Immutable, queryable, compliant with financial regulations |
| 2026-07-02 | Aggressive mode admin-only | Prevents inexperienced users from excessive risk |
| 2026-07-02 | 5-minute cooldown on changes | Prevents rapid switching and system gaming |
| 2026-07-02 | Hard limits cannot be overridden | Safety-first design principle |

---

**Document End**

*This design document is confidential and intended for the Karsa development team only.*