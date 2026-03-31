"""
Shared LLM utilities for agent nodes.

Key design:
- get_llm() builds the right LangChain chat model from env config.
- stream_llm() streams tokens and pushes text_delta events to the SSE queue.
- emit() is the single function all nodes use to push any event to the queue.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

_log = logging.getLogger(__name__)

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel


def get_llm(model: str | None = None) -> "BaseChatModel":
    model = model or os.getenv("DEFAULT_MODEL", "claude-sonnet-4-6")

    if "claude" in model:
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model,
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            max_tokens=8192,
        )

    # OpenAI-compatible (OpenAI, DeepSeek, Qwen, Grok, etc.)
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model,
        api_key=os.getenv("OPENAI_API_KEY") or "sk-placeholder",
        base_url=os.getenv("OPENAI_BASE_URL") or None,
        max_tokens=8192,
    )


async def stream_llm(
    messages: list[BaseMessage],
    config: RunnableConfig,
    agent_id: str,
    max_attempts: int = 3,
) -> str:
    """Stream LLM output token by token, pushing text_delta events to the queue.
    Returns the full accumulated response string. Retries up to max_attempts on empty response."""
    llm = get_llm()
    for attempt in range(1, max_attempts + 1):
        full = ""
        async for chunk in llm.astream(messages):
            text = chunk.content if isinstance(chunk.content, str) else ""
            if text:
                full += text
                await emit(config, {
                    "type": "text_delta",
                    "data": {"content": text, "agentId": agent_id},
                })
        if full:
            return full
        _log.warning("stream_llm: empty response on attempt %d/%d", attempt, max_attempts)
        if attempt < max_attempts:
            await asyncio.sleep(2 ** attempt)  # 2s, 4s
    return full


async def emit(config: RunnableConfig, event: dict) -> None:
    """Push an event onto the SSE queue stored in LangGraph's configurable."""
    import asyncio
    queue: asyncio.Queue | None = config.get("configurable", {}).get("event_queue")
    if queue is not None:
        await queue.put(event)
