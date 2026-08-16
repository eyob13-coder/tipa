"""Add channel_id and post_id columns

Revision ID: a520c1e85997
Revises: 'None'
Create Date: 2026-08-14 23:27:48.542106

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import Base

# revision identifiers, used by Alembic.
revision: str = 'a520c1e85997'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create the application tables if they don't exist yet (fresh deployments),
    # then ensure the channel_id index is present.
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

    inspector = sa.inspect(bind)
    if inspector.has_table('creators'):
        indexes = {i['name'] for i in inspector.get_indexes('creators')}
        if 'ix_creators_channel_id' not in indexes:
            op.create_index(op.f('ix_creators_channel_id'), 'creators', ['channel_id'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('creators'):
        indexes = {i['name'] for i in inspector.get_indexes('creators')}
        if 'ix_creators_channel_id' in indexes:
            op.drop_index(op.f('ix_creators_channel_id'), table_name='creators')
