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
    def build_lora_list(
        matched: list[dict[str, Any]],
        max_loras: int,
        *,
        provider_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Truncate to ``max_loras`` and apply provider URL fallback.

        If ``provider_name`` is set and the LoRA URL is in Runware shorthand
        (``deathwalker:slug@1``) but the provider isn't Runware, attempt a
        reverse lookup via ``runware_lora_mapping.json``. If the mapping is
        missing, log a warning and drop the LoRA rather than passing an
        unusable identifier to a non-Runware backend.
        """
        out: list[dict[str, Any]] = []
        mapping: dict[str, str] | None = None
        for item in matched:
            if len(out) >= max_loras:
                break
            data = item["data"]
            url = data["url"]
            if provider_name is not None and provider_name != "runware" and _is_runware_shorthand(url):
                if mapping is None:
                    mapping = _load_runware_mapping()
                fallback = mapping.get(url)
                if fallback:
                    url = fallback
                else:
                    logger.warning(
                        "lora_provider_url_unmapped",
                        lora_id=item["id"],
                        provider=provider_name,
                        runware_url=data["url"],
                    )
                    continue
            out.append(
                {
                    "id": item["id"],
                    "name": data.get("name", item["id"]),
                    "url": url,
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

# Runware shorthand → public URL mapping. Populated lazily from
# ``backend/data/runware_lora_mapping.json``. Used when a non-Runware provider
# is asked to consume a LoRA stored under Runware's hosted shorthand.
_RUNWARE_MAPPING_CACHE: dict[str, str] | None = None


def init_lora_manager(dict_path: str | Path) -> LoRAManager:
    global _manager
    _manager = LoRAManager(dict_path)
    return _manager


def get_lora_manager() -> LoRAManager | None:
    return _manager


def _is_runware_shorthand(url: str) -> bool:
    """Runware uses ``namespace:slug@version`` for hosted LoRAs."""
    return ":" in url and "@" in url and not url.startswith(("http://", "https://"))


def _runware_mapping_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "runware_lora_mapping.json"


def _load_runware_mapping() -> dict[str, str]:
    global _RUNWARE_MAPPING_CACHE
    if _RUNWARE_MAPPING_CACHE is not None:
        return _RUNWARE_MAPPING_CACHE
    path = _runware_mapping_path()
    if not path.exists():
        _RUNWARE_MAPPING_CACHE = {}
        return _RUNWARE_MAPPING_CACHE
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:  # noqa: BLE001
        logger.warning("runware_mapping_load_failed", error=str(exc), path=str(path))
        _RUNWARE_MAPPING_CACHE = {}
        return _RUNWARE_MAPPING_CACHE
    mapping = payload.get("mappings", {}) if isinstance(payload, dict) else {}
    _RUNWARE_MAPPING_CACHE = {str(k): str(v) for k, v in mapping.items()}
    return _RUNWARE_MAPPING_CACHE


def reset_runware_mapping_for_tests() -> None:
    """Drop the cached mapping so the next call reloads from disk."""
    global _RUNWARE_MAPPING_CACHE
    _RUNWARE_MAPPING_CACHE = None
