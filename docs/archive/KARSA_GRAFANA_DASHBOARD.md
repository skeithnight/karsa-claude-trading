To build a world-class Grafana dashboard for Karsa, we must first establish a **Golden Rule of Trading Telemetry**:

*   **Prometheus** is for *time-series metrics, rates, and distributions* (Cumulative PnL, Slippage, Latency, Win Rates).
*   **PostgreSQL** is for *live tabular state* (Current Open Positions, Exact Trade History, Unrealized PnL). 

If you try to force live position data into Prometheus, your dashboard will be slow and inaccurate. We will use **both** data sources in this dashboard design.

Here is the exact blueprint for the **Karsa Crypto ASM - Command Center** dashboard.

---

### 📊 Dashboard Layout & Panel Configuration

#### **Row 1: ASM Cockpit & System Health**
*This row gives you an instant "Is the bot alive and healthy?" check.*

| Panel Name | Visualization | Data Source | Query / Configuration |
| :--- | :--- | :--- | :--- |
| **ASM Uptime** | Stat | Prometheus | `karsa_asm_uptime_seconds` <br>*(Format: seconds $\rightarrow$ human readable)* |
| **Next Scan** | Stat (Gauge) | Prometheus | `karsa_asm_next_scan_seconds` <br>*(Thresholds: Green < 300s, Yellow < 60s, Red < 10s)* |
| **Total Equity** | Stat | Prometheus | `karsa_wallet_equity_usd` <br>*(Unit: Currency USD)* |
| **Cash vs Margin** | Bar Gauge | Prometheus | **Cash:** `karsa_wallet_cash_usd` <br>**Margin:** `karsa_wallet_margin_usd` |
| **WS Health** | Stat | Prometheus | `time() - karsa_ws_health_tick_timestamp` <br>*(If > 10s, turn Red. Means WS is dead)* |
| **Bybit API Latency** | Time Series | Prometheus | `histogram_quantile(0.95, sum(rate(karsa_bybit_call_duration_seconds_bucket[5m])) by (le))` |

---

#### **Row 2: Realized PnL & Strategy Alpha**
*This row tracks the actual money made and the AI's decision quality.*

| Panel Name | Visualization | Data Source | Query / Configuration |
| :--- | :--- | :--- | :--- |
| **Cumulative Realized PnL** | Time Series | Prometheus | `sum(karsa_realized_pnl_total)` <br>*(Note: Ensure `record_trade_close` increments a Counter, not a Gauge)* |
| **Daily Realized PnL** | Bar Chart | Prometheus | `sum(increase(karsa_realized_pnl_total[24h]))` |
| **Win / Loss Ratio** | Pie Chart | Prometheus | `sum by (result) (karsa_signal_outcome_total)` <br>*(Legend: WIN, LOSS, BREAKEVEN)* |
| **AI Judge Actions** | Pie Chart | Prometheus | `sum by (action) (karsa_ai_decision_total)` <br>*(Legend: HOLD, EXIT, TIGHTEN_STOP)* |
| **LLM Token Cost** | Stat | Prometheus | `sum(karsa_llm_tokens_total{type="output"}) * 0.000015` <br>*(Adjust multiplier based on your LLM pricing)* |

---

#### **Row 3: Live State — Open & Active Positions (Unrealized)**
*This row uses the **PostgreSQL** data source to show exactly what the bot is holding right now.*

| Panel Name | Visualization | Data Source | Query / Configuration |
| :--- | :--- | :--- | :--- |
| **Total Unrealized PnL** | Stat | **Postgres** | `SELECT SUM(unrealized_pnl) as value FROM open_positions;` <br>*(Color: Green if > 0, Red if < 0)* |
| **Active Positions Table** | Table | **Postgres** | `SELECT symbol, side, size, entry_price, leverage, unrealized_pnl, opened_at FROM open_positions ORDER BY opened_at DESC;` |
| **Exposure by Side** | Pie Chart | **Postgres** | `SELECT side, SUM(size * entry_price) as notional_exposure FROM open_positions GROUP BY side;` |
| **Average Leverage** | Stat | **Postgres** | `SELECT AVG(leverage) as value FROM open_positions;` |

---

#### **Row 4: Trade History & Execution Quality**
*This row proves how well the Smart Order Router (SOR) is performing.*

| Panel Name | Visualization | Data Source | Query / Configuration |
| :--- | :--- | :--- | :--- |
| **Recent Trade History** | Table | **Postgres** | `SELECT symbol, side, net_pnl, roi_pct, exit_reason, closed_at FROM closed_paper_trades ORDER BY closed_at DESC LIMIT 50;` <br>*(Enable "Cell display" $\rightarrow$ "Color text" for `net_pnl`)* |
| **Execution Slippage** | Histogram | Prometheus | `histogram_quantile(0.95, sum(rate(karsa_slippage_bps_bucket[5m])) by (le))` <br>*(Shows 95th percentile slippage in Basis Points)* |
| **Limit Fallback Rate** | Stat | Prometheus | `(sum(karsa_limit_fallback_total) / sum(karsa_order_fill_total)) * 100` <br>*(Unit: Percent. If > 20%, your limit orders are pricing too tight)* |
| **Stop Loss Activity** | Time Series | Prometheus | **Breaches:** `sum(karsa_sl_breach_total)` <br>**Executions:** `sum(karsa_sl_execution_total)` |

---

### 🛠️ How to Implement This in Grafana

#### Step 1: Add the PostgreSQL Data Source
1. In Grafana, go to **Connections $\rightarrow$ Data Sources $\rightarrow$ Add data source**.
2. Select **PostgreSQL**.
3. Configure the connection:
   - **Host:** `postgres:5432` (or `localhost:5432` if Grafana is outside Docker)
   - **Database:** `karsa_trading`
   - **User:** `karsa`
   - **Password:** *(Your POSTGRES_PASSWORD)*
   - **TLS/SSL Mode:** `disable` (for local docker network)
4. Click **Save & Test**.

#### Step 2: Build the Panels
1. Create a new Dashboard named **"Karsa Crypto ASM - Command Center"**.
2. Add a new Row for each of the 4 sections above.
3. Click **Add Panel**, select the correct Data Source (Prometheus for metrics, PostgreSQL for tables), and paste the exact queries provided in the tables.

#### Step 3: Crucial Prometheus Code Tweak for Cumulative PnL
For the **Cumulative Realized PnL** panel to work perfectly in Prometheus, ensure your `record_trade_close` function in `src/metrics/crypto_metrics.py` uses a **Counter** (or a Summary/Histogram where you sum the `_sum`), not a Gauge. 

If it's currently a Gauge that just sets the last trade's PnL, change it to this:

```python
# In src/metrics/crypto_metrics.py
from prometheus_client import Counter

# Define as a Counter so it accumulates over time
REALIZED_PNL = Counter(
    'karsa_realized_pnl_total', 
    'Cumulative realized PnL in USD', 
    ['symbol']
)

def record_trade_close(symbol: str, net_pnl: float, ...):
    # ... existing code ...
    REALIZED_PNL.labels(symbol=symbol).inc(net_pnl) # Increment the counter
```

### 🎯 Dashboard Usage Guide

*   **Morning Check:** Look at **Row 1**. Is WS Health green? Is API latency low? If yes, the bot is healthy.
*   **Performance Check:** Look at **Row 2**. Is Cumulative PnL trending up? Is the Win/Loss pie chart mostly green? 
*   **Risk Check:** Look at **Row 3**. Check the **Active Positions Table**. Are there too many positions in the same sector? Is the Unrealized PnL deeply red?
*   **Execution Check:** Look at **Row 4**. If **Limit Fallback Rate** is high, you need to adjust your SOR's limit order pricing logic in `src/risk/sor.py` to be more aggressive. If **Slippage** is high, your order sizes might be too large for the order book depth.