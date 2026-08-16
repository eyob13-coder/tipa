"""Rename Chapa fields, drop creator subaccount id, add verification fields.

Revision ID: c9d4e5f6a7b8
Revises: b7f3d1c2e9a4
Create Date: 2026-08-16 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.db.base import Base


# revision identifiers, used by Alembic.
revision: str = 'c9d4e5f6a7b8'
down_revision: Union[str, None] = 'b7f3d1c2e9a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # Safety net: make sure the tables exist even on a fresh database.
    Base.metadata.create_all(bind=bind)

    inspector = sa.inspect(bind)
    if inspector.has_table('tips'):
        columns = {c['name'] for c in inspector.get_columns('tips')}
        with op.batch_alter_table('tips') as batch_op:
            if 'chapa_tx_ref' in columns and 'tx_ref' not in columns:
                batch_op.alter_column('chapa_tx_ref', new_column_name='tx_ref')
            if 'chapa_ref_id' in columns and 'ref_id' not in columns:
                batch_op.alter_column('chapa_ref_id', new_column_name='ref_id')
            if 'verification_method' not in columns:
                batch_op.add_column(sa.Column('verification_method', sa.String(), nullable=True))
            if 'verified_amount' not in columns:
                batch_op.add_column(sa.Column('verified_amount', sa.Numeric(10, 2), nullable=True))

        # Index on ref_id must be created AFTER the batch rebuild (SQLite cannot
        # create an index on a column renamed in the same batch rebuild).
        if 'ref_id' in columns or 'chapa_ref_id' in columns:
            existing_indexes = {ix['name'] for ix in inspector.get_indexes('tips')}
            if 'ix_tips_ref_id' not in existing_indexes:
                op.create_index('ix_tips_ref_id', 'tips', ['ref_id'], unique=True)

    inspector = sa.inspect(bind)
    if inspector.has_table('creators'):
        columns = {c['name'] for c in inspector.get_columns('creators')}
        if 'chapa_subaccount_id' in columns:
            with op.batch_alter_table('creators') as batch_op:
                batch_op.drop_column('chapa_subaccount_id')


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('creators'):
        columns = {c['name'] for c in inspector.get_columns('creators')}
        if 'chapa_subaccount_id' not in columns:
            with op.batch_alter_table('creators') as batch_op:
                batch_op.add_column(sa.Column('chapa_subaccount_id', sa.String(), nullable=False, server_default=''))

    inspector = sa.inspect(bind)
    if inspector.has_table('tips'):
        columns = {c['name'] for c in inspector.get_columns('tips')}

        # Drop index before the batch rebuild (reverse order of upgrade).
        if 'ref_id' in columns:
            existing_indexes = {ix['name'] for ix in inspector.get_indexes('tips')}
            if 'ix_tips_ref_id' in existing_indexes:
                op.drop_index('ix_tips_ref_id', table_name='tips')

        with op.batch_alter_table('tips') as batch_op:
            if 'tx_ref' in columns and 'chapa_tx_ref' not in columns:
                batch_op.alter_column('tx_ref', new_column_name='chapa_tx_ref')
            if 'ref_id' in columns and 'chapa_ref_id' not in columns:
                batch_op.alter_column('ref_id', new_column_name='chapa_ref_id')
            if 'verified_amount' in columns:
                batch_op.drop_column('verified_amount')
            if 'verification_method' in columns:
                batch_op.drop_column('verification_method')
