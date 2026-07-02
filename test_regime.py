import asyncio
from src.data.mcp_client import MCPClient
from src.advisory.crypto_regime import CryptoRegimeFilter

async def main():
    mcp = MCPClient()
    regime_filter = CryptoRegimeFilter(mcp)
    regime = await regime_filter.get_current_regime()
    print("REGIME:", regime)

asyncio.run(main())
