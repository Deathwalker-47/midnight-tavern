"""Deterministic pre-validation layer — runs before any DM model call.

Hard rejects are immediate (no LLM tokens spent). Soft flags are packed
into the prompt so the DM model can make context-aware decisions.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PreValidationResult:
    passed: bool
    checks_run: list[str] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    hard_reject: bool = False
    reject_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks_run": self.checks_run,
            "failures": self.failures,
            "hard_reject": self.hard_reject,
            "reject_reason": self.reject_reason,
        }

    def flags_for_prompt(self) -> str:
        """Format soft flags as a block to inject into the DM prompt."""
        soft = [f for f in self.failures if not f.get("hard_reject")]
        if not soft:
            return ""
        lines = ["PRE-VALIDATION FLAGS (be strict about these):"]
        for f in soft:
            lines.append(f"  - {f.get('reason', '')}")
        return "\n".join(lines)


_BLOCKING_CONDITIONS = {
    "unconscious": "Character is unconscious and cannot act.",
    "paralyzed": "Character is paralyzed and cannot move or act.",
    "petrified": "Character is petrified and cannot act.",
    "stunned": "Character is stunned and cannot act this turn.",
    "dead": "Character is dead and cannot act.",
}

_ABILITY_VERBS = [
    "cast", "channel", "summon", "invoke", "activate",
    "unleash", "conjure", "transform into", "shapeshift", "use ability",
]

_ITEM_VERBS = ["drink", "use", "equip", "throw", "eat", "apply", "read", "consume"]

_RESOURCE_STATS = {"mp", "mana", "sp", "stamina", "ep", "energy", "ap", "action points"}


class PreValidator:
    """Deterministic rule checks before DM model call.

    All checks are async for consistency, though none currently do I/O.
    The order matters: alive check runs first, remaining checks only fire
    if the character can still act.
    """

    def __init__(self, enforcement_config: dict[str, Any]) -> None:
        self.config = enforcement_config

    async def validate(
        self,
        player_message: str,
        sheet_stats: dict[str, Any],
        sheet_skills: list[Any],
        sheet_inventory: list[Any],
        is_alive: bool,
    ) -> PreValidationResult:
        result = PreValidationResult(passed=True)
        checks = [
            self._check_alive,
            self._check_conditions,
            self._check_ability_claims,
            self._check_item_claims,
            self._check_resource_availability,
        ]

        for check in checks:
            check_result = await check(
                player_message, sheet_stats, sheet_skills, sheet_inventory, is_alive
            )
            result.checks_run.append(check_result["check_name"])
            if not check_result["passed"] or check_result.get("flag_for_dm"):
                result.failures.append(check_result)
                if check_result.get("hard_reject"):
                    result.passed = False
                    result.hard_reject = True
                    result.reject_reason = check_result["reason"]
                    return result

        return result

    async def _check_alive(
        self, message: str, stats: dict, skills: list, inventory: list, is_alive: bool
    ) -> dict[str, Any]:
        if not self.config.get("dead_cannot_act", True):
            return {"check_name": "alive_check", "passed": True}
        if not is_alive:
            return {
                "check_name": "alive_check",
                "passed": False,
                "hard_reject": True,
                "reason": "This character is dead. Dead characters cannot act.",
            }
        return {"check_name": "alive_check", "passed": True}

    async def _check_conditions(
        self, message: str, stats: dict, skills: list, inventory: list, is_alive: bool
    ) -> dict[str, Any]:
        if not self.config.get("respect_conditions", True):
            return {"check_name": "condition_check", "passed": True}

        condition_val = ""
        for key in ("Condition", "condition", "Status", "status"):
            if key in stats:
                condition_val = str(stats[key]).lower()
                break

        if condition_val:
            for cond_key, reason in _BLOCKING_CONDITIONS.items():
                if cond_key in condition_val:
                    return {
                        "check_name": "condition_check",
                        "passed": False,
                        "hard_reject": True,
                        "reason": reason,
                    }

        return {"check_name": "condition_check", "passed": True}

    async def _check_ability_claims(
        self, message: str, stats: dict, skills: list, inventory: list, is_alive: bool
    ) -> dict[str, Any]:
        if not self.config.get("require_skill_for_ability", True):
            return {"check_name": "ability_check", "passed": True}

        message_lower = message.lower()
        has_magic_verb = any(verb in message_lower for verb in _ABILITY_VERBS)

        if has_magic_verb and not skills:
            return {
                "check_name": "ability_check",
                "passed": True,
                "flag_for_dm": True,
                "reason": "Player claims to use an ability but their skill list is empty. Validate carefully.",
            }

        return {"check_name": "ability_check", "passed": True}

    async def _check_item_claims(
        self, message: str, stats: dict, skills: list, inventory: list, is_alive: bool
    ) -> dict[str, Any]:
        if not self.config.get("require_item_for_use", True):
            return {"check_name": "item_check", "passed": True}

        message_lower = message.lower()
        has_item_verb = any(verb in message_lower for verb in _ITEM_VERBS)

        if has_item_verb and not inventory:
            return {
                "check_name": "item_check",
                "passed": True,
                "flag_for_dm": True,
                "reason": "Player appears to use or consume an item but their inventory is empty.",
            }

        return {"check_name": "item_check", "passed": True}

    async def _check_resource_availability(
        self, message: str, stats: dict, skills: list, inventory: list, is_alive: bool
    ) -> dict[str, Any]:
        if not self.config.get("resource_costs_enforced", True):
            return {"check_name": "resource_check", "passed": True}

        depleted = []
        for stat_name, stat_value in stats.items():
            if stat_name.lower() in _RESOURCE_STATS:
                try:
                    if float(stat_value) <= 0:
                        depleted.append(stat_name)
                except (TypeError, ValueError):
                    pass

        if depleted:
            names = ", ".join(depleted)
            return {
                "check_name": "resource_check",
                "passed": True,
                "flag_for_dm": True,
                "reason": f"{names} is at zero. Reject if the action requires this resource.",
            }

        return {"check_name": "resource_check", "passed": True}
