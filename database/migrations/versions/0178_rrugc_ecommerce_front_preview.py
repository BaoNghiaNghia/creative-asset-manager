"""Persist per-job front embroidery preview references.

Revision ID: 0178_rrugc_ecommerce_front_preview
Revises: 0177_rrugc_ecommerce_jobs
"""
from alembic import op
import sqlalchemy as sa

revision = "0178_rrugc_ecommerce_front_preview"
down_revision = "0177_rrugc_ecommerce_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("rrugc_ecommerce_jobs", sa.Column(
        "front_preview_remote_file_id", sa.String(255)))
    op.add_column("rrugc_ecommerce_jobs", sa.Column(
        "front_preview_content_type", sa.String(128)))
    op.add_column("rrugc_ecommerce_jobs", sa.Column(
        "front_preview_size_bytes", sa.Integer()))
    op.add_column("rrugc_ecommerce_jobs", sa.Column(
        "front_preview_design_sha256", sa.String(64)))


def downgrade():
    bind = op.get_bind()
    populated = bind.scalar(sa.text(
        "SELECT count(*) FROM rrugc_ecommerce_jobs "
        "WHERE front_preview_remote_file_id IS NOT NULL"))
    if populated:
        raise RuntimeError("ecommerce_front_preview_not_safe_to_drop")
    for column in ("front_preview_design_sha256", "front_preview_size_bytes",
                   "front_preview_content_type", "front_preview_remote_file_id"):
        op.drop_column("rrugc_ecommerce_jobs", column)
