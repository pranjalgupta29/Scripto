"""script guest prep

Revision ID: b615d0c73e42
Revises: a274b6e91c88
Create Date: 2026-09-17 18:05:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b615d0c73e42'
down_revision = 'a274b6e91c88'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Whether this version was written with what the guest submitted through the
    # prep link. Existing scripts predate the prep link, so: false.
    op.add_column(
        'scripts',
        sa.Column('guest_prep_used', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('scripts', 'guest_prep_used')
