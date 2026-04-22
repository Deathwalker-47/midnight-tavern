"""Provider abstraction — unified interface for all LLM providers."""

from collections.abc import AsyncGenerator
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Unified interface all provider adapters must implement."""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str | None,
        model: str,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> str: ...

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None,
        model: str,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]: ...
