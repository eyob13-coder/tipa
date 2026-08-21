"""Engagement features: tip goals table + weekly digest tracking column.

Revision ID: c5d6e7f8a9b0
Revises: f7a8b9c0d1e2
Create Date: 2026-08-21

Idempotent: older migrations run Base.metadata.create_all, so every target may
already exist when this runs against a fresh database.
"""

import sqlalchemy as sa

from alembic import op
from app.db.models import GUID

revision = "c5d6e7f8a9b0"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return name in inspector.get_table_names()


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in [c["name"] for c in inspector.get_columns(table)]


def upgrade() -> None:
    if not _has_table("tip_goals"):
        op.create_table(
            "tip_goals",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column(
                "creator_id",
                GUID(),
                sa.ForeignKey("creators.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("target_amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="active"),
            sa.Column("bound_channel_id", sa.String(), nullable=True),
            sa.Column("bound_message_id", sa.String(), nullable=True),
            sa.Column("bound_text", sa.Text(), nullable=True),
            sa.Column("bound_is_caption", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("reached_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_tip_goals_creator_id", "tip_goals", ["creator_id"])
        op.create_index("ix_tip_goals_status", "tip_goals", ["status"])
    if not _has_column("creators", "last_weekly_digest_at"):
        op.add_column("creators", sa.Column("last_weekly_digest_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if _has_column("creators", "last_weekly_digest_at"):
        op.drop_column("creators", "last_weekly_digest_at")
    if _has_table("tip_goals"):
        op.drop_index("ix_tip_goals_status", table_name="tip_goals")
        op.drop_index("ix_tip_goals_creator_id", table_name="tip_goals")
        op.drop_table("tip_goals")
