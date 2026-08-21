"""Payment-hardening schema: account verification, receipt storage, rate limits.

Idempotent: earlier revisions bootstrap via ``Base.metadata.create_all``, which
already includes these models, so every step checks before it alters.

Revision ID: a1b2c3d4e5f6
Revises: d8a9b0c1e2f3
Create Date: 2026-08-21 16:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = 'd8a9b0c1e2f3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(inspector, table: str) -> dict[str, dict]:
    return {col["name"]: col for col in inspector.get_columns(table)}


def _add_columns_if_missing(table: str, columns: list[sa.Column]) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table):
        return
    existing = _columns(inspector, table)
    missing = [col for col in columns if col.name not in existing]
    if not missing:
        return
    with op.batch_alter_table(table) as batch_op:
        for col in missing:
            batch_op.add_column(col)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("creators"):
        _add_columns_if_missing(
            "creators",
            [
                sa.Column("account_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
                sa.Column("account_verification_code", sa.String(), nullable=True),
                sa.Column("account_verification_ref", sa.String(), nullable=True),
            ],
        )

    if inspector.has_table("tips"):
        _add_columns_if_missing("tips", [sa.Column("receipt_file_path", sa.String(), nullable=True)])

    if not inspector.has_table("rate_limit_buckets"):
        op.create_table(
            'rate_limit_buckets',
            sa.Column('key', sa.String(), primary_key=True),
            sa.Column('window_started_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('count', sa.Integer(), nullable=False, server_default='0'),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("rate_limit_buckets"):
        op.drop_table("rate_limit_buckets")

    if inspector.has_table("tips"):
        cols = _columns(inspector, "tips")
        if "receipt_file_path" in cols:
            with op.batch_alter_table("tips") as batch_op:
                batch_op.drop_column("receipt_file_path")

    if inspector.has_table("creators"):
        cols = _columns(inspector, "creators")
        droppable = [
            name
            for name in ("account_verification_ref", "account_verification_code", "account_verified")
            if name in cols
        ]
        if droppable:
            with op.batch_alter_table("creators") as batch_op:
                for name in droppable:
                    batch_op.drop_column(name)
