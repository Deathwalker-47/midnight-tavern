"""Build the final image-generation prompt from chat + character + user intent.

Layered blocks (per PLAN_image_generation_architecture.md §Context Assembly):

  1. user explicit prompt
  2. recent chat-window snippet (last N user/character messages)
  3. character profile snippets (name, description, persona)
  4. LoRA prepend/append/negative blocks (via lora.LoRAManager)
  5. safety suffix (no real-person likeness, no minors)

The result is a `(prompt_final, prompt_negative, matched_loras)` triple.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.images.lora import LoRAManager, get_lora_manager

_SAFETY_SUFFIX = "no real-person likeness, no minors"
_RECENT_MESSAGE_LIMIT = 6


async def build_image_prompt(
    db: AsyncSession,
    *,
    user_prompt: str,
    negative_prompt: str | None,
    chat_id: uuid.UUID | None,
    character_id: uuid.UUID | None,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Compose the final prompt + negative + matched LoRA descriptors."""
    blocks: list[str] = [user_prompt.strip()]

    # 1) Character profile
    if character_id is not None:
        from app.modules.characters.models import Character
        from sqlalchemy import select

        result = await db.execute(
            select(Character).where(
                Character.id == character_id, Character.deleted_at.is_(None)
            )
        )
        char = result.scalar_one_or_none()
        if char is not None:
            desc_parts = [char.name]
            if char.description:
                desc_parts.append(char.description)
            if char.personality:
                desc_parts.append(char.personality)
            blocks.append(", ".join(desc_parts))

    # 2) Recent chat snippet
    if chat_id is not None:
        from app.modules.messages.service import get_recent_messages

        recent = await get_recent_messages(db, chat_id, limit=_RECENT_MESSAGE_LIMIT)
        if recent:
            snippet = " ".join(m.content for m in recent if m.content)
            if snippet:
                blocks.append(snippet[:600])

    combined = ". ".join(b for b in blocks if b)

    # 3) LoRA enhancement (if dictionary loaded)
    matched: list[dict[str, Any]] = []
    final_prompt = combined
    final_negative = (negative_prompt or "").strip()
    lm: LoRAManager | None = get_lora_manager()
    if lm is not None:
        matched_raw = lm.match(combined, final_negative)
        matched_raw = LoRAManager.apply_role_caps(matched_raw)
        final_prompt, final_negative_enhanced = lm.build_enhanced_prompt(combined, matched_raw)
        if not final_negative:
            final_negative = final_negative_enhanced
        else:
            final_negative = ", ".join(filter(None, [final_negative_enhanced, final_negative]))
        matched = matched_raw

    # 4) Safety suffix
    final_negative = ", ".join(filter(None, [final_negative, _SAFETY_SUFFIX]))

    return final_prompt, final_negative, matched
