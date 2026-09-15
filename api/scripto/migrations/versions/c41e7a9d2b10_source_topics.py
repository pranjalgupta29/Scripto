"""source topics

Revision ID: c41e7a9d2b10
Revises: b58f84ebe966
Create Date: 2026-09-15 20:15:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c41e7a9d2b10'
down_revision = 'b58f84ebe966'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The host topic a topic-research source was found for. Existing rows stay
    # NULL: which search found them was never recorded, and the topic brief
    # treats unlabelled sources as one shared group.
    op.add_column('episode_sources', sa.Column('topic', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('episode_sources', 'topic')
