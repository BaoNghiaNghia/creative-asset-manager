"""Persist exact V6.1 QA submissions independently of trusted approval.

Revision ID: 0220_cam_image_qa_records
Revises: 0219_cam_image_approvals
"""
from alembic import op
import sqlalchemy as sa

revision="0220_cam_image_qa_records"
down_revision="0219_cam_image_approvals"
branch_labels=None
depends_on=None


def upgrade():
    op.create_table("cam_image_qa_record_submissions",
        sa.Column("tenant_id",sa.String(255),primary_key=True),
        sa.Column("receipt_id",sa.String(64),primary_key=True),
        sa.Column("pack_id",sa.String(64),nullable=False),
        sa.Column("role",sa.Integer(),nullable=False),
        sa.Column("attempt_id",sa.String(128),nullable=False),
        sa.Column("epoch",sa.Integer(),nullable=False),
        sa.Column("worker_id",sa.String(128),nullable=False),
        sa.Column("plan_sha256",sa.String(64),nullable=False),
        sa.Column("batch_id",sa.String(32),nullable=False),
        sa.Column("record_sha256",sa.String(64),nullable=False),
        sa.Column("record",sa.JSON(),nullable=False),
        sa.Column("received_at",sa.BigInteger(),nullable=False),
        sa.ForeignKeyConstraint(["tenant_id","pack_id","role"],
            ["cam_image_roles.tenant_id","cam_image_roles.pack_id","cam_image_roles.role"],
            name="fk_cam_image_qa_record_role"),
        sa.UniqueConstraint("tenant_id","pack_id","role","attempt_id",
            name="uq_cam_image_qa_record_attempt"),
        sa.CheckConstraint("role BETWEEN 1 AND 13",name="ck_cam_image_qa_record_role"),
        sa.CheckConstraint("epoch >= 1",name="ck_cam_image_qa_record_epoch"))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM cam_image_qa_record_submissions")):
        raise RuntimeError("Refusing to discard QA submission history")
    op.drop_table("cam_image_qa_record_submissions")
