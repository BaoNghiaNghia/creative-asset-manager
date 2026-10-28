"""Add tenant-scoped Typography V2 library and immutable style DNA metadata.

Revision ID: 0188_typography_v2_foundation
Revises: 0187_navbar_member_permissions

No existing UGC reference row, source bytes, or job record is changed.
"""
from alembic import op
import sqlalchemy as sa

revision = "0188_typography_v2_foundation"
down_revision = "0187_navbar_member_permissions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rrugc_typography_references",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("workspace_id", sa.String(255), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("thumbnail_asset_id", sa.String(36)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("source_platform", sa.String(64)),
        sa.Column("source_rights_status", sa.String(32), nullable=False),
        sa.Column("source_attribution", sa.Text()),
        sa.Column("privacy", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("campaign_id", sa.String(36)),
        sa.Column("keyword_id", sa.String(36)),
        sa.Column("skill_name", sa.String(128)),
        sa.Column("reference_roles_json", sa.JSON(), nullable=False),
        sa.Column("influence", sa.String(16), nullable=False),
        sa.Column("preference", sa.String(16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("analysis_status", sa.String(24), nullable=False),
        sa.Column("editorial_status", sa.String(24), nullable=False),
        sa.Column("created_by_user_id", sa.String(255), nullable=False),
        sa.Column("reviewed_by_user_id", sa.String(255)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_typography_reference_tenant_id"),
        sa.UniqueConstraint("tenant_id", "content_hash", name="uq_typography_reference_tenant_hash"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            ["rrugc_reference_assets.tenant_id", "rrugc_reference_assets.id"],
            name="fk_typography_reference_asset_tenant", ondelete="RESTRICT",
        ),
        sa.CheckConstraint("source_type IN ('upload','scout','internal')", name="ck_typography_reference_source_type"),
        sa.CheckConstraint(
            "source_rights_status IN ('owned','licensed','permitted_reference','unknown','blocked')",
            name="ck_typography_reference_rights",
        ),
        sa.CheckConstraint("privacy IN ('private','workspace')", name="ck_typography_reference_privacy"),
        sa.CheckConstraint("scope IN ('workspace','campaign','keyword','skill')", name="ck_typography_reference_scope"),
        sa.CheckConstraint("influence = 'balanced'", name="ck_typography_reference_balanced"),
        sa.CheckConstraint("preference IN ('positive','negative','neutral')", name="ck_typography_reference_preference"),
        sa.CheckConstraint(
            "analysis_status IN ('pending','running','ready','failed','needs_review')",
            name="ck_typography_reference_analysis_status",
        ),
        sa.CheckConstraint(
            "editorial_status IN ('candidate','approved','rejected','archived')",
            name="ck_typography_reference_editorial_status",
        ),
        sa.CheckConstraint("priority >= -100 AND priority <= 100", name="ck_typography_reference_priority"),
    )
    op.create_index(
        "ix_typography_reference_library", "rrugc_typography_references",
        ["tenant_id", "editorial_status", "analysis_status", "updated_at"],
    )
    op.create_index(
        "ix_typography_reference_scope", "rrugc_typography_references",
        ["tenant_id", "scope", "campaign_id", "keyword_id"],
    )
    op.create_table(
        "rrugc_typography_style_dna",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("reference_id", sa.String(36), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("analyzer_version", sa.String(64), nullable=False),
        sa.Column("image_hash", sa.String(64), nullable=False),
        sa.Column("typography_region_json", sa.JSON(), nullable=False),
        sa.Column("style_family", sa.String(80), nullable=False),
        sa.Column("font_mood_json", sa.JSON(), nullable=False),
        sa.Column("layout_graph_json", sa.JSON(), nullable=False),
        sa.Column("overlap_rules_json", sa.JSON(), nullable=False),
        sa.Column("color_description", sa.String(250)),
        sa.Column("embroidery_risks_json", sa.JSON(), nullable=False),
        sa.Column("distinctiveness_features_json", sa.JSON(), nullable=False),
        sa.Column("perceptual_hash", sa.String(64)),
        sa.Column("embedding_key", sa.String(255)),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("manual_override_json", sa.JSON()),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reference_id"],
            ["rrugc_typography_references.tenant_id", "rrugc_typography_references.id"],
            name="fk_typography_dna_reference_tenant", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "reference_id", "analyzer_version", "image_hash",
            name="uq_typography_dna_analysis",
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_typography_dna_confidence"),
    )
    op.create_index(
        "ix_typography_dna_style_family", "rrugc_typography_style_dna",
        ["tenant_id", "style_family"],
    )


def downgrade():
    conn = op.get_bind()
    for table in ("rrugc_typography_style_dna", "rrugc_typography_references"):
        if conn.scalar(sa.text("SELECT count(*) FROM " + table)):
            raise RuntimeError("typography_v2_data_must_be_archived_before_downgrade")
    op.drop_index("ix_typography_dna_style_family", table_name="rrugc_typography_style_dna")
    op.drop_table("rrugc_typography_style_dna")
    op.drop_index("ix_typography_reference_scope", table_name="rrugc_typography_references")
    op.drop_index("ix_typography_reference_library", table_name="rrugc_typography_references")
    op.drop_table("rrugc_typography_references")
