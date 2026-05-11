"""Character sheet description + spells; spell_update action type.

Revision ID: 003
Revises: 002
Create Date: 2026-05-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # character_sheets: anti-drift description anchor + spells array
    op.add_column(
        "character_sheets",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "character_sheets",
        sa.Column(
            "spells",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("character_sheets", "spells")
    op.drop_column("character_sheets", "description")
