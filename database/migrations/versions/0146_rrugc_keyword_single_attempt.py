"""Cap outstanding Stage 1 keyword image jobs to a single worker attempt.

Revision ID: 0146_rrugc_keyword_single_attempt
Revises: 0145_rrugc_stage1_output_names
"""
from alembic import op

revision = "0146_rrugc_keyword_single_attempt"
down_revision = "0145_rrugc_stage1_output_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # New code enqueues with max_attempts=1. Existing pending/running/retry
    # Stage 1 jobs must also be capped; otherwise a previously queued
    # 3-attempt job may still execute repeatedly after the deploy.
    op.execute("""
        UPDATE processing_jobs
        SET max_attempts = 1,
            status = CASE
                WHEN status IN ('pending', 'retry') AND attempt_count >= 1
                THEN 'failed' ELSE status END,
            last_error_code = CASE
                WHEN status IN ('pending', 'retry') AND attempt_count >= 1
                THEN COALESCE(last_error_code, 'keyword_image_attempt_limit')
                ELSE last_error_code END,
            last_error_message = CASE
                WHEN status IN ('pending', 'retry') AND attempt_count >= 1
                THEN COALESCE(last_error_message, 'Stage 1 permits one worker attempt per job.')
                ELSE last_error_message END,
            completed_at = CASE
                WHEN status IN ('pending', 'retry') AND attempt_count >= 1
                THEN CURRENT_TIMESTAMP ELSE completed_at END
        WHERE job_type = 'rrugc_keyword_image_generate'
          AND status IN ('pending', 'retry', 'processing')
    """)
    op.execute("""
        UPDATE rrugc_keyword_image_jobs
        SET status = 'failed',
            last_error_code = COALESCE(last_error_code, 'keyword_image_attempt_limit'),
            last_error_message = COALESCE(
                last_error_message, 'Stage 1 permits one worker attempt per job.')
        WHERE status IN ('queued', 'running')
          AND processing_job_id IN (
            SELECT id FROM processing_jobs
            WHERE job_type = 'rrugc_keyword_image_generate'
              AND status = 'failed' AND attempt_count >= 1
          )
    """)


def downgrade() -> None:
    # Historical attempts and terminal failures must never be recreated.
    pass
