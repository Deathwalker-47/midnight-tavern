"""CLI launcher for asset workers.

Examples::

    python -m app.workers.cli.run_worker backdrops
    python -m app.workers.cli.run_worker backdrops --regenerate
    python -m app.workers.cli.run_worker poses --character <uuid> --lora-version v1
    python -m app.workers.cli.run_worker poses --character <uuid> --lora-version v1 --limit 5

Live runs hit the configured image provider chain — real credits are spent.
Set ``IMAGE_PROVIDER_ORDER=dummy`` to do a dry run that exercises the DB
machinery without provider cost.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

from app.workers.backdrop_generator import generate_backdrops
from app.workers.character_pose_generator import generate_character_poses


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an asset pre-generation worker.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    bp = sub.add_parser("backdrops", help="Generate backdrop library.")
    bp.add_argument("--regenerate", action="store_true", help="Overwrite existing slugs.")
    bp.add_argument("--specs", default=None, help="Override BACKDROP_SPECS_PATH.")

    pp = sub.add_parser("poses", help="Generate character pose library.")
    pp.add_argument("--character", required=True, help="Character UUID.")
    pp.add_argument("--lora-version", required=True, help="LoRA version tag.")
    pp.add_argument("--matrix", default=None, help="Override POSE_MATRIX_PATH.")
    pp.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after N combinations (smoke testing).",
    )

    return parser


async def _main_async(args: argparse.Namespace) -> dict:
    if args.cmd == "backdrops":
        return await generate_backdrops(
            regenerate=args.regenerate, specs_path=args.specs
        )
    if args.cmd == "poses":
        return await generate_character_poses(
            character_id=uuid.UUID(args.character),
            lora_version=args.lora_version,
            matrix_path=args.matrix,
            limit=args.limit,
        )
    raise SystemExit(f"unknown command: {args.cmd}")


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    result = asyncio.run(_main_async(args))
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
