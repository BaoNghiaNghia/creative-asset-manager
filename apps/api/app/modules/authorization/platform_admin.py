from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.auth_persistence.model import AuthAuditEventModel, UserModel, utcnow
from app.modules.authorization.model import PlatformAdminAssignmentModel
from app.modules.authorization.principal_cache import principal_cache


class PlatformAdminService:
    """Durable platform administration outside tenant role assignments."""

    def __init__(self, session: Session):
        self.session = session

    def is_platform_admin(self, user_id: str) -> bool:
        return self.session.scalar(
            select(PlatformAdminAssignmentModel.id).where(
                PlatformAdminAssignmentModel.user_id == user_id,
                PlatformAdminAssignmentModel.status == "active",
            )
        ) is not None

    def grant(
        self,
        *,
        user_id: str,
        granted_by_user_id: str | None,
        reason: str,
    ) -> PlatformAdminAssignmentModel:
        if self.session.get(UserModel, user_id) is None:
            raise LookupError("user not found")
        normalized_reason = reason.strip()[:2000]
        if not normalized_reason:
            raise ValueError("platform administrator grant requires a reason")
        row = self.session.scalar(
            select(PlatformAdminAssignmentModel).where(
                PlatformAdminAssignmentModel.user_id == user_id
            )
        )
        if row is None:
            row = PlatformAdminAssignmentModel(user_id=user_id)
            self.session.add(row)
        row.status = "active"
        row.granted_by_user_id = granted_by_user_id
        row.reason = normalized_reason
        row.revoked_at = None
        row.updated_at = utcnow()
        self.session.add(
            AuthAuditEventModel(
                actor_id=granted_by_user_id,
                action="platform_admin_granted",
                detail_json={"user_id": user_id, "reason": normalized_reason},
            )
        )
        self.session.flush()
        principal_cache.invalidate_user(user_id)
        return row

    def revoke(
        self, *, user_id: str, revoked_by_user_id: str | None, reason: str
    ) -> bool:
        normalized_reason = reason.strip()[:2000]
        if not normalized_reason:
            raise ValueError("platform administrator revocation requires a reason")
        row = self.session.scalar(
            select(PlatformAdminAssignmentModel).where(
                PlatformAdminAssignmentModel.user_id == user_id,
                PlatformAdminAssignmentModel.status == "active",
            )
        )
        if row is None:
            return False
        row.status = "revoked"
        row.reason = normalized_reason
        row.revoked_at = utcnow()
        row.updated_at = row.revoked_at
        self.session.add(
            AuthAuditEventModel(
                actor_id=revoked_by_user_id,
                action="platform_admin_revoked",
                detail_json={"user_id": user_id, "reason": normalized_reason},
            )
        )
        self.session.flush()
        principal_cache.invalidate_user(user_id)
        return True
