"""Narrow PostgreSQL claim-queue scans to runnable, noncancelled jobs.

Revision ID: 0179_processing_job_due_partial_index
Revises: 0178_rrugc_ecommerce_front_preview

Created concurrently so the busy processing_jobs table remains writable.
"""

from alembic import op

revision = "0179_processing_job_due_partial_index"
down_revision = "0178_rrugc_ecommerce_front_preview"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_processing_jobs_due_eligible"


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            + INDEX_NAME
            + " ON processing_jobs "
            "(status, next_attempt_at, priority, created_at) "
            "WHERE status IN ('pending','retry') "
            "AND cancellation_requested IS FALSE "
            "AND attempt_count < max_attempts"
        )


def downgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS " + INDEX_NAME)
