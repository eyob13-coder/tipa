"""Add claimed_at and last_reminder_at columns to tips

Revision ID: b7f3d1c2e9a4
Revises: a520c1e85997
Create Date: 2026-08-15 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import Base

# revision identifiers, used by Alembic.
revision: str = 'b7f3d1c2e9a4'
down_revision: str | None = 'a520c1e85997'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # Safety net: make sure the tables exist even on a fresh database.
    Base.metadata.create_all(bind=bind)

    inspector = sa.inspect(bind)
    if not inspector.has_table('tips'):
        return

    columns = {c['name'] for c in inspector.get_columns('tips')}
    if 'claimed_at' not in columns:
        op.add_column('tips', sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True))
    if 'last_reminder_at' not in columns:
        op.add_column('tips', sa.Column('last_reminder_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('tips'):
        return

    columns = {c['name'] for c in inspector.get_columns('tips')}
    if 'last_reminder_at' in columns:
        op.drop_column('tips', 'last_reminder_at')
    if 'claimed_at' in columns:
        op.drop_column('tips', 'claimed_at')
