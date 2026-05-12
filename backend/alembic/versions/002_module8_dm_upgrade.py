"""Module 8 DM upgrade — richer ruleset config, is_alive, game_events, dm_config_attachments.

Revision ID: 002
Revises: 001
Create Date: 2026-05-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

_DEFAULT_ENFORCEMENT = (
    '{"require_skill_for_ability": true, "require_item_for_use": true, '
    '"permadeath": true, "no_invented_powers": true, '
    '"resource_costs_enforced": true, "dead_cannot_act": true, '
    '"respect_conditions": true}'
)


def upgrade() -> None:
    # ── game_rulesets: new config columns ────────────────────────────────────
    op.add_column("game_rulesets", sa.Column("reminder_text", sa.Text(), nullable=True))
    op.add_column("game_rulesets", sa.Column("instruction", sa.Text(), nullable=True))
    op.add_column("game_rulesets", sa.Column("player_guide", sa.Text(), nullable=True))
    op.add_column("game_rulesets", sa.Column("recommended_provider", sa.String(50), nullable=True))
    op.add_column("game_rulesets", sa.Column("dm_temperature", sa.Float(), nullable=False, server_default="0.3"))
    op.add_column("game_rulesets", sa.Column("dm_max_tokens", sa.Integer(), nullable=False, server_default="2000"))
    op.add_column("game_rulesets", sa.Column("context_window", sa.Integer(), nullable=False, server_default="10"))
    op.add_column(
        "game_rulesets",
        sa.Column(
            "enforcement_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=_DEFAULT_ENFORCEMENT,
        ),
    )

    # ── character_sheets: permadeath flag ────────────────────────────────────
    op.add_column(
        "character_sheets",
        sa.Column("is_alive", sa.Boolean(), nullable=False, server_default="true"),
    )

    # ── dm_rolls: richer roll metadata ───────────────────────────────────────
    op.add_column("dm_rolls", sa.Column("dc", sa.Integer(), nullable=True))
    op.add_column("dm_rolls", sa.Column("success", sa.Boolean(), nullable=True))
    op.add_column("dm_rolls", sa.Column("advantage", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("dm_rolls", sa.Column("disadvantage", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("dm_rolls", sa.Column("stat_used", sa.String(100), nullable=True))

    # ── game_events: comprehensive per-action ruling log ─────────────────────
    op.create_table(
        "game_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("player_action", sa.Text(), nullable=False),
        sa.Column("ruling", sa.String(10), nullable=False),
        sa.Column("ruling_reason", sa.Text(), nullable=True),
        sa.Column("approved_action", sa.Text(), nullable=True),
        sa.Column("forbidden_elements", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("stat_changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("inventory_changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("skill_changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("dm_model_used", sa.String(255), nullable=True),
        sa.Column("dm_prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("dm_completion_tokens", sa.Integer(), nullable=True),
        sa.Column("dm_latency_ms", sa.Integer(), nullable=True),
        sa.Column("pre_validation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("question_text", sa.Text(), nullable=True),
        sa.Column("player_answer", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["dm_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_game_events_session", "game_events", ["session_id"])
    op.create_index("idx_game_events_message", "game_events", ["message_id"])
    op.create_index("idx_game_events_ruling", "game_events", ["session_id", "ruling"])

    # ── dm_config_attachments: link ruleset to character or chat ─────────────
    op.create_table(
        "dm_config_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ruleset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("character_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("chat_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("mode", sa.String(20), nullable=False, server_default="required"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["ruleset_id"], ["game_rulesets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(character_id IS NOT NULL AND chat_id IS NULL) OR (character_id IS NULL AND chat_id IS NOT NULL)",
            name="ck_attachment_exclusive",
        ),
    )
    op.create_index("idx_dm_attachments_ruleset", "dm_config_attachments", ["ruleset_id"])
    op.create_index(
        "idx_dm_attachments_character",
        "dm_config_attachments",
        ["character_id"],
        unique=True,
        postgresql_where=sa.text("character_id IS NOT NULL"),
    )
    op.create_index(
        "idx_dm_attachments_chat",
        "dm_config_attachments",
        ["chat_id"],
        unique=True,
        postgresql_where=sa.text("chat_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("dm_config_attachments")
    op.drop_table("game_events")

    for col in ("stat_used", "disadvantage", "advantage", "success", "dc"):
        op.drop_column("dm_rolls", col)

    op.drop_column("character_sheets", "is_alive")

    for col in ("enforcement_config", "context_window", "dm_max_tokens",
                "dm_temperature", "recommended_provider", "player_guide",
                "instruction", "reminder_text"):
        op.drop_column("game_rulesets", col)
