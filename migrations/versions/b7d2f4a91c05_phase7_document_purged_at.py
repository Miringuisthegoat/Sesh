"""phase 7: documents.purged_at

Revision ID: b7d2f4a91c05
Revises: e459c0c4bcff
Create Date: 2026-11-04 09:00:00

"""
from alembic import op
import sqlalchemy as sa

revision = 'b7d2f4a91c05'
down_revision = 'e459c0c4bcff'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('purged_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_column('purged_at')
