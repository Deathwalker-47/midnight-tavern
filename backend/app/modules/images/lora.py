"""LoRA dictionary loader and keyword-based matcher.

Ported from Silly-Tavern-Flux-Bridge's ``LoRAManager`` (flux_lora_bridge.py:522)
— simplified for the chat use case:
* one ``master_lora_dict.json`` loaded once at startup,
* keyword + permanent matching,
* role caps (character/nsfw/expression/general/misc) applied before per-provider
  caps,
* enhanced-prompt builder that prepends/appends per-LoRA strings and merges
  negative prompts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Role caps mirror flux-bridge Config.ROLE_CAPS at flux_lora_bridge.py:259
ROLE_CAPS: dict[str, int] = {
    "character": 6,
    "nsfw": 4,
    "expression": 2,
    "general": 2,
    "misc": 1,
}

# Per-provider LoRA caps (flux_lora_bridge.py:268-272).
PROVIDER_MAX_LORAS: dict[str, int] = {
    "runware": 12,
    "wavespeed": 4,
    "fal": 3,
    "together": 2,
    "dummy": 0,  # dummy provider never wires LoRAs
}


class LoRAManager:
    """Loads ``master_lora_dict.json`` and matches LoRAs against prompt text."""

    def __init__(self, dict_path: str | Path) -> None:
        self.dict_path = Path(dict_path)
        self._dict: dict[str, Any] = self._load()
        n = len(self._dict.get("loras", {}))
        logger.info("lora_manager_loaded", count=n, path=str(self.dict_path))

    def _load(self) -> dict[str, Any]:
        if not self.dict_path.exists():
            logger.warning("lora_dict_missing", path=str(self.dict_path))
            return {"config": {"permanent_loras": [], "default_negative_prompt": ""}, "loras": {}}
        with self.dict_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    # ── Configuration ────────────────────────────────────────────────────────

    def permanent_loras(self) -> list[str]:
        return self._dict.get("config", {}).get("permanent_loras", [])

    def default_negative_prompt(self) -> str:
        return self._dict.get("config", {}).get("default_negative_prompt", "")

    # ── Matching ─────────────────────────────────────────────────────────────

    def match(self, prompt: str, negative_prompt: str = "") -> list[dict[str, Any]]:
        """Return ordered list of matched LoRA descriptors.

        Each item: ``{"id": str, "data": dict, "reason": str}``.
        Permanent LoRAs are added first; keyword matches follow; the result is
        sorted by ``rank`` ascending.
        """
        loras = self._dict.get("loras", {})
        combined = f"{prompt.lower()} {negative_prompt.lower()}"
        matched: list[dict[str, Any]] = []
        seen: set[str] = set()

        for lid in self.permanent_loras():
            if lid in loras:
                matched.append({"id": lid, "data": loras[lid], "reason": "permanent"})
                seen.add(lid)

        for lid, data in loras.items():
            if lid in seen:
                continue
            for keyword in data.get("keywords", []):
                if keyword.lower() in combined:
                    matched.append({"id": lid, "data": data, "reason": f"keyword:{keyword}"})
                    seen.add(lid)
                    break

        matched.sort(key=lambda m: m["data"].get("rank", 999))
        return matched

    # ── Role caps + provider truncation ─────────────────────────────────────

    @staticmethod
    def apply_role_caps(matched: list[dict[str, Any]]) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        out: list[dict[str, Any]] = []
        for item in matched:
            role = (item.get("data", {}) or {}).get("category", "misc")
            cap = ROLE_CAPS.get(role, ROLE_CAPS["misc"])
            counts.setdefault(role, 0)
            if counts[role] >= cap:
                continue
            out.append(item)
            counts[role] += 1
        return out

    @staticmethod
    def build_lora_list(matched: list[dict[str, Any]], max_loras: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in matched:
            if len(out) >= max_loras:
                break
            data = item["data"]
            out.append(
                {
                    "id": item["id"],
                    "name": data.get("name", item["id"]),
                    "url": data["url"],
                    "weight": data.get("weight", 1.0),
                }
            )
        return out

    # ── Prompt building ──────────────────────────────────────────────────────

    def build_enhanced_prompt(
        self, original_prompt: str, matched: list[dict[str, Any]]
    ) -> tuple[str, str]:
        """Return ``(full_prompt, full_negative)`` with LoRA prepend/append/negs."""
        prepend: list[str] = []
        append: list[str] = []
        negative: list[str] = [self.default_negative_prompt()]

        for item in matched:
            data = item["data"]
            if (s := (data.get("prepend_prompt") or "").strip()):
                prepend.append(s)
            if (s := (data.get("append_prompt") or "").strip()):
                append.append(s)
            if (s := (data.get("negative_prompt") or "").strip()):
                negative.append(s)

        full = " ".join(filter(None, prepend + [original_prompt] + append))
        full_neg = ", ".join(filter(None, negative))
        return _dedupe(full), _dedupe(full_neg)


def _dedupe(prompt: str) -> str:
    parts = re.split(r"[,.]", prompt)
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        norm = p.strip().lower()
        if norm and norm not in seen:
            seen.add(norm)
            out.append(p.strip())
        elif not norm:
            out.append(p)
    return ", ".join(out).strip()


# Module-level singleton populated by the FastAPI lifespan.
_manager: LoRAManager | None = None


def init_lora_manager(dict_path: str | Path) -> LoRAManager:
    global _manager
    _manager = LoRAManager(dict_path)
    return _manager


def get_lora_manager() -> LoRAManager | None:
    return _manager
