# Implementation Plan: Trade History Pagination

**Source:** `docs/TRADE_HISTORY_TELEGRAM.md`
**Date:** 2026-07-10

---

## Current State

`trade_history_cmd()` (crypto_handlers.py:1493) already:
- Fetches 15 trades from `ClosedPaperTrade`
- Shows win/loss, PnL%, exit reason, summary
- Uses HTML formatting via `fmt()` (bold, code)

**Missing per doc:**
- No pagination (fetches all 15 at once)
- Uses HTML parse_mode (vulnerable to `<` in AI reasoning)
- No Prev/Next inline keyboard
- No pure Unicode formatting

---

## Changes Required

### 1. Create Formatter (NEW file)

**File:** `src/utils/formatters/trade_history_formatter.py`

```python
class TradeHistoryFormatter:
    PAGE_SIZE = 5

    @staticmethod
    def format_trade(trade) -> str:
        # Pure Unicode, no HTML
        icon = "🟢" if trade.pnl_pct >= 0 else "🔴"
        pnl_str = f"+{trade.pnl_pct:.2f}%" if trade.pnl_pct >= 0 else f"{trade.pnl_pct:.2f}%"
        ts = trade.exit_date.strftime("%m-%d %H:%M") if trade.exit_date else "?"
        reason = (trade.exit_reason or "N/A")[:110]
        return f"{icon} {trade.ticker:<10} {pnl_str:<8} {ts}\n   └─ {reason}"

    @staticmethod
    def build_keyboard(current_page: int, total_pages: int):
        # [ ◀️ Prev ] [ 1 / 3 ] [ Next ▶️ ]
        # callback_data = "karsa:history:page:N"

    @staticmethod
    def build_message(trades, page, total, wins, losses, net_pnl):
        # Header + trades + summary footer + keyboard
```

### 2. Rewrite trade_history_cmd

**File:** `src/bot/crypto_handlers.py` (line ~1493)

- Use `TradeHistoryFormatter.build_message()`
- Set `parse_mode=None` (pure text, no HTML)
- LIMIT 5 OFFSET 0 for first page

### 3. Add Callback Handler

**File:** `src/bot/crypto_handlers.py` (button_callback, line ~1553)

- Match `karsa:history:page:N` pattern
- Parse page number
- Fetch LIMIT 5 OFFSET (page-1)*5
- `edit_message_text()` in-place
- Use `TradeHistoryFormatter.build_message()`

### 4. Remove Performance Button (DONE)

Removed from ACTIVE SESSION keyboard. Main nav keyboard still has it.

---

## DB Query Change

```python
# Before: fetch 15, no pagination
.limit(15)

# After: fetch page slice
.limit(5).offset((page - 1) * 5)

# Separate query for total stats (wins/losses/net)
select(func.count(...)).where(pnl > 0)  # wins
select(func.count(...)).where(pnl <= 0)  # losses
select(func.sum(realized_pnl))           # net
```

---

## Files Changed

| File | Change |
|------|--------|
| `src/utils/formatters/trade_history_formatter.py` | NEW — formatter + keyboard builder |
| `src/bot/crypto_handlers.py` | Rewrite `trade_history_cmd`, add `karsa:history:*` callback |

---

## Verification

1. Send `/history` → shows 5 trades with Prev/Next buttons
2. Click Next → edits message in-place, shows page 2
3. Click Prev → goes back to page 1
4. AI reasoning with `<` symbols renders correctly (parse_mode=None)
5. Summary shows correct W/L/WR/Net across all pages
