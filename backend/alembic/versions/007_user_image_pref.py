"""Add users.image_pref column.

Revision ID: 007
Revises: 006
Create Date: 2026-05-13

``image_pref`` drives the three-tier image router:

* ``auto``   — LLM ``[SCENE_BEAT:*]`` markers + heuristic fallback (default)
* ``always`` — force Tier 2 (or Tier 3 if ≥2 characters) on every assistant turn
* ``never``  — never auto-generate (manual /image only)
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "image_pref",
            sa.String(10),
            nullable=False,
            server_default="auto",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "image_pref")
