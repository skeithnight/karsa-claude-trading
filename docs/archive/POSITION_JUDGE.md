### 🚨 Flaw #1: Tier 1 is "Blind" to Trends
**The Problem:** In your `_CHEAP_SYSTEM_PROMPT`, you tell the AI: 
> *"If gain is negative and **trending down** → EXIT."*

However, Tier 1 has **no tools** and only receives a single snapshot of data (`gain_pct`, `hours_held`). Because it has no historical data, **it is physically impossible for the LLM to know if it is "trending down."** It will just guess or default to HOLD.

**The Fix:** You must calculate the trend in Python and pass it to the AI in the `_build_task` method. 

Update `_build_task` in `position_judge.py`:
```python
def _build_task(self, data: dict, escalated: bool) -> str:
    lines = [
        f"POSITION: {data.get('ticker', '?')} {data.get('side', '?')}",
        f"Entry: {data.get('entry_price', '?')} | Current: {data.get('current_price', '?')}",
        f"Gain: {data.get('gain_pct', 0):+.2f}% | Hours held: {data.get('hours_held', 0):.1f}",
        f"Bucket: {data.get('bucket', 'standard')}",
        f"Gate reason: {data.get('gate_reason', '?')}",
    ]
    
    # --- NEW: Give the AI actual trend data ---
    # Assuming your PerformanceGate passes the previous gain in the dict
    if "gain_change_since_last_check" in data:
        change = data["gain_change_since_last_check"]
        trend_word = "BLEEDING" if change < -1.0 else "FLAT" if abs(change) < 1.0 else "PUMPING"
        lines.append(f"Momentum: {trend_word} ({change:+.2f}% since last check)")

    # ... rest of the method
```

### 🚨 Flaw #2: Raw OHLCV Data Will Fry the LLM
**The Problem:** In your `get_price_action` tool, you return `ohlcv[-20:]`. This sends 20 raw JSON dictionaries (Open, High, Low, Close, Volume) to the LLM. 
1. LLMs are terrible at doing math on raw arrays.
2. It wastes thousands of tokens, making your "Escalated Pass" very expensive and slow.

**The Fix:** Summarize the price action in Python *before* giving it to the LLM.

Update `_handle_tool_call` for `get_price_action`:
```python
if tool_name == "get_price_action":
    interval = tool_input.get("interval", "5m")
    limit = tool_input.get("limit", 50)
    ohlcv = await self.mcp.get_ohlcv(ticker, "CRYPTO", timeframe=interval, limit=limit)
    if not ohlcv:
        return {"error": f"No OHLCV data for {ticker}"}
        
    # --- NEW: Summarize the last 20 candles for the LLM ---
    recent = ohlcv[-20:]
    closes = [float(c["close"]) for c in recent]
    highs = [float(c["high"]) for c in recent]
    lows = [float(c["low"]) for c in recent]
    
    start_price = closes[0]
    end_price = closes[-1]
    pct_change = ((end_price - start_price) / start_price) * 100
    
    return {
        "trend": "bearish" if pct_change < -1 else "bullish" if pct_change > 1 else "consolidating",
        "change_pct": round(pct_change, 2),
        "range_high": max(highs),
        "range_low": min(lows),
        "current_price": end_price
    }
```

### 🚨 Flaw #3: The Missing `dynamic_stop_pct` Database Column
**The Problem:** Your AI can output `"action": "TIGHTEN_STOP"` and `"new_stop_pct": 1.5`. However, if we look at your SQL migration (`add_performance_gate_columns.sql`), you only added:
`bucket`, `last_judgment`, `last_judgment_at`, and `judge_escalated`.

**You have nowhere to save the new stop loss!** If the AI tightens the stop to +1.5%, the `PerformanceGate` won't remember it on the next 5-minute loop, and the trailing stop will fail.

**The Fix:** You need to add one column to your database and model.
1. Update `db/migrations/add_performance_gate_columns.sql`:
```sql
ALTER TABLE crypto_positions
ADD COLUMN IF NOT EXISTS bucket VARCHAR(20) DEFAULT 'standard',
ADD COLUMN IF NOT EXISTS last_judgment JSONB,
ADD COLUMN IF NOT EXISTS last_judgment_at TIMESTAMP,
ADD COLUMN IF NOT EXISTS judge_escalated BOOLEAN DEFAULT FALSE,
-- ADD THIS LINE:
ADD COLUMN IF NOT EXISTS dynamic_stop_pct FLOAT DEFAULT NULL; 
```
2. Ensure your `CryptoPosition` SQLAlchemy/Pydantic model includes `dynamic_stop_pct: Optional[float] = None`.
3. In `performance_gate.py`, when the AI returns `TIGHTEN_STOP`, update the DB: `position.dynamic_stop_pct = judgment["new_stop_pct"]`.

### 🚨 Flaw #4: JSON Parsing Fragility (Markdown Blocks)
**The Problem:** Your prompt demands: *"RESPOND WITH ONLY a valid JSON object"*. 
However, LLMs (especially Claude) are heavily RLHF-trained to wrap JSON in markdown blocks like this:
```json
{
  "action": "HOLD",
  "confidence": 80
}
```
If your `BaseAgent` returns that string, `result.get("action")` will crash because `result` is a string, not a dict.

**The Fix:** Add a quick cleanup step in `_normalize_result` to strip markdown blocks before parsing:
```python
import json
import re

def _normalize_result(self, result: dict, position_data: dict) -> dict:
    # If the LLM returned a string wrapped in markdown, extract the JSON
    if isinstance(result, str):
        match = re.search(r'\{.*\}', result, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group(0))
            except json.JSONDecodeError:
                result = {"error": "Failed to parse LLM JSON string"}
        else:
            result = {"error": "No JSON found in LLM response"}

    # ... rest of your existing _normalize_result logic ...
```

### Summary
The logic in `position_judge.py` is 90% there and highly advanced. If you fix the **OHLCV summarization** (to save tokens) and add the **`dynamic_stop_pct` DB column** (so trailing stops actually work), this AI Judge will be incredibly robust. 