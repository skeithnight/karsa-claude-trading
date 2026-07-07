### Profit Leak #1: The "Breakeven" Fallacy (Fees & Slippage)
**The Problem:** Your "breakeven gates" are set to `0.0%` (e.g., `meme_1h_not_breakeven`, `std_12h_flat`). 
However, in crypto (especially on DEXs), a round-trip trade costs money (e.g., 0.5% fees + 0.5% slippage = 1.0% total cost). If your bot exits a trade at exactly `0.0%` gain, **you are actually losing 1.0% to fees.** Over hundreds of trades, this will bleed your account dry.

**The Fix:** Your "breakeven" checkpoints must actually be set to your average round-trip cost (usually `+1.0%` or `+1.5%`) so that when the bot exits, you are actually keeping money.

```python
# In src/risk/performance_gate.py, update the "flat/breakeven" checkpoints:

# MEME: Changed from 0.0 to +1.0% to cover fees
Checkpoint(after_minutes=60, min_gain_pct=1.0, reason="meme_1h_not_profitable"),

# STANDARD: Changed from 0.0 to +1.0%
Checkpoint(after_minutes=720, min_gain_pct=1.0, reason="std_12h_flat"),

# CORE: Changed from 0.0 to +1.0%
Checkpoint(after_minutes=4320, min_gain_pct=1.0, reason="core_72h_flat"),
```

### Profit Leak #2: No Trailing Stop (Giving Back Massive Pumps)
**The Problem:** Your `CLEAR_WIN_THRESHOLD = 3.0`. If a Meme coin pumps **+20%** in 10 minutes, the ASM says "Clear Win! -> HOLD". 
But what happens if the coin immediately dumps back to **+2%**? The ASM just keeps holding, and eventually, it drops below your next checkpoint and gets stopped out for a tiny profit (or a loss). **You are riding the pump up, but giving all the profits back on the way down.**

**The Fix:** You need a **Dynamic Trailing Stop**. If a position goes into massive profit, the ASM should dynamically raise its "Hard Fail" threshold to lock in a guaranteed profit.

Add this logic to your `evaluate` method in `PerformanceGate`:
```python
def evaluate(self, position: Position) -> GateAction:
    current_gain = self.calculate_gain(position)
    
    # --- NEW: DYNAMIC TRAILING STOP (PROFIT LOCK) ---
    # If we are up > 5%, we refuse to let this trade turn into a loser.
    # Force exit if it drops back below +1.5% (locking in profit after fees)
    if current_gain > 5.0 and current_gain < 1.5:
        return GateAction.EXIT, "trailing_stop_profit_lock"
        
    # ... (rest of your existing checkpoint logic below) ...
```

### Profit Leak #3: Meme 1-Hour Check is Too Aggressive
**The Problem:** Your `meme` bucket demands `0.0%` (now `1.0%` after the fix above) at exactly 60 minutes. 
Meme coins often spike in the first 5 minutes, and then **consolidate (trade sideways)** for an hour before the next leg up. During consolidation, the price might drift down slightly. If your bot strictly chops the trade at 60 minutes, you will get "shaken out" of winning trades right before they pump again.

**The Fix:** Give the 1-hour checkpoint a tiny bit of breathing room to survive consolidation.

```python
# Change the 1h meme checkpoint to allow a tiny bit of bleeding during consolidation
# Changed from 1.0 (or 0.0) to -1.0
Checkpoint(after_minutes=60, min_gain_pct=-1.0, reason="meme_1h_consolidation_check"),
```
*(Note: The 2-hour checkpoint is `+1.0`, so it still has to perform well by hour 2. This just prevents getting chopped out at hour 1).*

### Profit Leak #4: AI Judge "Death by a Thousand Cuts"
**The Problem:** Any position between `-8%` and `+3%` goes to the AI Judge. If a trade is bleeding slowly, it will hit the AI Judge at 1h, 4h, 12h, and 24h. If the AI is overly optimistic, it will keep saying "Hold," and you will slowly bleed to your `-8%` hard fail.

**The Fix:** You don't necessarily need to change the code for this, but you **must update the system prompt** for your `PositionJudge` AI agent. 
Add this rule to the AI's prompt:
> *"If a position has been evaluated by you previously and is still bleeding (negative PnL), you must prioritize capital preservation. Do not hold a bleeding trade for more than two consecutive checkpoints unless there is a massive, immediate catalyst. Default to EXIT."*

---

### Summary: Is it ready to make a profit?
**Not yet.** If you deploy PR #21 exactly as it is right now, it will successfully protect you from -50% crashes, but **it will slowly drain your account through fees and missed profit-taking.**