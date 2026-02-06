"""Add user role and admin system.

Adds multi-user permission system including:
- user_role enum type and role column on users table
- system_settings singleton table for admin-configured settings
- admin_audit_logs table for tracking admin operations
- can_use_custom_api_key column on user_settings table

Revision ID: 003_add_user_role_and_admin
Revises: 002_add_news_full_content
Create Date: 2026-02-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = '003_add_user_role_and_admin'
down_revision: Union[str, None] = '002_add_news_full_content'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add user role and admin system tables."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # === Step 1: Create user_role enum type ===
    user_role_enum = sa.Enum('admin', 'user', name='user_role')
    user_role_enum.create(conn, checkfirst=True)

    # === Step 2: Add role column to users table ===
    if 'users' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('users')]

        if 'role' not in existing_columns:
            op.add_column('users', sa.Column(
                'role',
                sa.Enum('admin', 'user', name='user_role'),
                nullable=False,
                server_default='user',
            ))
            op.create_index('ix_users_role', 'users', ['role'], unique=False)

    # === Step 3: Create system_settings table ===
    if 'system_settings' not in inspector.get_table_names():
        op.create_table(
            'system_settings',
            sa.Column('id', sa.Integer(), primary_key=True, default=1,
                      comment='Singleton ID, always 1'),
            # OpenAI Configuration
            sa.Column('openai_api_key', sa.Text(), nullable=True,
                      comment='系统级 OpenAI API Key（加密存储）'),
            sa.Column('openai_base_url', sa.String(500), nullable=True,
                      comment='自定义 OpenAI API 地址'),
            sa.Column('openai_model', sa.String(100), nullable=True,
                      server_default='gpt-4o-mini', comment='默认对话模型'),
            sa.Column('openai_max_tokens', sa.Integer(), nullable=True,
                      server_default='4096', comment='默认最大输出 token 数'),
            sa.Column('openai_temperature', sa.Float(), nullable=True,
                      server_default='0.7', comment='默认温度参数 (0.0-2.0)'),
            # Embedding & News Processing
            sa.Column('embedding_model', sa.String(100), nullable=True,
                      server_default='text-embedding-3-small', comment='向量嵌入模型'),
            sa.Column('news_filter_model', sa.String(100), nullable=True,
                      server_default='gpt-4o-mini', comment='新闻筛选模型'),
            sa.Column('news_retention_days', sa.Integer(), nullable=False,
                      server_default='30', comment='新闻内容保留天数'),
            # External API Keys
            sa.Column('finnhub_api_key', sa.Text(), nullable=True,
                      comment='系统级 Finnhub API Key（加密存储）'),
            sa.Column('polygon_api_key', sa.Text(), nullable=True,
                      comment='系统级 Polygon.io API Key（加密存储）'),
            # User Permission Settings
            sa.Column('allow_user_custom_api_keys', sa.Boolean(), nullable=False,
                      server_default='false', comment='是否允许用户使用自定义 API Key（全局开关）'),
            # Audit Fields
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.Column('updated_by', sa.Integer(),
                      sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True,
                      comment='最后更新者（管理员用户 ID）'),
        )

        # Insert default row (singleton)
        op.execute(
            "INSERT INTO system_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING"
        )

    # === Step 4: Add can_use_custom_api_key column to user_settings table ===
    if 'user_settings' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('user_settings')]

        if 'can_use_custom_api_key' not in existing_columns:
            op.add_column('user_settings', sa.Column(
                'can_use_custom_api_key',
                sa.Boolean(),
                nullable=False,
                server_default='false',
                comment='管理员授权：允许此用户使用自定义 API Key',
            ))

    # === Step 5: Create admin_audit_logs table ===
    if 'admin_audit_logs' not in inspector.get_table_names():
        op.create_table(
            'admin_audit_logs',
            sa.Column('id', UUID(as_uuid=False), primary_key=True,
                      server_default=sa.text('uuid_generate_v4()')),
            sa.Column('admin_id', sa.Integer(),
                      sa.ForeignKey('users.id', ondelete='CASCADE'),
                      nullable=False, index=True,
                      comment='执行操作的管理员用户 ID'),
            sa.Column('action', sa.String(100), nullable=False, index=True,
                      comment='操作类型（如: update_system_settings, grant_api_key_permission, lock_user 等）'),
            sa.Column('target_user_id', sa.Integer(),
                      sa.ForeignKey('users.id', ondelete='SET NULL'),
                      nullable=True, index=True,
                      comment='操作目标用户 ID（如适用）'),
            sa.Column('details', sa.Text(), nullable=True,
                      comment='操作详情（JSON 格式）'),
            sa.Column('ip_address', sa.String(45), nullable=True,
                      comment='操作者 IP 地址（支持 IPv6）'),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now(), index=True),
        )


def downgrade() -> None:
    """Remove user role and admin system tables."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # === Step 5: Drop admin_audit_logs table ===
    if 'admin_audit_logs' in inspector.get_table_names():
        op.drop_table('admin_audit_logs')

    # === Step 4: Remove can_use_custom_api_key column from user_settings ===
    if 'user_settings' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('user_settings')]
        if 'can_use_custom_api_key' in existing_columns:
            op.drop_column('user_settings', 'can_use_custom_api_key')

    # === Step 3: Drop system_settings table ===
    if 'system_settings' in inspector.get_table_names():
        op.drop_table('system_settings')

    # === Step 2: Remove role column from users table ===
    if 'users' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('users')]
        if 'role' in existing_columns:
            try:
                op.drop_index('ix_users_role', table_name='users')
            except Exception:
                pass
            op.drop_column('users', 'role')

    # === Step 1: Drop user_role enum type ===
    user_role_enum = sa.Enum('admin', 'user', name='user_role')
    user_role_enum.drop(conn, checkfirst=True)
