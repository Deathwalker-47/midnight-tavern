"""Add image_jobs.job_kind enum + composite_meta JSONB.

Revision ID: 006
Revises: 005
Create Date: 2026-05-13

Distinguishes single-character / composite / hq jobs so a single ``image_jobs``
table can carry all three tiers' lifecycle state.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``create_type=False`` so the column reference below doesn't try to
    # CREATE TYPE a second time (Postgres rejects DuplicateObjectError); we
    # create the type explicitly with ``checkfirst=True`` instead.
    image_job_kind = postgresql.ENUM(
        "single",
        "composite",
        "hq",
        name="image_job_kind",
        create_type=False,
    )
    image_job_kind.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "image_jobs",
        sa.Column(
            "job_kind",
            image_job_kind,
            nullable=False,
            server_default="single",
        ),
    )
    op.add_column(
        "image_jobs",
        sa.Column("composite_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("image_jobs", "composite_meta")
    op.drop_column("image_jobs", "job_kind")
    sa.Enum(name="image_job_kind").drop(op.get_bind(), checkfirst=True)
