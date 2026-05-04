"""Generation pipeline with pre-interceptor support.

The DM module is the primary consumer of the PreGenerationInterceptor hook.
Registering a pre-interceptor allows the DM to block or modify a generation
request before the narrative model is called — without touching the provider
abstraction layer itself.

Currently the chat router calls evaluate_action() directly; this interface
establishes the pattern for future clean wiring.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InterceptorResult:
    blocked: bool = False
    block_reason: str | None = None
    modified_context: str | None = None  # [DUNGEON MIND RULING] block for narrative AI
    metadata: dict[str, Any] = field(default_factory=dict)


class PreGenerationInterceptor(ABC):
    """Hook that runs BEFORE narrative model generation.

    Return an InterceptorResult with blocked=True to prevent generation.
    Return modified_context to inject DM ruling into the narrative prompt.
    """

    @abstractmethod
    async def process(self, player_message: str, context: dict[str, Any]) -> InterceptorResult:
        ...


class GenerationPipeline:
    """Orchestrates pre-interceptors around a generation call.

    Usage (future wiring):
        pipeline = GenerationPipeline()
        pipeline.register_pre_interceptor(dm_interceptor)
        result = await pipeline.run(player_message, context, generate_fn)
    """

    def __init__(self) -> None:
        self.pre_interceptors: list[PreGenerationInterceptor] = []

    def register_pre_interceptor(self, interceptor: PreGenerationInterceptor) -> None:
        self.pre_interceptors.append(interceptor)

    async def run(
        self,
        player_message: str,
        context: dict[str, Any],
        generate_fn: Any,
    ) -> Any:
        injected_context: str | None = None

        for interceptor in self.pre_interceptors:
            result = await interceptor.process(player_message, context)
            if result.blocked:
                return {"blocked": True, "reason": result.block_reason}
            if result.modified_context:
                injected_context = result.modified_context

        return await generate_fn(player_message, context, injected_context)
