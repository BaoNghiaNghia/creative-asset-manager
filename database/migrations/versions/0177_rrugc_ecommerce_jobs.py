"""Durable per-view GatorHats Ecommerce production jobs.

Revision ID: 0177_rrugc_ecommerce_jobs
Revises: 0176_catinat_waste_witness
"""
from alembic import op
import sqlalchemy as sa

revision = "0177_rrugc_ecommerce_jobs"
down_revision = "0176_catinat_waste_witness"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "rrugc_ecommerce_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("source_plan_id", sa.String(36), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("stock_sha256", sa.String(64), nullable=False),
        sa.Column("front_reference_remote_file_id", sa.String(255)),
        sa.Column("front_reference_content_type", sa.String(128)),
        sa.Column("front_reference_size_bytes", sa.Integer()),
        sa.Column("skill_source", sa.String(32), nullable=False),
        sa.Column("skill_id", sa.String(255)),
        sa.Column("skill_name", sa.String(128), nullable=False),
        sa.Column("skill_version", sa.String(64)),
        sa.Column("skill_bundle_sha256", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("processing_job_id", sa.String(36)),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_remote_file_id", sa.String(255)),
        sa.Column("output_content_type", sa.String(128)),
        sa.Column("output_size_bytes", sa.Integer()),
        sa.Column("output_width", sa.Integer()),
        sa.Column("output_height", sa.Integer()),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("created_by_user_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["source_plan_id"], ["rrugc_source_plans.id"],
                                name="fk_rrugc_ecommerce_source", ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "source_plan_id", "source_revision", "stage",
                            name="uq_rrugc_ecommerce_source_revision_stage"),
        sa.CheckConstraint("stage IN ('prod_s2_ec1','prod_s2_ec2','prod_s2_ec3','prod_s2_ec4')",
                           name="ck_rrugc_ecommerce_stage"),
        sa.CheckConstraint("retry_count >= 0", name="ck_rrugc_ecommerce_retry_count"),
    )
    op.create_index("ix_rrugc_ecommerce_tenant_stage", "rrugc_ecommerce_jobs",
                    ["tenant_id", "stage", "status"])

def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT count(*) FROM rrugc_ecommerce_jobs")):
        raise RuntimeError("ecommerce_jobs_not_safe_to_drop")
    op.drop_index("ix_rrugc_ecommerce_tenant_stage", table_name="rrugc_ecommerce_jobs")
    op.drop_table("rrugc_ecommerce_jobs")
