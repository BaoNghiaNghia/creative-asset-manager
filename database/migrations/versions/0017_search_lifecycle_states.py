"""Add recoverable Elasticsearch lifecycle states.

Revision ID: 0017_search_lifecycle_states
Revises: 0016_active_analysis_integrity

Rollback requires no rows to remain in verified or activating state. Move them
to building or failed before downgrading.
"""
from alembic import op

revision = "0017_search_lifecycle_states"
down_revision = "0016_active_analysis_integrity"
branch_labels = None
depends_on = None

_STATES = (
    "building", "validating", "verified", "activating", "active", "previous",
    "retired", "deletion_pending", "deleted", "failed",
)
_OLD_STATES = (
    "building", "validating", "active", "previous", "retired",
    "deletion_pending", "deleted", "failed",
)

def _constraint(states):
    return "lifecycle_state IN (%s)" % ",".join(repr(value) for value in states)

def upgrade():
    with op.batch_alter_table("search_index_records") as batch:
        batch.drop_constraint("ck_search_index_lifecycle_state", type_="check")
        batch.create_check_constraint("ck_search_index_lifecycle_state", _constraint(_STATES))

def downgrade():
    with op.batch_alter_table("search_index_records") as batch:
        batch.drop_constraint("ck_search_index_lifecycle_state", type_="check")
        batch.create_check_constraint("ck_search_index_lifecycle_state", _constraint(_OLD_STATES))
