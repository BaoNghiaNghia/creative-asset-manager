from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PermissionModel(Base):
    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint("permission_key", name="uq_permissions_key"),
        CheckConstraint("status IN ('active','disabled')", name="ck_permissions_status"),
        CheckConstraint("length(trim(permission_key)) > 0", name="ck_permissions_key_not_empty"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    permission_key: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class RoleModel(Base):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_roles_tenant_id_id"),
        UniqueConstraint("tenant_id", "role_key", name="uq_roles_tenant_key"),
        UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
        CheckConstraint("status IN ('active','disabled')", name="ck_roles_status"),
        CheckConstraint("length(trim(role_key)) > 0", name="ck_roles_key_not_empty"),
        CheckConstraint("length(trim(name)) > 0", name="ck_roles_name_not_empty"),
        CheckConstraint("is_system = false OR protected = true", name="ck_system_roles_protected"),
        Index("ix_roles_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    role_key: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    protected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class RolePermissionModel(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_permission"),
        Index("ix_role_permissions_permission_id", "permission_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    role_id: Mapped[str] = mapped_column(String(36), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    permission_id: Mapped[str] = mapped_column(String(36), ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MembershipRoleModel(Base):
    __tablename__ = "membership_roles"
    __table_args__ = (
        UniqueConstraint("tenant_membership_id", "role_id", name="uq_membership_roles_membership_role"),
        ForeignKeyConstraint(
            ["tenant_id", "tenant_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_membership_roles_tenant_membership",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.id"],
            name="fk_membership_roles_tenant_role",
            ondelete="CASCADE",
        ),
        Index("ix_membership_roles_tenant_membership", "tenant_id", "tenant_membership_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tenant_membership_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PlatformAdminAssignmentModel(Base):
    """Durable platform privilege, intentionally separate from tenant roles."""

    __tablename__ = "platform_admin_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_platform_admin_assignments_user"),
        CheckConstraint(
            "status IN ('active','revoked')",
            name="ck_platform_admin_assignments_status",
        ),
        Index("ix_platform_admin_assignments_status", "status"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    granted_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
