"""Persist accumulated active worker execution duration.

Revision ID: 0029_processing_job_duration
Revises: 0028_central_authorization

This records only time while a worker owns a job. It intentionally excludes
queue and retry-backoff time, so operational durations remain meaningful.
"""

from alembic import op
import sqlalchemy as sa

revision = "0029_processing_job_duration"
down_revision = "0028_central_authorization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("processing_jobs") as batch:
        batch.add_column(
            sa.Column(
                "processing_duration_ms",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("processing_jobs") as batch:
        batch.drop_column("processing_duration_ms")