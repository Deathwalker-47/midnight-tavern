"""Tier 3 image asset tables: backdrops, character_poses, scene_composites.

Revision ID: 005
Revises: 004
Create Date: 2026-05-13

See ``docs/plans/PLAN_image_generation_architecture.md`` §4.2.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── backdrops ────────────────────────────────────────────────────────
    op.create_table(
        "backdrops",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("width", sa.Integer(), nullable=False, server_default="1536"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column("storage_url", sa.String(500), nullable=False),
        sa.Column(
            "lighting_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("aspect_ratio", sa.String(20), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("generation_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_backdrops_slug", "backdrops", ["slug"])
    # GIN index on tags JSONB for tag-overlap lookups (Postgres-only).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_backdrops_tags_gin "
        "ON backdrops USING GIN (tags jsonb_path_ops)"
    )

    # ── character_poses ─────────────────────────────────────────────────
    op.create_table(
        "character_poses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "character_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("characters.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("lora_version", sa.String(60), nullable=False),
        sa.Column("pose_slug", sa.String(80), nullable=False),
        sa.Column("expression_slug", sa.String(80), nullable=False),
        sa.Column("framing", sa.String(40), nullable=False),
        sa.Column("facing", sa.String(40), nullable=False),
        sa.Column("transparent_storage_url", sa.String(500), nullable=False),
        sa.Column("raw_storage_url", sa.String(500), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column(
            "bounding_box",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "lighting_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("generation_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.UniqueConstraint(
            "character_id",
            "lora_version",
            "pose_slug",
            "expression_slug",
            "facing",
            name="uq_character_poses_lookup",
        ),
    )
    op.create_index(
        "ix_character_poses_lookup",
        "character_poses",
        ["character_id", "lora_version", "pose_slug", "expression_slug"],
    )

    # ── scene_composites ────────────────────────────────────────────────
    op.create_table(
        "scene_composites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scene_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "backdrop_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backdrops.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "pose_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("layout_template", sa.String(40), nullable=False),
        sa.Column("storage_url", sa.String(500), nullable=False),
        sa.Column("composite_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_scene_composites_hash", "scene_composites", ["scene_hash"])
    op.create_index("ix_scene_composites_lru", "scene_composites", ["last_used_at"])


def downgrade() -> None:
    op.drop_index("ix_scene_composites_lru", table_name="scene_composites")
    op.drop_index("ix_scene_composites_hash", table_name="scene_composites")
    op.drop_table("scene_composites")
    op.drop_index("ix_character_poses_lookup", table_name="character_poses")
    op.drop_table("character_poses")
    op.execute("DROP INDEX IF EXISTS ix_backdrops_tags_gin")
    op.drop_index("ix_backdrops_slug", table_name="backdrops")
    op.drop_table("backdrops")
