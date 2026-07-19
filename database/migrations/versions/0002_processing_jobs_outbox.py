"""Create processing jobs and transactional outbox.

Revision ID: 0002_processing_jobs_outbox
Revises: 0001_asset_registry

Rollback: stop all job/outbox consumers, export pending and failed records if
needed, then downgrade. The downgrade drops only the two Step 05 tables.
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_processing_jobs_outbox"
down_revision = "0001_asset_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.String(255)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_processing_jobs_tenant_key"),
        sa.CheckConstraint("priority >= 0", name="ck_processing_jobs_priority"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_processing_jobs_attempt_count"),
        sa.CheckConstraint("max_attempts > 0", name="ck_processing_jobs_max_attempts"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'retry', 'completed', 'failed')",
            name="ck_processing_jobs_status",
        ),
    )
    op.create_index(
        "ix_processing_jobs_available",
        "processing_jobs",
        ["status", "next_attempt_at", "priority", "created_at"],
    )
    op.create_index(
        "ix_processing_jobs_lease", "processing_jobs", ["status", "lease_expires_at"]
    )
    op.create_index(
        "ix_processing_jobs_entity",
        "processing_jobs",
        ["tenant_id", "entity_type", "entity_id"],
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.String(255)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_outbox_events_tenant_key"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_outbox_events_attempt_count"),
        sa.CheckConstraint("max_attempts > 0", name="ck_outbox_events_max_attempts"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'published', 'failed')",
            name="ck_outbox_events_status",
        ),
    )
    op.create_index(
        "ix_outbox_events_available",
        "outbox_events",
        ["status", "next_attempt_at", "created_at"],
    )
    op.create_index(
        "ix_outbox_events_lease", "outbox_events", ["status", "lease_expires_at"]
    )
    op.create_index(
        "ix_outbox_events_entity",
        "outbox_events",
        ["tenant_id", "entity_type", "entity_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_entity", table_name="outbox_events")
    op.drop_index("ix_outbox_events_lease", table_name="outbox_events")
    op.drop_index("ix_outbox_events_available", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_processing_jobs_entity", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_lease", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_available", table_name="processing_jobs")
    op.drop_table("processing_jobs")
