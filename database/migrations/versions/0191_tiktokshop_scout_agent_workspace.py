"""Isolate Pinterest Scout agent campaign claims per workspace.

Existing agents remain GatorHats. Migration does not change any campaign,
source-plan, candidate, job or output row.
"""
from alembic import op
import sqlalchemy as sa

revision = "0191_tiktokshop_scout_agent_workspace"
down_revision = "0190_rrugc_keyword_creative_brief"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("rrugc_scout_agents") as batch:
        batch.add_column(sa.Column("workspace", sa.String(24), nullable=False,
                                   server_default="gatorhats"))
    op.create_index("ix_rrugc_agent_tenant_workspace_active",
                    "rrugc_scout_agents", ["tenant_id", "workspace", "active"])


def downgrade():
    # Never silently convert an active TikTokShop agent to GatorHats.
    bind = op.get_bind()
    if bind.scalar(sa.text(
        "SELECT count(*) FROM rrugc_scout_agents WHERE workspace != 'gatorhats'"
    )):
        raise RuntimeError("cannot_downgrade_tiktokshop_scout_agents")
    op.drop_index("ix_rrugc_agent_tenant_workspace_active",
                  table_name="rrugc_scout_agents")
    with op.batch_alter_table("rrugc_scout_agents") as batch:
        batch.drop_column("workspace")
