"""add token usage tracking

Revision ID: 010_token_usage
Revises: 51d59ebea874
Create Date: 2026-05-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '010_token_usage'
down_revision: Union[str, None] = '009_engram_hnsw_index'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'token_usage',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('app_id', sa.String(), nullable=True),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('completion_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('model', sa.String(), nullable=False),
        sa.Column('cost_cents', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_token_usage_user_id', 'token_usage', ['user_id'])
    op.create_index('ix_token_usage_app_id', 'token_usage', ['app_id'])

    op.add_column('users', sa.Column('total_tokens_used', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('users', 'total_tokens_used')
    op.drop_index('ix_token_usage_app_id')
    op.drop_index('ix_token_usage_user_id')
    op.drop_table('token_usage')
