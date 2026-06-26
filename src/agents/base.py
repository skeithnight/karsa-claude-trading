"""Karsa Trading System - Base Agent with Anthropic SDK Tool-Use Loop"""

import json
from typing import Any

import anthropic

from src.config import settings, LLM_BASE_URL, LLM_AUTH_TOKEN
from src.data.mcp_client import MCPClient
from src.utils.logging import get_logger
from src.utils.rate_limit import RateLimiter

logger = get_logger("base_agent")


class BaseAgent:
    """Base class for all trading agents using the Anthropic SDK agentic loop.

    Agents point to 9Router (not Anthropic directly). The combo_name is passed
    as the model parameter — 9Router maps it to the actual model and handles
    fallback routing.
    """

    def __init__(
        self,
        name: str,
        combo_name: str,
        system_prompt: str,
        tools: list[dict],
        mcp: MCPClient,
        rate_limiter: RateLimiter | None = None,
        max_iterations: int = 10,
    ):
        self.name = name
        self.combo_name = combo_name
        self.system_prompt = system_prompt
        self.tools = tools
        self.mcp = mcp
        self.rate_limiter = rate_limiter
        self.max_iterations = max_iterations

        self.client = anthropic.AsyncAnthropic(
            base_url=LLM_BASE_URL,
            api_key=LLM_AUTH_TOKEN,
        )

    async def run(self, task: str) -> dict[str, Any]:
        """Execute the agentic tool-use loop.

        Returns parsed JSON dict from the agent's final response.
        """
        messages = [{"role": "user", "content": task}]

        for iteration in range(self.max_iterations):
            if self.rate_limiter:
                allowed = await self.rate_limiter.wait_for_token(
                    key=f"agent:{self.name}",
                    max_tokens=10,
                    refill_rate=1.0,
                    wait_seconds=10.0,
                )
                if not allowed:
                    logger.warning("agent_rate_limited", agent=self.name, iteration=iteration)
                    return {"error": "rate_limited", "agent": self.name}

            try:
                response = await self.client.messages.create(
                    model=self.combo_name,
                    max_tokens=4096,
                    system=self.system_prompt,
                    tools=self.tools,
                    messages=messages,
                )
            except anthropic.RateLimitError:
                logger.warning("api_rate_limit", agent=self.name, iteration=iteration)
                return {"error": "api_rate_limited", "agent": self.name}
            except anthropic.APIError as e:
                logger.error("api_error", agent=self.name, error=str(e))
                return {"error": "api_error", "detail": str(e), "agent": self.name}

            logger.info(
                "agent_response",
                agent=self.name,
                iteration=iteration,
                stop_reason=getattr(response, "stop_reason", None),
                input_tokens=getattr(getattr(response, "usage", None), "input_tokens", None),
                output_tokens=getattr(getattr(response, "usage", None), "output_tokens", None),
            )

            if getattr(response, "stop_reason", None) == "end_turn":
                return self._extract_response(response)

            # Process tool calls
            tool_results = []
            content_blocks = getattr(response, "content", []) or []
            for block in content_blocks:
                if getattr(block, "type", None) == "tool_use":
                    result = await self._handle_tool_call(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result) if isinstance(result, dict) else str(result),
                    })

            if not tool_results:
                return self._extract_response(response)

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        logger.warning("agent_max_iterations", agent=self.name, max=self.max_iterations)
        return {"error": "max_iterations_reached", "agent": self.name}

    async def _handle_tool_call(self, tool_name: str, tool_input: dict) -> Any:
        """Handle a tool call. Override in subclasses for custom tools."""
        logger.info("tool_call", agent=self.name, tool=tool_name, input=tool_input)
        try:
            return await self.mcp._call_tool(tool_name, tool_input)
        except Exception as e:
            logger.error("tool_call_error", agent=self.name, tool=tool_name, error=str(e))
            return {"error": str(e)}

    def _extract_response(self, response) -> dict[str, Any]:
        """Extract text and try to parse as JSON."""
        text_parts = []
        content_blocks = getattr(response, "content", []) or []
        for block in content_blocks:
            if hasattr(block, "text"):
                text_parts.append(block.text)

        full_text = "\n".join(text_parts)
        if not full_text:
            return {"error": "Empty response from LLM", "raw_response": str(response)}

        try:
            return json.loads(full_text)
        except (json.JSONDecodeError, ValueError):
            return {"text": full_text, "agent": self.name}
