"""guest prep link

Revision ID: e83c5d14bf76
Revises: d52b8c3f7a91
Create Date: 2026-09-17 14:05:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'e83c5d14bf76'
down_revision = 'd52b8c3f7a91'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A capability URL the host sends the guest. Unique so a token identifies
    # exactly one episode; revoked by setting it back to NULL.
    op.add_column('episodes', sa.Column('prep_token', sa.String(length=64), nullable=True))
    op.add_column(
        'episodes', sa.Column('prep_token_created_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint('uq_episodes_prep_token', 'episodes', ['prep_token'])


def downgrade() -> None:
    op.drop_constraint('uq_episodes_prep_token', 'episodes', type_='unique')
    op.drop_column('episodes', 'prep_token_created_at')
    op.drop_column('episodes', 'prep_token')
