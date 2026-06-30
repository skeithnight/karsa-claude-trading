# Plan: Redesign handlers.py with Composable Formatting

**Source**: `docs/REDESIGN_HANDLER.md`
**Complexity**: Medium-Large (1406-line file, 16 commands)

## Summary

Replace manual HTML string concatenation in `src/bot/handlers.py` with a composable formatting engine (`src/utils/format.py`). Eliminates HTML injection, reduces cognitive load, separates presentation from logic.

## /trades Status

`/trades` shows empty because there are 0 paper positions and 0 closed trades. 3 signals exist but none have been approved via HITL yet. Expected for fresh setup — `/trades` will populate once signals are approved and paper trades are executed.

## Approach

**Incremental migration** — not a full rewrite. Create `format.py` first, then refactor handlers command-by-command. Keep all business logic (DB queries, orchestrator calls, validation) exactly as-is.

## Files to Change

| File | Action | Why |
|---|---|---|
| `src/utils/format.py` | CREATE | Composable formatting engine (HTML marker, bold/italic/pre/fmt/join) |
| `src/bot/handlers.py` | UPDATE | Replace f-string HTML with fmt() calls in all 16 commands |
| `src/utils/telegram_helpers.py` | UPDATE | format_pre_table returns raw string (pre() wrapper at call site) |

## Tasks

### Task 1: Create `src/utils/format.py`
Copy from REDESIGN_HANDLER.md section 1. Pure utility, no dependencies. ~56 lines.

### Task 2: Refactor `_reply()` to auto-detect HTML class
When content is `HTML` instance, auto-set `parse_mode="HTML"`.

### Task 3: Refactor commands (one by one)
For each command, replace f-string HTML with `fmt()`/`bold()`/`pre()`:
- `start_cmd`, `guide_cmd` — static text, easy wins
- `scan_cmd`, `status_cmd` — moderate
- `portfolio_cmd`, `analyze_cmd`, `briefing_cmd`, `regime_cmd`, `pnl_cmd` — table-heavy
- `add_cmd`, `remove_cmd`, `edit_cmd` — simple success/error messages
- `trades_cmd`, `audit_cmd` — table + data
- `stop_cmd`, `resume_cmd` — trivial
- `button_callback` — no changes needed (delegates)

### Task 4: Update `format_pre_table` to return raw string
Remove `<pre>` wrapping from the function — callers wrap with `pre()` themselves.

## Validation
- All 16 commands render correctly in Telegram
- User input (tickers, amounts) is auto-escaped by `_safe()`
- No manual `escape_html()` calls needed in handlers
- All 34 tests still pass
- Syntax check passes on all modified files

## Risks
- Large diff — 1400 lines touched
- `send_long_message` HTML chunking must still work with new format
- `pre()` wrapping must not double-escape table content
