"""Add user AI config columns to user_settings.

Adds openai_max_tokens, openai_temperature, openai_system_prompt, news_source columns
to allow users to customize AI chat parameters and news source preferences.

Revision ID: 001_add_user_ai_config
Revises:
Create Date: 2026-02-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001_add_user_ai_config'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add new AI config columns to user_settings."""
    # Add columns only if they don't exist
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check if user_settings table exists
    if 'user_settings' not in inspector.get_table_names():
        # Table doesn't exist, skip (will be created by SQLAlchemy)
        return

    existing_columns = [col['name'] for col in inspector.get_columns('user_settings')]

    if 'openai_max_tokens' not in existing_columns:
        op.add_column('user_settings', sa.Column('openai_max_tokens', sa.Integer(), nullable=True))

    if 'openai_temperature' not in existing_columns:
        op.add_column('user_settings', sa.Column('openai_temperature', sa.Float(), nullable=True))

    if 'openai_system_prompt' not in existing_columns:
        op.add_column('user_settings', sa.Column('openai_system_prompt', sa.Text(), nullable=True))

    if 'news_source' not in existing_columns:
        op.add_column('user_settings', sa.Column('news_source', sa.String(50), nullable=True, server_default='auto'))


def downgrade() -> None:
    """Remove AI config columns from user_settings."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if 'user_settings' not in inspector.get_table_names():
        return

    existing_columns = [col['name'] for col in inspector.get_columns('user_settings')]

    if 'openai_system_prompt' in existing_columns:
        op.drop_column('user_settings', 'openai_system_prompt')

    if 'openai_temperature' in existing_columns:
        op.drop_column('user_settings', 'openai_temperature')

    if 'openai_max_tokens' in existing_columns:
        op.drop_column('user_settings', 'openai_max_tokens')

    if 'news_source' in existing_columns:
        op.drop_column('user_settings', 'news_source')
