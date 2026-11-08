"""Phase 3C.3B1: additive, inert actual-exposure and blinded-QA ledgers.

Revision ID: 0205_cam_agent_trial_evidence
Revises: 0204_cam_agent_holdout_studies

This migration never backfills any exposure from shadow admission.
No trial, candidate policy, worker routing or independent QA is enabled.
Rollback to 0204 is code-only via the prior reviewed bridge; schema downgrade
refuses any real evidence row to prevent irreversible audit deletion.
"""
from alembic import op
import sqlalchemy as sa

revision = "0205_cam_agent_trial_evidence"
down_revision = "0204_cam_agent_holdout_studies"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cam_agent_trial_exposures",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("study_id", sa.String(64), nullable=False),
        sa.Column("keyword_id", sa.String(36), nullable=False),
        sa.Column("processing_job_id", sa.String(36), nullable=False),
        sa.Column("arm", sa.String(12), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("policy_sha256", sa.String(64), nullable=False),
        sa.Column("worker_identity", sa.String(96), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("tenant_id", "study_id", "keyword_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "study_id", "keyword_id"],
            ["cam_agent_trial_assignments.tenant_id",
             "cam_agent_trial_assignments.study_id",
             "cam_agent_trial_assignments.keyword_id"],
            name="fk_cam_trial_exposure_assignment", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["processing_job_id"], ["processing_jobs.id"],
            name="fk_cam_trial_exposure_job", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "processing_job_id", name="uq_cam_trial_exposure_tenant_job",
        ),
        sa.CheckConstraint(
            "arm IN ('control', 'challenger')", name="ck_cam_trial_exposure_arm",
        ),
    )
    op.create_index(
        "ix_cam_trial_exposure_tenant_study_arm",
        "cam_agent_trial_exposures", ["tenant_id", "study_id", "arm"],
    )
    op.create_table(
        "cam_agent_trial_qa_reviews",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("review_id", sa.String(36), nullable=False),
        sa.Column("study_id", sa.String(64), nullable=False),
        sa.Column("keyword_id", sa.String(36), nullable=False),
        sa.Column("output_version_id", sa.String(36), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("blind_token_sha256", sa.String(64), nullable=False),
        sa.Column("reviewer_id", sa.String(255), nullable=False),
        sa.Column("verdict", sa.String(12), nullable=False),
        sa.Column("critical_defect", sa.Boolean(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("tenant_id", "review_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "study_id", "keyword_id"],
            ["cam_agent_trial_exposures.tenant_id",
             "cam_agent_trial_exposures.study_id",
             "cam_agent_trial_exposures.keyword_id"],
            name="fk_cam_trial_qa_exposure", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["output_version_id"], ["rrugc_image_output_versions.id"],
            name="fk_cam_trial_qa_output", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "output_version_id", name="uq_cam_trial_qa_tenant_output",
        ),
        sa.CheckConstraint(
            "verdict IN ('accepted', 'rejected')", name="ck_cam_trial_qa_verdict",
        ),
        sa.CheckConstraint(
            "NOT (critical_defect AND verdict = 'accepted')",
            name="ck_cam_trial_qa_critical",
        ),
    )
    op.create_index(
        "ix_cam_trial_qa_tenant_study_keyword",
        "cam_agent_trial_qa_reviews", ["tenant_id", "study_id", "keyword_id"],
    )


def downgrade():
    bind = op.get_bind()
    if (
        bind.scalar(sa.text("SELECT COUNT(*) FROM cam_agent_trial_qa_reviews"))
        or bind.scalar(sa.text("SELECT COUNT(*) FROM cam_agent_trial_exposures"))
    ):
        raise RuntimeError("Refusing destructive trial evidence downgrade with data")
    op.drop_index(
        "ix_cam_trial_qa_tenant_study_keyword",
        table_name="cam_agent_trial_qa_reviews",
    )
    op.drop_table("cam_agent_trial_qa_reviews")
    op.drop_index(
        "ix_cam_trial_exposure_tenant_study_arm",
        table_name="cam_agent_trial_exposures",
    )
    op.drop_table("cam_agent_trial_exposures")
