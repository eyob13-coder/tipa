"""Ops hardening schema: idempotency keys, creator freeze, language, dispute status.

Revision ID: f7a8b9c0d1e2
Revises: a1b2c3d4e5f6
Create Date: 2026-08-21

Idempotent: older migrations run Base.metadata.create_all, so every target may
already exist when this runs against a fresh database.
"""

import sqlalchemy as sa

from alembic import op

revision = "f7a8b9c0d1e2"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in [c["name"] for c in inspector.get_columns(table)]


def _has_index(table: str, index: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return index in [i["name"] for i in inspector.get_indexes(table)]


def upgrade() -> None:
    if not _has_column("creators", "is_frozen"):
        op.add_column(
            "creators",
            sa.Column("is_frozen", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if not _has_column("creators", "language"):
        op.add_column(
            "creators",
            sa.Column("language", sa.String(length=2), nullable=False, server_default="en"),
        )
    if not _has_column("tips", "idempotency_key"):
        op.add_column("tips", sa.Column("idempotency_key", sa.String(), nullable=True))
    if not _has_index("tips", "ix_tips_idempotency_key"):
        op.create_index("ix_tips_idempotency_key", "tips", ["idempotency_key"], unique=True)


def downgrade() -> None:
    if _has_index("tips", "ix_tips_idempotency_key"):
        op.drop_index("ix_tips_idempotency_key", table_name="tips")
    if _has_column("tips", "idempotency_key"):
        op.drop_column("tips", "idempotency_key")
    if _has_column("creators", "language"):
        op.drop_column("creators", "language")
    if _has_column("creators", "is_frozen"):
        op.drop_column("creators", "is_frozen")
