"""Sequential provider fallback chain.

Matches Silly-Tavern-Flux-Bridge txt2img loop at flux_lora_bridge.py:1939-1999:
try the configured providers in order, advance on transient failure, record
the provider that ultimately succeeded plus every provider attempted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from app.modules.images.providers.base import ImageProvider, ImageProviderError

logger = structlog.get_logger(__name__)


@dataclass
class ChainResult:
    image_bytes: bytes
    provider_used: str
    providers_attempted: list[str] = field(default_factory=list)


class AllProvidersFailedError(Exception):
    def __init__(self, attempted: list[str], last_error: str) -> None:
        super().__init__(
            f"All image providers failed (attempted={attempted}): {last_error}"
        )
        self.attempted = attempted
        self.last_error = last_error


class ProviderChain:
    def __init__(self, providers: list[ImageProvider]) -> None:
        if not providers:
            raise ValueError("ProviderChain requires at least one provider")
        self.providers = providers

    async def generate(
        self,
        prompt: str,
        negative_prompt: str,
        loras: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> ChainResult:
        attempted: list[str] = []
        last_error = "no providers tried"
        for provider in self.providers:
            attempted.append(provider.name)
            try:
                logger.info("image_provider_attempt", provider=provider.name)
                data = await provider.generate(prompt, negative_prompt, loras, params)
                return ChainResult(
                    image_bytes=data,
                    provider_used=provider.name,
                    providers_attempted=attempted,
                )
            except ImageProviderError as exc:
                last_error = str(exc)
                logger.warning(
                    "image_provider_failed", provider=provider.name, error=last_error
                )
                continue
            except Exception as exc:  # noqa: BLE001 — preserve chain semantics
                last_error = repr(exc)
                logger.warning(
                    "image_provider_unhandled", provider=provider.name, error=last_error
                )
                continue
        raise AllProvidersFailedError(attempted, last_error)
