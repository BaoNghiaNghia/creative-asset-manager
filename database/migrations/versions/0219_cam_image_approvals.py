"""Separate trusted image plan and saved-PNG review evidence.

Revision ID: 0219_cam_image_approvals
Revises: 0218_cam_image_commands
"""
from alembic import op
import sqlalchemy as sa

revision="0219_cam_image_approvals"
down_revision="0218_cam_image_commands"
branch_labels=None
depends_on=None


def upgrade():
    op.create_table("cam_image_plan_approvals",
        sa.Column("tenant_id",sa.String(255),primary_key=True),
        sa.Column("pack_id",sa.String(64),primary_key=True),
        sa.Column("plan_sha256",sa.String(64),nullable=False),
        sa.Column("evidence",sa.JSON(),nullable=False),
        sa.Column("approved_by",sa.String(128),nullable=False),
        sa.Column("approved_at",sa.BigInteger(),nullable=False),
        sa.Column("revoked",sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.ForeignKeyConstraint(["tenant_id","pack_id"],
            ["cam_image_packs.tenant_id","cam_image_packs.pack_id"],
            name="fk_cam_image_plan_approval_pack"))
    op.create_table("cam_image_qa_approvals",
        sa.Column("tenant_id",sa.String(255),primary_key=True),
        sa.Column("receipt_id",sa.String(64),primary_key=True),
        sa.Column("pack_id",sa.String(64),nullable=False),
        sa.Column("role",sa.Integer(),nullable=False),
        sa.Column("attempt_id",sa.String(128),nullable=False),
        sa.Column("epoch",sa.Integer(),nullable=False),
        sa.Column("worker_id",sa.String(128),nullable=False),
        sa.Column("plan_sha256",sa.String(64),nullable=False),
        sa.Column("image_sha256",sa.String(64),nullable=False),
        sa.Column("verdict",sa.String(16),nullable=False),
        sa.Column("evidence",sa.JSON(),nullable=False),
        sa.Column("reviewed_by",sa.String(128),nullable=False),
        sa.Column("reviewed_at",sa.BigInteger(),nullable=False),
        sa.Column("revoked",sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.ForeignKeyConstraint(["tenant_id","pack_id","role"],
            ["cam_image_roles.tenant_id","cam_image_roles.pack_id","cam_image_roles.role"],
            name="fk_cam_image_qa_approval_role"),
        sa.UniqueConstraint("tenant_id","pack_id","role","attempt_id","image_sha256",
                            name="uq_cam_image_qa_attempt_image"),
        sa.CheckConstraint("role BETWEEN 1 AND 13",name="ck_cam_image_qa_role"),
        sa.CheckConstraint("verdict IN ('pass','hold','unverified')",name="ck_cam_image_qa_verdict"))


def downgrade():
    bind=op.get_bind()
    for name in ("cam_image_qa_approvals","cam_image_plan_approvals"):
        if bind.scalar(sa.text("SELECT COUNT(*) FROM "+name)):
            raise RuntimeError("Refusing to discard trusted image approval history")
    op.drop_table("cam_image_qa_approvals")
    op.drop_table("cam_image_plan_approvals")
