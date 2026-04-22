"""Dice expression parser and server-side roller.

Uses the secrets module for cryptographic randomness — all rolls are
server-side and tamper-proof.

Supported notation:
  1d20          — single die
  2d6           — multiple dice
  3d8+5         — with positive modifier
  1d20-1        — with negative modifier
  4d6kh3        — roll 4d6, keep highest 3
  4d6kl1        — roll 4d6, keep lowest 1
  dF / 4dF      — FATE/Fudge dice (faces: -1, 0, +1)
  1d100         — percentile
"""

import re
import secrets
from dataclasses import dataclass, field

_EXPR_RE = re.compile(
    r"^(?P<num>\d+)?[dD](?P<faces>\d+|[fF])(?P<keep>[kK][hHlL]\d+)?(?P<mod>[+-]\d+)?$"
)

_FATE_FACES = [-1, -1, 0, 0, 1, 1]


@dataclass
class DiceResult:
    expression: str
    num_dice: int
    dice_faces: int | str  # int for standard dice, "F" for FATE
    modifier: int
    all_rolls: list[int] = field(default_factory=list)
    kept_rolls: list[int] = field(default_factory=list)
    total: int = 0


def parse_and_roll(expression: str) -> DiceResult:
    """Parse a dice expression and execute a server-side roll."""
    expr = expression.strip().replace(" ", "")
    m = _EXPR_RE.match(expr)
    if not m:
        raise ValueError(f"Invalid dice expression: {expression!r}")

    num_dice = int(m.group("num") or 1)
    faces_raw = m.group("faces").upper()
    keep_raw = m.group("keep")
    mod_raw = m.group("mod")

    modifier = int(mod_raw) if mod_raw else 0
    is_fate = faces_raw == "F"
    dice_faces: int | str = "F" if is_fate else int(faces_raw)

    if not 1 <= num_dice <= 100:
        raise ValueError(f"Number of dice must be 1–100, got {num_dice}")
    if not is_fate and not 2 <= dice_faces <= 1000:  # type: ignore[operator]
        raise ValueError(f"Dice faces must be 2–1000, got {dice_faces}")

    if is_fate:
        all_rolls = [secrets.choice(_FATE_FACES) for _ in range(num_dice)]
    else:
        all_rolls = [secrets.randbelow(int(dice_faces)) + 1 for _ in range(num_dice)]

    kept_rolls = all_rolls[:]
    if keep_raw:
        keep_type = keep_raw[1].upper()  # H or L
        keep_n = min(int(keep_raw[2:]), num_dice)
        sorted_rolls = sorted(all_rolls, reverse=(keep_type == "H"))
        kept_rolls = sorted_rolls[:keep_n]

    total = sum(kept_rolls) + modifier

    return DiceResult(
        expression=expression,
        num_dice=num_dice,
        dice_faces=dice_faces,
        modifier=modifier,
        all_rolls=all_rolls,
        kept_rolls=kept_rolls,
        total=total,
    )
