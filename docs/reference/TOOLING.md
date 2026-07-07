# Tooling Reference

## rtk (Rust Token Killer)
CLI proxy that filters and compresses shell command output before it reaches LLM context — 60-90% token savings. Installed globally; auto-rewrites Bash tool calls via PreToolUse hook.

```bash
# Setup (one-time)
rtk init -g          # installs Claude Code hook + RTK.md

# rtk is transparent after setup — these run automatically:
docker compose ps    # -> rtk docker compose ps  (compact)
docker logs karsa-orchestrator --tail 20  # -> deduplicated
git status           # -> compact
pytest               # -> failures only

# Explicit calls when needed
rtk docker ps                          # compact container list
rtk docker logs karsa-orchestrator    # deduplicated logs
rtk docker compose ps                  # compose services
rtk pytest                             # Python tests, -90% output
rtk git diff                           # condensed diff
rtk gain                               # token savings stats
rtk gain --graph                       # ASCII graph last 30 days
```

> Note: rtk only intercepts Bash tool calls. Claude Code built-in tools (Read, Grep, Glob) bypass the hook — use shell commands (`cat`, `rg`, `find`) or `rtk read`/`rtk grep` explicitly when you want filtering there.

## graphify
Turns the Karsa codebase into a queryable knowledge graph — code, SQL schema (`db/init.sql`), docs, all in one graph. Use it to navigate agent relationships and data flows without reading every file.

```bash
# Setup (one-time)
uv tool install graphifyy
graphify install          # registers Claude Code skill
graphify claude install   # writes CLAUDE.md hook + always-on graph reminder
```

```
# In Claude Code sessions
/graphify .                                   # build/rebuild the graph
/graphify . --update                          # re-extract only changed files
/graphify query "how does signal flow from analyst to Telegram?"
/graphify query "what connects BaseAgent to MCPClient?"
/graphify path "Orchestrator" "ApprovalManager"
/graphify explain "BaseAgent"
graphify export callflow-html                 # Mermaid architecture page
```

Graph output lives in `graphify-out/` (commit this):
- `graph.html` — interactive browser view
- `GRAPH_REPORT.md` — key concepts, surprising connections, suggested questions
- `graph.json` — queryable via `graphify query` anytime

## Build & Run

### Quick Start (first time)
```bash
cp .env.example .env        # fill in API keys
# Required in .env:
#   DB_PASSWORD=<12+ chars, no placeholders>
#   REDIS_PASSWORD=<any>
#   TELEGRAM_TOKEN=<from @BotFather>
#   TELEGRAM_CHAT_ID=<your chat ID>
#   9ROUTER_URL, 9ROUTER_AUTH_TOKEN, 9ROUTER_MODEL (or ANTHROPIC_API_KEY)
docker compose up --build   # starts all services
```

### Development Commands
```bash
# Start all
docker compose up -d --build

# Rebuild single service (after code changes)
docker compose up -d --build karsa-orchestrator
docker compose up -d --build karsa-telegram-bot

# Restart without rebuild (config changes only)
docker compose restart karsa-orchestrator karsa-telegram-bot

# Stop all
docker compose down

# Check status
docker compose ps

# Logs (follow)
docker logs -f karsa-orchestrator
docker logs -f karsa-telegram-bot
```

### Health Checks
```bash
# Orchestrator health (scheduler status)
curl http://localhost:8000/health
curl http://localhost:8000/health/scheduler

# Inside container — quick config check
docker exec karsa-orchestrator python3 -c "from src.config import settings; print(settings.TRADING_MODE)"
```

### Testing IDX Intelligence
```bash
# Check composite score
docker exec karsa-orchestrator python3 -c "
from src.config import settings
from src.data.mcp_client import MCPClient
from src.advisory.idx_intelligence import IDXMarketIntelligence
import asyncio

async def test():
    mcp = MCPClient()
    intel = IDXMarketIntelligence(mcp)
    result = await intel.get_regime_composite()
    print(f'Score: {result[\"score\"]} ({result[\"regime\"]})')
    print(f'Components: {result[\"components\"]}')

asyncio.run(test())
"

# Check earnings calendar
docker exec karsa-orchestrator python3 -c "
from src.advisory.idx_intelligence import EarningsCalendar
cal = EarningsCalendar()
universe = cal.get_blackout_universe()
print(f'Blackout tickers: {universe if universe else \"None\"}')
"
```