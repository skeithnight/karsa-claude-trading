# Monitoring Reference

## Grafana Dashboards
- **URL**: http://localhost:3000 (admin/admin)
- **Dashboard 1**: "Karsa ASM & Trading Operations" (`/d/karsa-asm-ops`) — `monitoring/asm-dashboard.json`
- **Dashboard 2**: "Karsa Trading Operations v2" (`/d/karsa-v2-dashboard`) — `monitoring/grafana-dashboard.json`
- **Dashboard 3**: "ASM - Core Operations" (`/d/asm-core-operations`) — `monitoring/asm-core-operations.json`
  - Top stats: ASM Health, Realized PnL, Unrealized PnL, Cash & Equity, Active Positions
  - Live tables: Open Positions, Trade History (24h)
  - Analytics: Exit Reasons (donut), AI Actions (donut)
  - 10s auto-refresh
- **Refresh**: 10s auto-refresh

## Prometheus Metrics (port 8444)
- `karsa_auto_session_active` — ASM online/offline
- `karsa_auto_session_available_cash_usd` — available USDT balance
- `karsa_auto_session_realized_pnl_usd` — session realized PnL
- `karsa_auto_session_unrealized_pnl_usd` — total unrealized PnL
- `karsa_position_unrealized_pnl_usd{ticker,side}` — per-position PnL
- `karsa_position_entry_price_usd{ticker}` — entry price
- `karsa_position_mark_price_usd{ticker}` — current mark price
- `karsa_position_size_qty{ticker}` — position size in base currency
- `karsa_position_leverage{ticker}` — leverage multiplier
- `karsa_open_positions_count` — number of open positions
- `karsa_signal_rejections_total{reason}` — rejection reasons
- `karsa_kill_switch_active` / `karsa_circuit_breaker_active` — safety states

## Quick Commands
```bash
# View live positions
docker exec karsa-crypto-bot curl -s localhost:8444/metrics | grep -E "position_|auto_session_"

# Check ASM status
docker exec karsa-crypto-bot curl -s localhost:8444/metrics | grep auto_session_active

# View rejection reasons
docker logs karsa-crypto-bot --tail 100 2>&1 | grep signal_rejected
```