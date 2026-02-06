"""Admin audit log SQLAlchemy model for tracking admin operations."""

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class AdminAuditLog(Base):
    """
    Audit log for tracking admin operations.

    Records all administrative actions for security and compliance purposes.
    """

    __tablename__ = "admin_audit_logs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    admin_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="执行操作的管理员用户 ID",
    )

    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="操作类型（如: update_system_settings, grant_api_key_permission, lock_user 等）",
    )

    target_user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="操作目标用户 ID（如适用）",
    )

    details: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="操作详情（JSON 格式）",
    )

    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45),
        nullable=True,
        comment="操作者 IP 地址（支持 IPv6）",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    # Relationships
    admin = relationship(
        "User",
        foreign_keys=[admin_id],
        backref="admin_audit_logs",
    )
    target_user = relationship(
        "User",
        foreign_keys=[target_user_id],
    )

    def __repr__(self) -> str:
        return (
            f"<AdminAuditLog(id={self.id}, admin_id={self.admin_id}, "
            f"action={self.action}, target_user_id={self.target_user_id})>"
        )
