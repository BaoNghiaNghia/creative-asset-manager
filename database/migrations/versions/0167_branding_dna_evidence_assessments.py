"""Add audit-only listing evidence assessments.

Revision ID: 0167_branding_dna_evidence_assessments
Revises: 0166_branding_dna_access_reviews
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0167_branding_dna_evidence_assessments"
down_revision = "0166_branding_dna_access_reviews"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bc_listing_evidence_assessments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("shop_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("rights_basis", sa.String(40), nullable=False),
        sa.Column("evidence_reference", sa.String(160)),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("reviewed_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "shop_id", "snapshot_id"],
            ["bc_listing_snapshots.tenant_id", "bc_listing_snapshots.shop_id",
             "bc_listing_snapshots.id"],
            name="fk_bc_evidence_assessment_snapshot_scope",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "decision IN ('needs_independent_source','metadata_consistent','rejected')",
            name="ck_bc_evidence_assessment_decision",
        ),
        sa.CheckConstraint(
            "rights_basis IN ('unknown','consent_reference_recorded','permission_missing')",
            name="ck_bc_evidence_assessment_rights_basis",
        ),
    )
    op.create_index(
        "ix_bc_evidence_assessments_scope_date", "bc_listing_evidence_assessments",
        ["tenant_id", "shop_id", "snapshot_id", "created_at"],
    )


def downgrade():
    bind = op.get_bind()
    url = bind.engine.url
    disposable = (
        url.database == "cam_integration"
        or (url.get_backend_name() == "sqlite"
            and (url.database or "").rsplit("/", 1)[-1] == "evidence-assessment-test.db")
    )
    if (os.getenv("CAM_ALLOW_EVIDENCE_ASSESSMENT_DROP_FOR_TEST") != "1"
            or not disposable):
        raise RuntimeError("Evidence assessment audit history protected; downgrade denied")
    op.drop_index(
        "ix_bc_evidence_assessments_scope_date",
        table_name="bc_listing_evidence_assessments",
    )
    op.drop_table("bc_listing_evidence_assessments")
