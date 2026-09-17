"""prep questionnaire

Revision ID: f19a4e77c305
Revises: e83c5d14bf76
Create Date: 2026-09-17 15:10:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'f19a4e77c305'
down_revision = 'e83c5d14bf76'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Questions drafted from the research and approved by the host before the
    # prep link exists. Style is the kind of show, which shapes what is asked.
    op.add_column('episodes', sa.Column('prep_style', sa.String(length=32), nullable=True))
    op.add_column('episodes', sa.Column('prep_questions', postgresql.JSONB(), nullable=True))
    op.add_column(
        'episodes', sa.Column('prep_published_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('episodes', 'prep_published_at')
    op.drop_column('episodes', 'prep_questions')
    op.drop_column('episodes', 'prep_style')
