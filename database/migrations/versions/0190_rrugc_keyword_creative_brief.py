"""Durable tenant-isolated creative briefs. Additive, no existing Stage 1 rows changed."""
from alembic import op
import sqlalchemy as sa

revision = "0190_rrugc_keyword_creative_brief"
down_revision = "0189_typography_v2_analysis_queue"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rrugc_keyword_creative_briefs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("keyword_id", sa.String(36), nullable=False),
        sa.Column("keyword_sha256", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(48), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("brief_json", sa.JSON()),
        sa.Column("last_error_code", sa.String(80)),
        sa.Column("processing_job_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["keyword_id"], ["rrugc_keyword_volumes.id"],
                                name="fk_rrugc_creative_brief_keyword", ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "keyword_id", name="uq_rrugc_creative_brief_keyword"),
        sa.CheckConstraint("status IN ('queued','running','completed','failed')",
                           name="ck_rrugc_creative_brief_status"),
    )
    op.create_index(
        "ix_rrugc_creative_brief_status", "rrugc_keyword_creative_briefs",
        ["tenant_id", "status", "created_at"],
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM rrugc_keyword_creative_briefs")):
        raise RuntimeError("creative_briefs_must_be_empty_before_downgrade")
    op.drop_index("ix_rrugc_creative_brief_status", table_name="rrugc_keyword_creative_briefs")
    op.drop_table("rrugc_keyword_creative_briefs")
