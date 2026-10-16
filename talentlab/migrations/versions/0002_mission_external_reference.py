"""référence externe des missions (besoins importés de Boond en lecture seule)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('missions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('external_source', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('external_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('external_hash', sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint('uq_mission_external', ['owner_id', 'external_source', 'external_id'])


def downgrade() -> None:
    with op.batch_alter_table('missions', schema=None) as batch_op:
        batch_op.drop_constraint('uq_mission_external', type_='unique')
        batch_op.drop_column('external_hash')
        batch_op.drop_column('external_id')
        batch_op.drop_column('external_source')
