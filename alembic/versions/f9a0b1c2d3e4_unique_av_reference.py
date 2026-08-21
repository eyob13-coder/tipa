"""One account-verification payment reference may prove ownership for only one creator.

A single micro-deposit receipt must not verify two accounts; the application
layer had a check-then-act race. This unique index is the backstop.

Revision ID: f9a0b1c2d3e4
Revises: e7f8a9b0c1d2
Create Date: 2026-08-22
"""
from alembic import op
import sqlalchemy as sa

revision = "f9a0b1c2d3e4"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("creators") as batch:
        batch.create_unique_constraint(
            "uq_creators_account_verification_ref",
            ["account_verification_ref"],
        )


def downgrade() -> None:
    with op.batch_alter_table("creators") as batch:
        batch.drop_constraint(
            "uq_creators_account_verification_ref",
            type_="unique",
        )
