"""Sequential provider fallback chain.

Matches Silly-Tavern-Flux-Bridge txt2img loop at flux_lora_bridge.py:1939-1999:
try the configured providers in order, advance on transient failure, record
the provider that ultimately succeeded plus every provider attempted.

Hardening (Phase 1 of the three-tier image plan):

* Per-provider total budget via ``asyncio.wait_for(..., timeout=...)`` so a
  provider that hangs in polling can't starve the whole chain.
* Chain-level ``validate_image_bytes`` — defense in depth. Individual providers
  may forget to validate, and a Together/CDN 403 returning HTML still counts
  as a transient failure that should advance to the next provider.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.core.config import settings
from app.modules.images.providers.base import (
    ImageProvider,
    ImageProviderError,
    validate_image_bytes,
)

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
    def __init__(
        self,
        providers: list[ImageProvider],
        *,
        timeout_s: int | None = None,
    ) -> None:
        if not providers:
            raise ValueError("ProviderChain requires at least one provider")
        self.providers = providers
        self.timeout_s = timeout_s if timeout_s is not None else settings.IMAGE_PROVIDER_TIMEOUT_S

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
                data = await asyncio.wait_for(
                    provider.generate(prompt, negative_prompt, loras, params),
                    timeout=self.timeout_s,
                )
                # Defense-in-depth: enforce magic-bytes validation at the chain
                # boundary. If a provider returns HTML/error pages that slipped
                # past its own checks, this raises ImageProviderError and the
                # chain advances.
                validate_image_bytes(data, provider.name)
                return ChainResult(
                    image_bytes=data,
                    provider_used=provider.name,
                    providers_attempted=attempted,
                )
            except asyncio.TimeoutError:
                last_error = f"timeout after {self.timeout_s}s"
                logger.warning(
                    "image_provider_timeout",
                    provider=provider.name,
                    timeout_s=self.timeout_s,
                )
                continue
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
