"""Add account_status for pending user approval workflow.

Adds account lifecycle status to support user registration approval:
- account_status enum type (active, pending_approval, suspended)
- account_status column on users table
- require_registration_approval column on system_settings table

Revision ID: 005_add_account_status
Revises: 004_add_news_api_settings
Create Date: 2026-02-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '005_add_account_status'
down_revision: Union[str, None] = '004_add_news_api_settings'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add account_status enum and columns for pending user approval."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # === Step 1: Create account_status enum type ===
    account_status_enum = sa.Enum(
        'active', 'pending_approval', 'suspended',
        name='account_status'
    )
    account_status_enum.create(conn, checkfirst=True)

    # === Step 2: Add account_status column to users table ===
    if 'users' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('users')]

        if 'account_status' not in existing_columns:
            op.add_column('users', sa.Column(
                'account_status',
                sa.Enum('active', 'pending_approval', 'suspended', name='account_status'),
                nullable=False,
                server_default='active',  # Existing users are active by default
                comment='Account lifecycle status: active, pending_approval, suspended',
            ))
            op.create_index(
                'ix_users_account_status',
                'users',
                ['account_status'],
                unique=False
            )

    # === Step 3: Add require_registration_approval to system_settings ===
    if 'system_settings' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('system_settings')]

        if 'require_registration_approval' not in existing_columns:
            op.add_column('system_settings', sa.Column(
                'require_registration_approval',
                sa.Boolean(),
                nullable=False,
                server_default='false',  # Disabled by default for backwards compatibility
                comment='是否要求新用户注册后等待管理员审批',
            ))


def downgrade() -> None:
    """Remove account_status enum and columns."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # === Step 3: Remove require_registration_approval from system_settings ===
    if 'system_settings' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('system_settings')]
        if 'require_registration_approval' in existing_columns:
            op.drop_column('system_settings', 'require_registration_approval')

    # === Step 2: Remove account_status column from users ===
    if 'users' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('users')]
        if 'account_status' in existing_columns:
            try:
                op.drop_index('ix_users_account_status', table_name='users')
            except Exception:
                pass
            op.drop_column('users', 'account_status')

    # === Step 1: Drop account_status enum type ===
    account_status_enum = sa.Enum(
        'active', 'pending_approval', 'suspended',
        name='account_status'
    )
    account_status_enum.drop(conn, checkfirst=True)
