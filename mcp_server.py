"""Mock TradingView MCP Server — placeholder until real MCP is integrated."""
from fastapi import FastAPI
import uvicorn

app = FastAPI(title="Mock TradingView MCP")


@app.post("/tools/{tool_name}")
async def tool(tool_name: str, request_body: dict = None):
    return {"status": "mock", "tool": tool_name, "data": []}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"status": "ok", "message": "Mock TradingView MCP"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
