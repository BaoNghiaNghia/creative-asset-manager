"""Fix JSON comparison in provider policy wakeup trigger.

Revision ID: 0181_provider_policy_json_wakeup
Revises: 0180_processing_queue_notify

PostgreSQL json has no equality operator. On UPDATE, a trigger using
NEW.allowed_models_json IS DISTINCT FROM OLD.allowed_models_json
raises SQLSTATE 42883, preventing the worker from reserving a provider slot.
Use jsonb for equality comparison while preserving notification semantics.
"""
from alembic import op

revision = "0181_provider_policy_json_wakeup"
down_revision = "0180_processing_queue_notify"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
CREATE OR REPLACE FUNCTION public.cam_processing_provider_policy_wakeup()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        PERFORM pg_notify('cam_processing_queue_wakeup', '*');
        RETURN NULL;
    END IF;
    IF NEW.active_jobs < OLD.active_jobs
       OR NEW.single_active_jobs < OLD.single_active_jobs
       OR NEW.batch_active_jobs < OLD.batch_active_jobs
       OR NEW.active_jobs_limit > OLD.active_jobs_limit
       OR NEW.single_active_jobs_limit > OLD.single_active_jobs_limit
       OR NEW.batch_active_jobs_limit > OLD.batch_active_jobs_limit
       OR NEW.processing_enabled IS DISTINCT FROM OLD.processing_enabled
       OR NEW.processing_paused IS DISTINCT FROM OLD.processing_paused
       OR NEW.single_enabled IS DISTINCT FROM OLD.single_enabled
       OR NEW.batch_enabled IS DISTINCT FROM OLD.batch_enabled
       OR NEW.emergency_stop IS DISTINCT FROM OLD.emergency_stop
       OR NEW.allowed_models_json::jsonb IS DISTINCT FROM OLD.allowed_models_json::jsonb
       OR NEW.daily_budget_limit_micros IS DISTINCT FROM OLD.daily_budget_limit_micros
       OR NEW.monthly_budget_limit_micros IS DISTINCT FROM OLD.monthly_budget_limit_micros
    THEN
        PERFORM pg_notify('cam_processing_queue_wakeup', '*');
    END IF;
    RETURN NULL;
END;
$$;
""")


def downgrade():
    # Keep the safe JSONB comparison: restoring the old JSON equality
    # operator would immediately break every provider quota reservation.
    pass
