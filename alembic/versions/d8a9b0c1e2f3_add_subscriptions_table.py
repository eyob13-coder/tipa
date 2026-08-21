"""Add subscriptions table and extend verification_logs for subscription audits.

Idempotent: earlier revisions bootstrap the schema via ``Base.metadata.create_all``,
which already includes the new model, so every step checks before it alters.

Revision ID: d8a9b0c1e2f3
Revises: e5f6a7b8c9d0
Create Date: 2026-08-21 09:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.models import GUID

# revision identifiers, used by Alembic.
revision: str = 'd8a9b0c1e2f3'
down_revision: str | None = 'e5f6a7b8c9d0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(inspector, table: str) -> dict[str, dict]:
    return {col["name"]: col for col in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("subscriptions"):
        op.create_table(
            'subscriptions',
            sa.Column('id', GUID(), primary_key=True),
            sa.Column(
                'creator_id',
                GUID(),
                sa.ForeignKey('creators.id', ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column('plan', sa.String(), nullable=False, server_default='pro'),
            sa.Column('status', sa.String(), nullable=False, server_default='pending'),
            sa.Column('amount', sa.Numeric(10, 2), nullable=False),
            sa.Column('tx_ref', sa.String(), nullable=False, unique=True),
            sa.Column('ref_id', sa.String(), nullable=True, unique=True),
            sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('verification_method', sa.String(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        )
        inspector = sa.inspect(bind)

    if inspector.has_table("verification_logs"):
        cols = _columns(inspector, "verification_logs")

        if "subscription_id" not in cols:
            with op.batch_alter_table("verification_logs") as batch_op:
                batch_op.add_column(sa.Column("subscription_id", GUID(), nullable=True))

        if cols.get("tip_id", {}).get("nullable", True) is False:
            with op.batch_alter_table("verification_logs") as batch_op:
                batch_op.alter_column("tip_id", existing_type=GUID(), nullable=True)

        indexes = {ix["name"] for ix in inspector.get_indexes("verification_logs")}
        if "ix_verification_logs_subscription_id" not in indexes:
            op.create_index(
                "ix_verification_logs_subscription_id",
                "verification_logs",
                ["subscription_id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("verification_logs"):
        cols = _columns(inspector, "verification_logs")
        if "subscription_id" in cols:
            with op.batch_alter_table("verification_logs") as batch_op:
                batch_op.drop_column("subscription_id")
        if cols.get("tip_id", {}).get("nullable", True) is False:
            op.execute("DELETE FROM verification_logs WHERE tip_id IS NULL")
            with op.batch_alter_table("verification_logs") as batch_op:
                batch_op.alter_column("tip_id", existing_type=GUID(), nullable=False)

    if inspector.has_table("subscriptions"):
        op.drop_table("subscriptions")
