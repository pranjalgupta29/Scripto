"""docx source type

Revision ID: d52b8c3f7a91
Revises: c41e7a9d2b10
Create Date: 2026-09-17 13:20:00.000000
"""
from alembic import op


revision = 'd52b8c3f7a91'
down_revision = 'c41e7a9d2b10'
branch_labels = None
depends_on = None

_OLD = "type IN ('web_article','youtube','pdf','profile','user_pasted')"
_NEW = "type IN ('web_article','youtube','pdf','docx','profile','user_pasted')"


def upgrade() -> None:
    # Uploaded Word documents (resumes, bios) are a source type of their own.
    op.drop_constraint('ck_source_type', 'sources', type_='check')
    op.create_check_constraint('ck_source_type', 'sources', _NEW)


def downgrade() -> None:
    op.execute("DELETE FROM sources WHERE type = 'docx'")
    op.drop_constraint('ck_source_type', 'sources', type_='check')
    op.create_check_constraint('ck_source_type', 'sources', _OLD)
