"""One capacity-one resource group shared by participating flows.

No claims until integration is independently verified. State persists even
if the owning worker's lease expires; reconciliation is explicit.
"""
from sqlalchemy import BigInteger, Boolean, CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class RenderResource(Base):
    __tablename__ = "cam_render_resources"
    __table_args__ = (
        CheckConstraint("epoch >= 0", name="ck_cam_render_resource_epoch"),
        CheckConstraint("owner_flow IS NULL OR owner_flow IN ('image','video')",
                        name="ck_cam_render_resource_flow"),
    )
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    integration_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    shared_with_video: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    owner_flow: Mapped[str | None] = mapped_column(String(8))
    owner_worker: Mapped[str | None] = mapped_column(String(128))
    owner_operation: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[int | None] = mapped_column(BigInteger)
