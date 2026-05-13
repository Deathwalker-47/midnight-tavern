"""Image generation tables + DM user_id FK constraint.

Revision ID: 004
Revises: 003
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── image_jobs ────────────────────────────────────────────────────────
    image_job_status = sa.Enum(
        "queued",
        "running",
        "completed",
        "failed",
        "canceled",
        name="image_job_status",
    )
    image_job_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "image_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("chat_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("character_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            image_job_status,
            nullable=False,
            server_default="queued",
        ),
        sa.Column("prompt_user", sa.Text(), nullable=False),
        sa.Column("prompt_final", sa.Text(), nullable=True),
        sa.Column("negative_prompt", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column("steps", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("cfg_scale", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column(
            "loras_used",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("provider_used", sa.String(50), nullable=True),
        sa.Column(
            "providers_attempted",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_image_jobs_status", "image_jobs", ["status"])

    # ── image_outputs ─────────────────────────────────────────────────────
    op.create_table(
        "image_outputs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("image_jobs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("storage_url", sa.String(500), nullable=False),
        sa.Column(
            "mime_type", sa.String(50), nullable=False, server_default="image/png"
        ),
        sa.Column("bytes_size", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ── DM scoping: FK on game_rulesets.user_id (kept nullable for public/system rulesets) ──
    op.create_foreign_key(
        "fk_game_rulesets_user_id",
        "game_rulesets",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_game_rulesets_user_id", "game_rulesets", type_="foreignkey")
    op.drop_table("image_outputs")
    op.drop_index("ix_image_jobs_status", table_name="image_jobs")
    op.drop_table("image_jobs")
    sa.Enum(name="image_job_status").drop(op.get_bind(), checkfirst=True)
