"""Signed outbound webhooks per creator (#9).

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-08-21

One webhook per creator: fires HMAC-signed ``tip.verified`` events to an
HTTPS endpoint the creator controls.
"""
import sqlalchemy as sa

from alembic import op

revision = "e7f8a9b0c1d2"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return name in inspector.get_table_names()


def upgrade() -> None:
    if _has_table("creator_webhooks"):
        return

    from app.db.models import GUID

    op.create_table(
        "creator_webhooks",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "creator_id",
            GUID(),
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("secret", sa.String(length=128), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("last_status", sa.Integer(), nullable=True),
        sa.Column("last_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_creator_webhooks_creator_id", "creator_webhooks", ["creator_id"])


def downgrade() -> None:
    if _has_table("creator_webhooks"):
        op.drop_index("ix_creator_webhooks_creator_id", table_name="creator_webhooks")
        op.drop_table("creator_webhooks")
