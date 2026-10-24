"""Commit-time wakeups for processing worker LISTEN/NOTIFY (hint-only).

Revision ID: 0180_processing_queue_notify
Revises: 0179_processing_job_due_partial_index

No new indexes or tables. Notifications are hints and are non-durable;
periodic polling remains required for future due dates, expiring leases,
fairness windows and PostgreSQL connection interruptions.
"""
from alembic import op

revision = "0180_processing_queue_notify"
down_revision = "0179_processing_job_due_partial_index"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute("""
CREATE FUNCTION cam_processing_job_wakeup() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IN ('pending','retry')
       AND NEW.cancellation_requested IS FALSE
       AND NEW.next_attempt_at <= statement_timestamp() THEN
        PERFORM pg_notify('cam_processing_queue_wakeup', NEW.job_type);
    END IF;
    RETURN NEW;
END;
$$;
""")
    op.execute("""
CREATE TRIGGER cam_processing_job_wakeup_insert
AFTER INSERT ON processing_jobs
FOR EACH ROW
WHEN (NEW.status IN ('pending','retry') AND NEW.cancellation_requested IS FALSE)
EXECUTE FUNCTION cam_processing_job_wakeup();
""")
    op.execute("""
CREATE TRIGGER cam_processing_job_wakeup_update
AFTER UPDATE OF status, next_attempt_at, job_type, priority, cancellation_requested
ON processing_jobs
FOR EACH ROW
WHEN (
    NEW.status IN ('pending','retry')
    AND NEW.cancellation_requested IS FALSE
    AND (
        OLD.status IS DISTINCT FROM NEW.status
        OR OLD.next_attempt_at IS DISTINCT FROM NEW.next_attempt_at
        OR OLD.job_type IS DISTINCT FROM NEW.job_type
        OR OLD.priority IS DISTINCT FROM NEW.priority
        OR OLD.cancellation_requested IS DISTINCT FROM NEW.cancellation_requested
    )
)
EXECUTE FUNCTION cam_processing_job_wakeup();
""")
    # Quota release and policy re-enablement can make already queued jobs
    # claimable without changing any processing_jobs row.
    op.execute("""
CREATE FUNCTION cam_processing_tenant_policy_wakeup() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        PERFORM pg_notify('cam_processing_queue_wakeup', '*');
        RETURN NULL;
    END IF;
    IF NEW.total_active_jobs < OLD.total_active_jobs
       OR NEW.ai_active_jobs < OLD.ai_active_jobs
       OR NEW.source_active_jobs < OLD.source_active_jobs
       OR NEW.storage_active_jobs < OLD.storage_active_jobs
       OR NEW.total_active_jobs_limit > OLD.total_active_jobs_limit
       OR NEW.ai_active_jobs_limit > OLD.ai_active_jobs_limit
       OR NEW.source_active_jobs_limit > OLD.source_active_jobs_limit
       OR NEW.storage_active_jobs_limit > OLD.storage_active_jobs_limit
       OR NEW.pipeline_enabled IS DISTINCT FROM OLD.pipeline_enabled
       OR NEW.processing_paused IS DISTINCT FROM OLD.processing_paused
       OR NEW.source_sync_enabled IS DISTINCT FROM OLD.source_sync_enabled
       OR NEW.download_enabled IS DISTINCT FROM OLD.download_enabled
       OR NEW.managed_storage_enabled IS DISTINCT FROM OLD.managed_storage_enabled
       OR NEW.ai_analysis_enabled IS DISTINCT FROM OLD.ai_analysis_enabled
       OR NEW.search_v2_enabled IS DISTINCT FROM OLD.search_v2_enabled
       OR NEW.sidecar_enabled IS DISTINCT FROM OLD.sidecar_enabled THEN
        PERFORM pg_notify('cam_processing_queue_wakeup', '*');
    END IF;
    RETURN NULL;
END;
$$;
""")
    op.execute("""
CREATE TRIGGER cam_tenant_policy_wakeup
AFTER INSERT OR UPDATE OR DELETE ON tenant_processing_policies
FOR EACH ROW EXECUTE FUNCTION cam_processing_tenant_policy_wakeup();
""")
    op.execute("""
CREATE FUNCTION cam_processing_provider_policy_wakeup() RETURNS trigger
LANGUAGE plpgsql AS $$
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
       OR NEW.allowed_models_json IS DISTINCT FROM OLD.allowed_models_json
       OR NEW.daily_budget_limit_micros IS DISTINCT FROM OLD.daily_budget_limit_micros
       OR NEW.monthly_budget_limit_micros IS DISTINCT FROM OLD.monthly_budget_limit_micros
    THEN
        PERFORM pg_notify('cam_processing_queue_wakeup', '*');
    END IF;
    RETURN NULL;
END;
$$;
""")
    op.execute("""
CREATE TRIGGER cam_provider_policy_wakeup
AFTER INSERT OR UPDATE OR DELETE ON tenant_provider_policies
FOR EACH ROW EXECUTE FUNCTION cam_processing_provider_policy_wakeup();
""")


def downgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS cam_provider_policy_wakeup ON tenant_provider_policies")
    op.execute("DROP FUNCTION IF EXISTS cam_processing_provider_policy_wakeup()")
    op.execute("DROP TRIGGER IF EXISTS cam_tenant_policy_wakeup ON tenant_processing_policies")
    op.execute("DROP FUNCTION IF EXISTS cam_processing_tenant_policy_wakeup()")
    op.execute("DROP TRIGGER IF EXISTS cam_processing_job_wakeup_update ON processing_jobs")
    op.execute("DROP TRIGGER IF EXISTS cam_processing_job_wakeup_insert ON processing_jobs")
    op.execute("DROP FUNCTION IF EXISTS cam_processing_job_wakeup()")
