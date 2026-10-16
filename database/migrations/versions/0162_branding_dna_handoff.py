"""Add tenant-scoped unverified Branding DNA browser handoff review queue.

Revision ID: 0162_branding_dna_handoff
Revises: 0161_branding_dna_worker_events
No existing data is changed. This does NOT enable scans or evidence import.
"""
import os
from alembic import op
import sqlalchemy as sa

revision = "0162_branding_dna_handoff"
down_revision = "0161_branding_dna_worker_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bc_handoff_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("shop_id", sa.String(36), sa.ForeignKey("bc_shops.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("candidate_sha256", sa.String(64), nullable=False),
        sa.Column("packet_sha256", sa.String(64), nullable=False),
        sa.Column("report_sha256", sa.String(64), nullable=False),
        sa.Column("report_file", sa.String(110), nullable=False),
        sa.Column("listing_key", sa.String(60), nullable=False),
        sa.Column("title", sa.String(180), nullable=False),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("image_reference_count", sa.Integer(), nullable=False),
        sa.Column("operator_note", sa.String(200), nullable=False),
        sa.Column("review_note", sa.String(300), nullable=False),
        sa.Column("status", sa.String(35), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "shop_id", "candidate_sha256", name="uq_bc_handoff_candidate_replay"),
        sa.CheckConstraint("status IN ('pending_source_review','needs_authorized_export','rejected')",
                           name="ck_bc_handoff_review_status"),
        sa.CheckConstraint("image_reference_count BETWEEN 0 AND 12", name="ck_bc_handoff_image_count"),
    )
    op.create_index("ix_bc_handoff_tenant_shop", "bc_handoff_candidates",
                    ["tenant_id", "shop_id", "created_at"])


def downgrade() -> None:
    # Production must never erase operator review history by accident.
    # Disposable integration test databases may opt in explicitly.
    bind = op.get_bind()
    url = bind.engine.url
    disposable_database = (
        (url.get_backend_name() == "postgresql" and url.database == "cam_integration")
        or (url.get_backend_name() == "sqlite" and (url.database or "").replace("\\\\", "/").rsplit("/", 1)[-1] == "handoff.db")
    )
    if os.environ.get("CAM_ALLOW_HANDOFF_HISTORY_DROP_FOR_TEST") != "1" or not disposable_database:
        raise RuntimeError("Handoff review history is protected; rollback is limited to opted-in disposable CI/test databases.")
    op.drop_index("ix_bc_handoff_tenant_shop", table_name="bc_handoff_candidates")
    op.drop_table("bc_handoff_candidates")
