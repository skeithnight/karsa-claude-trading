### 📊 1. Prometheus Metrics (For Real-Time Grafana Dashboards)
*These are defined in `src/metrics/crypto_metrics.py` and wired to business logic.*

| Metric Name | Type | Purpose | Wiring Status | Where it's called |
| :--- | :--- | :--- | :---: | :--- |
| **Performance Gate Metrics** | | | | |
| `karsa_perf_gate_zone` | Counter | Tracks which zone a position hits (HARD_FAIL, CLEAR_WIN, AMBIGUOUS, etc.) | ✅ **WIRED** | `performance_gate.py` (Inside `evaluate()`) |
| `karsa_perf_gate_exit` | Counter | Tracks the exact reason for exiting (dynamic_stop, hard_fail, ai_judge, etc.) | ✅ **WIRED** | `performance_gate.py` (Right before returning `EXIT`) |
| `karsa_perf_gate_dynamic_stop_active`| Gauge | 1 if a dynamic trailing stop is active, 0 if not. | ✅ **WIRED** | `performance_gate.py` (Check `pos.dynamic_stop_pct`) |
| `karsa_perf_gate_drawdown_trigger_total`| Counter | How many times mid-checkpoint drawdown triggered the AI. | ✅ **WIRED** | `performance_gate.py` (Inside drawdown `if` block) |
| `karsa_perf_gate_price_stale_skip_total`| Counter | How many times Hard Fail was skipped due to stale RPC data. | ✅ **WIRED** | `performance_gate.py` (Inside stale price `if` block) |
| `karsa_perf_gate_consecutive_holds` | Gauge | Current consecutive AI hold count for a specific position. | ✅ **WIRED** | `performance_gate.py` (After fetching Redis hold count) |
| **Regime & Intelligence Metrics** | | | | |
| `karsa_scan_duration_seconds` | Histogram | Full crypto scan cycle time | ✅ **WIRED** | `orchestrator.py` (Wrap scan in timer) |
| `karsa_crypto_regime` | Gauge | Current crypto market regime | ✅ **WIRED** | `orchestrator.py` (After regime fetch) |
| `karsa_btc_dominance_pct` | Gauge | BTC market dominance % | ✅ **WIRED** | `orchestrator.py` (After regime fetch) |
| **Session Performance Metrics** | | | | |
| `karsa_session_return_pct` | Gauge | Total return % since session start | ✅ **WIRED** | `autonomous_session.py` (_update_metrics) |
| `karsa_max_drawdown_pct` | Gauge | Max drawdown % in current session | ✅ **WIRED** | `autonomous_session.py` (_update_metrics) |
| `karsa_profit_factor` | Gauge | Gross profit / gross loss ratio | ✅ **WIRED** | `autonomous_session.py` (_update_metrics) |
| `karsa_total_trades_count` | Gauge | Total trades executed in session | ✅ **WIRED** | `autonomous_session.py` (_update_metrics) |
| `karsa_winning_trades_count` | Gauge | Winning trades in session | ✅ **WIRED** | `autonomous_session.py` (_update_metrics) |
| `karsa_losing_trades_count` | Gauge | Losing trades in session | ✅ **WIRED** | `autonomous_session.py` (_update_metrics) |
| **Position-Level Metrics** | | | | |
| `karsa_position_age_hours` | Gauge | Age of open position in hours | ✅ **WIRED** | `autonomous_session.py` (position loop) |
| `karsa_funding_cost_8h_usd` | Gauge | Funding cost per 8h interval per position | ✅ **WIRED** | `autonomous_session.py` (position loop) |
| `karsa_correlation_loss_ratio` | Gauge | Fraction of positions losing within correlation tier | ✅ **WIRED** | `circuit_breaker.py` (check_correlation_cascade) |
| **Infrastructure Metrics** | | | | |
| `karsa_daily_loss_pct` | Gauge | Current day unrealized+realized loss as % of equity | ✅ **WIRED** | `main_crypto.py` (_job_kill_switch) |
| **NEW: LLM & Token Metrics** | | | | |
| `karsa_llm_tokens_input_total` | Counter | Total input tokens consumed by LLM calls | ✅ **DEFINED** | Call `record_llm_tokens()` after LLM calls |
| `karsa_llm_tokens_output_total` | Counter | Total output tokens consumed by LLM calls | ✅ **DEFINED** | Call `record_llm_tokens()` after LLM calls |
| **NEW: Signal Outcome Metrics** | | | | |
| `karsa_signal_outcome_total` | Counter | Signal outcomes by type | ✅ **DEFINED** | Call `record_signal_outcome()` when trade closes |
| **NEW: Daily Trade Count** | | | | |
| `karsa_daily_trade_count` | Gauge | Number of trades executed today | ✅ **DEFINED** | Call `update_daily_trade_count()` |

---

### 🗄️ 2. Database Analytics Tables (For the "Knowledge Base")
*These tables are designed to store historical data so you can run SQL queries to improve the bot. The tables might exist in your schema, but no code is inserting data into them.*

| Table Name | Purpose | Wiring Status | Where it needs to be called |
| :--- | :--- | :---: | :--- |
| `ai_judge_decisions` | Tracks every AI thought, confidence score, tier used, and final outcome to measure AI ROI. | ❌ **NOT WIRED** | `position_judge.py` (Add `session.add()` after AI returns a decision) |
| `position_checkpoint_history` | Tracks the exact journey of a trade. Did it pass the 1h? Fail the 4h? What was the PnL at each step? | ❌ **NOT WIRED** | `performance_gate.py` (Add `session.add()` every time a checkpoint is evaluated) |
| `position_snapshots` | Records the price and PnL every 5 minutes to draw the exact equity curve of every trade. | ❌ **NOT WIRED** | `main_crypto.py` / ASM Loop (Add `session.add()` on every 5-min loop iteration) |

---

### 📝 Summary of Implementation Status

**Completed (Phases 1-6):**
- ✅ Performance Gate v2 metrics wired (6 metrics)
- ✅ Regime/Intelligence metrics wired (3 metrics)
- ✅ Session Performance metrics wired (6 metrics)
- ✅ Position-Level metrics wired (3 metrics)
- ✅ Infrastructure metrics wired (1 metric)
- ✅ New metric definitions added (4 metrics)

**Remaining (AI Judge Metrics):**
- ❌ `karsa_ai_judge_decisions_total` - NOT WIRED
- ❌ `karsa_ai_judge_tier_used` - NOT WIRED
- ❌ `karsa_ai_judge_escalation_total` - NOT WIRED
- ❌ `karsa_ai_judge_confidence_score` - NOT WIRED
- ❌ `karsa_ai_judge_latency_seconds` - NOT WIRED

**Database Tables (Not in scope):**
- ❌ `ai_judge_decisions` - NOT WIRED
- ❌ `position_checkpoint_history` - NOT WIRED
- ❌ `position_snapshots` - NOT WIRED