import asyncio
from src.data.bybit_client import BybitClient

class MockCache:
    async def get(self, *args, **kwargs): return None
    async def set(self, *args, **kwargs): pass
    async def delete(self, *args, **kwargs): pass
    async def ping(self): return True

async def main():
    cache = MockCache()
    client = BybitClient(cache=cache)
    try:
        wallet = await client.get_wallet_balance()
        print("Wallet:", wallet)
    except Exception as e:
        print("Exception:", str(e))

asyncio.run(main())
