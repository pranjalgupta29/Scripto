"""source identity gate

Revision ID: a274b6e91c88
Revises: f19a4e77c305
Create Date: 2026-09-17 16:40:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'a274b6e91c88'
down_revision = 'f19a4e77c305'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Whether a discovered page is about the person the host confirmed. Existing
    # rows stay NULL: never checked, and trusted as before.
    op.add_column('episode_sources', sa.Column('identity', sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column('episode_sources', 'identity')
