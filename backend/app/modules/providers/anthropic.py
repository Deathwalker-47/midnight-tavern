"""Anthropic provider adapter — streaming and non-streaming completion."""

from collections.abc import AsyncGenerator
from typing import Any

import anthropic
import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicProvider:
    def __init__(self, api_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str | None,
        model: str = _DEFAULT_MODEL,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> str:
        kwargs_clean = {k: v for k, v in kwargs.items() if v is not None}
        response = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system or "",
            messages=messages,
            **kwargs_clean,
        )
        return response.content[0].text  # type: ignore[union-attr]

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None,
        model: str = _DEFAULT_MODEL,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        kwargs_clean = {k: v for k, v in kwargs.items() if v is not None}
        async with self._client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system or "",
            messages=messages,
            **kwargs_clean,
        ) as stream:
            async for text in stream.text_stream:
                yield text
