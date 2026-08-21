"""VIP unlock content: per-creator private channel for verified tippers.

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-08-21

Adds ``creators.vip_channel_id`` — an optional Telegram channel that every
successfully verified tipper is invited into with a single-use link.
"""
import sqlalchemy as sa
from alembic import op

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in [c["name"] for c in inspector.get_columns(table)]


def upgrade() -> None:
    if not _has_column("creators", "vip_channel_id"):
        op.add_column(
            "creators",
            sa.Column("vip_channel_id", sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    if _has_column("creators", "vip_channel_id"):
        op.drop_column("creators", "vip_channel_id")
