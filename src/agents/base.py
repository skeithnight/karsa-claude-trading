"""Karsa Trading System - Base Agent with Anthropic SDK Tool-Use Loop"""

import json
from typing import Any

import anthropic

from src.config import LLM_MODEL, settings, LLM_BASE_URL, LLM_AUTH_TOKEN
from src.data.mcp_client import MCPClient
from src.utils.logging import get_logger
from src.utils.rate_limit import RateLimiter

logger = get_logger("base_agent")


class BaseAgent:
    """Base class for all trading agents using the Anthropic SDK agentic loop.

    Agents point to 9Router (not Anthropic directly). The combo_name is passed
    as the model parameter — 9Router maps it to the actual model and handles
    fallback routing.

    Trace capture: set self._capture_traces = True in subclasses to record
    full LLM conversation history. Traces returned as "_trace" key in result.
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
        self.combo_name = LLM_MODEL if LLM_MODEL else combo_name
        self.system_prompt = system_prompt
        self.tools = tools
        self.mcp = mcp
        self.rate_limiter = rate_limiter
        self.max_iterations = max_iterations
        self._capture_traces = False

        self.client = anthropic.AsyncAnthropic(
            base_url=LLM_BASE_URL,
            api_key=LLM_AUTH_TOKEN,
        )

    async def run(self, task: str) -> dict[str, Any]:
        """Execute the agentic tool-use loop.

        Returns parsed JSON dict from the agent's final response.
        If _capture_traces is True, result includes "_trace" dict.
        """
        messages = [{"role": "user", "content": task}]

        # Trace capture state
        trace_tools_used: list[dict] = []
        trace_tool_results: list[dict] = []
        trace_iterations = 0

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

            response = None
            for attempt in range(2):
                try:
                    import time
                    t0 = time.time()
                    response = await self.client.messages.create(
                        model=self.combo_name,
                        max_tokens=4096,
                        system=self.system_prompt,
                        tools=self.tools,
                        messages=messages,
                    )
                    try:
                        from src.metrics.crypto_metrics import LLM_LATENCY
                        LLM_LATENCY.labels(agent=self.name).observe(time.time() - t0)
                    except Exception:
                        pass
                except anthropic.RateLimitError:
                    logger.warning("api_rate_limit", agent=self.name, iteration=iteration)
                    return {"error": "api_rate_limited", "agent": self.name}
                except anthropic.APIError as e:
                    logger.error("api_error", agent=self.name, error=str(e))
                    return {"error": "api_error", "detail": str(e), "agent": self.name}

                content_blocks = getattr(response, "content", []) or []

                # 9Router returns OpenAI format for Gemini — content=None, data in choices[]
                choices = getattr(response, "choices", [])
                if not content_blocks and choices:
                    from anthropic.types import ToolUseBlock, TextBlock
                    message = choices[0].get("message", {})
                    # Extract tool calls
                    for tc in message.get("tool_calls", []):
                        func = tc.get("function", {})
                        try:
                            args = json.loads(func.get("arguments", "{}"))
                        except Exception:
                            args = {}
                        tool_id = tc.get("id") or f"toolu_{hash(func.get('name', '') + str(args)) % 10**12:012x}"
                        content_blocks.append(ToolUseBlock(
                            id=tool_id, name=func.get("name"),
                            input=args, type="tool_use"
                        ))
                    # Extract text content
                    text = message.get("content")
                    if text:
                        content_blocks.append(TextBlock(text=text, type="text"))
                    if content_blocks:
                        response.content = content_blocks

                if content_blocks:
                    break
                logger.warning("empty_response_retry", agent=self.name, iteration=iteration, attempt=attempt, model=self.combo_name, raw_response=str(response))
            else:
                logger.error("empty_response_final", agent=self.name, iteration=iteration, model=self.combo_name, raw_response=str(response))
                return {"error": "Empty response from LLM after retries", "agent": self.name, "model": self.combo_name, "detail": str(response)}

            stop = getattr(response, "stop_reason", None)
            has_tool_use = any(getattr(b, "type", None) == "tool_use" for b in content_blocks)

            logger.info(
                "agent_response",
                agent=self.name,
                iteration=iteration,
                stop_reason=stop,
                has_tool_use=has_tool_use,
                content_len=len(content_blocks),
                input_tokens=getattr(getattr(response, "usage", None), "input_tokens", None),
                output_tokens=getattr(getattr(response, "usage", None), "output_tokens", None),
            )

            # 9Router may omit stop_reason — treat no stop_reason + no tool_use as end_turn
            if stop == "end_turn" or (not stop and not has_tool_use):
                result = self._extract_response(response)
                return self._attach_trace(result, task, trace_tools_used, trace_tool_results, trace_iterations)

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
                    # Trace: record tool calls and results
                    if self._capture_traces:
                        trace_tools_used.append({"name": block.name, "input": block.input})
                        trace_tool_results.append({"tool": block.name, "result_summary": str(result)[:500]})

            trace_iterations = iteration + 1

            if not tool_results:
                result = self._extract_response(response)
                return self._attach_trace(result, task, trace_tools_used, trace_tool_results, trace_iterations)

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        logger.warning("agent_max_iterations", agent=self.name, max=self.max_iterations)
        return {"error": "max_iterations_reached", "agent": self.name}

    async def _handle_tool_call(self, tool_name: str, tool_input: dict) -> Any:
        """Handle a tool call. Override in subclasses for custom tools."""
        logger.debug("tool_call", agent=self.name, tool=tool_name, input=tool_input)
        try:
            return await self.mcp._call_tool(tool_name, tool_input)
        except Exception as e:
            logger.error("tool_call_error", agent=self.name, tool=tool_name, error=str(e))
            return {"error": str(e)}

    def _extract_response(self, response) -> dict[str, Any]:
        """Extract text and try to parse as JSON."""
        import re
        text_parts = []
        content_blocks = getattr(response, "content", []) or []
        for block in content_blocks:
            if hasattr(block, "text"):
                text_parts.append(block.text)

        full_text = "\n".join(text_parts)
        if not full_text:
            return {"error": "Empty response from LLM", "raw_response": str(response)}

        # Strip markdown fences that LLMs wrap around JSON
        clean_text = re.sub(r'^```(?:json)?\s*|\s*```$', '', full_text, flags=re.MULTILINE).strip()

        try:
            return json.loads(clean_text)
        except (json.JSONDecodeError, ValueError):
            return {"text": full_text, "agent": self.name}

    def _attach_trace(
        self, result: dict, task: str,
        tools_used: list[dict], tool_results: list[dict], iterations: int,
    ) -> dict:
        """Attach reasoning trace to result if capture is enabled."""
        if not self._capture_traces:
            return result

        if isinstance(result, list):
            result = {"signals": result}

        result["_trace"] = {
            "agent_name": self.name,
            "system_prompt": self.system_prompt[:2000],  # truncate for storage
            "user_prompt": task[:1000],
            "tools_used": tools_used,
            "tool_results": tool_results,
            "llm_response": json.dumps(result)[:2000],
            "reasoning": result.get("reasoning", ""),
            "strategy": result.get("strategy", ""),
            "confidence": result.get("confidence_score", 0),
            "iterations": iterations,
            "model": self.combo_name,
        }
        return result
