"""Phase 3C.3B2C2A: isolated, verified QA artwork copies.

Revision ID: 0207_cam_agent_trial_artwork_snapshots
Revises: 0206_etsy_spy_sms_status

Additive and empty on upgrade. This table NEVER references the disposable
Stage 1 Output History row. Sealed copies survive Regenerate deleting it.
No background capture, QA verdict or policy promotion is enabled by DDL.
"""
from alembic import op
import sqlalchemy as sa

revision = "0207_cam_agent_trial_artwork_snapshots"
down_revision = "0206_etsy_spy_sms_status"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cam_agent_trial_artwork_snapshots",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("study_id", sa.String(64), nullable=False),
        sa.Column("keyword_id", sa.String(36), nullable=False),
        sa.Column("source_output_version_id", sa.String(36), nullable=False),
        sa.Column("image_processing_job_id", sa.String(36), nullable=False),
        sa.Column("brief_processing_job_id", sa.String(36), nullable=False),
        sa.Column("source_remote_file_id", sa.String(255), nullable=False),
        sa.Column("snapshot_asset_id", sa.String(255), nullable=False),
        sa.Column("snapshot_remote_file_id", sa.String(255), nullable=True),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "snapshot_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "study_id", "keyword_id"],
            ["cam_agent_trial_exposures.tenant_id",
             "cam_agent_trial_exposures.study_id",
             "cam_agent_trial_exposures.keyword_id"],
            name="fk_cam_trial_snapshot_exposure", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "source_output_version_id",
                            name="uq_cam_trial_snapshot_tenant_output"),
        sa.UniqueConstraint("tenant_id", "snapshot_asset_id",
                            name="uq_cam_trial_snapshot_tenant_asset"),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 16777216",
                           name="ck_cam_trial_snapshot_size"),
        sa.CheckConstraint("width > 0 AND height > 0",
                           name="ck_cam_trial_snapshot_dims"),
        sa.CheckConstraint(
            "((status = 'reserved' AND snapshot_remote_file_id IS NULL AND verified_at IS NULL)"
            " OR (status = 'verified' AND snapshot_remote_file_id IS NOT NULL AND verified_at IS NOT NULL))",
            name="ck_cam_trial_snapshot_seal",
        ),
    )
    op.create_index(
        "ix_cam_trial_snapshot_study_status",
        "cam_agent_trial_artwork_snapshots",
        ["tenant_id", "study_id", "status", "created_at"],
    )


def downgrade():
    # A verified Drive copy is audit evidence; never erase its registry to
    # satisfy rollback. Code-only rollback across an additive revision instead.
    connection = op.get_bind()
    if connection.scalar(sa.text(
        "SELECT COUNT(*) FROM cam_agent_trial_artwork_snapshots"
    )):
        raise RuntimeError(
            "Refusing destructive trial artwork snapshot downgrade with data"
        )
    op.drop_index(
        "ix_cam_trial_snapshot_study_status",
        table_name="cam_agent_trial_artwork_snapshots",
    )
    op.drop_table("cam_agent_trial_artwork_snapshots")
