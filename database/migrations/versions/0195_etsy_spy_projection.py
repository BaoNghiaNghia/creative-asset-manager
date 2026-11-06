"""Tenant-safe Etsy Spy projection and agent sync (read-only CAM v1).

Revision ID: 0195_etsy_spy_projection
Revises: 0194_typography_g3_pilot_shortlist
"""
from uuid import uuid4
from datetime import datetime, timezone
from alembic import op
import sqlalchemy as sa

revision = "0195_etsy_spy_projection"
down_revision = "0194_typography_g3_pilot_shortlist"
branch_labels = None
depends_on = None

S = sa.String
T = sa.Text
I = sa.Integer
F = sa.Float
DT = sa.DateTime(timezone=True)
J = sa.JSON
B = sa.Boolean


def col(name, kind, *, pk=False, nullable=True):
    return sa.Column(name, kind, primary_key=pk, nullable=False if pk else nullable)


def tenant_agent(*, pk=False):
    return [col("tenant_id", S(255), pk=pk), col("agent_id", S(36), pk=pk)]


def upgrade():
    op.create_table(
        "etsy_spy_agents",
        col("id", S(36), pk=True), col("tenant_id", S(255), nullable=False),
        col("name", S(100), nullable=False), col("machine_label", S(100)),
        col("token_hash", S(64), nullable=False),
        col("active", B, nullable=False),
        col("extension_version", S(40)), col("local_api_version", S(40)),
        col("last_seen_at", DT), col("last_synced_at", DT),
        col("last_cursor", S(180)), col("last_error", S(160)),
        col("created_at", DT, nullable=False),
    )
    op.create_index("ix_etsy_spy_agents_tenant_id", "etsy_spy_agents", ["tenant_id"])
    op.create_index("ix_etsy_spy_agents_tenant_seen", "etsy_spy_agents", ["tenant_id", "last_seen_at"])
    op.create_table(
        "etsy_spy_sync_batches",
        col("id", S(36), pk=True), *tenant_agent(),
        col("batch_id", S(96), nullable=False), col("payload_sha256", S(64), nullable=False),
        col("cursor", S(180)), col("row_count", I, nullable=False),
        col("received_at", DT, nullable=False),
        sa.UniqueConstraint("tenant_id", "agent_id", "batch_id", name="uq_etsy_spy_batch"),
    )
    op.create_index("ix_etsy_spy_sync_batches_tenant_id", "etsy_spy_sync_batches", ["tenant_id"])
    op.create_table(
        "etsy_spy_sessions",
        *tenant_agent(pk=True), col("source_session_id", S(150), pk=True),
        col("mode", S(40)), col("status", S(50)), col("phase", S(80)),
        col("keyword", S(220)), col("revision", I, nullable=False),
        col("started_at", DT), col("updated_at", DT),
    )
    op.create_index("ix_etsy_spy_sessions_tenant_updated", "etsy_spy_sessions", ["tenant_id", "updated_at"])
    op.create_table(
        "etsy_spy_keywords",
        *tenant_agent(pk=True), col("source_session_id", S(150), pk=True),
        col("node_id", S(150), pk=True), col("keyword", S(220), nullable=False),
        col("parent_id", S(150)), col("depth", I), col("priority", F),
        col("status", S(50)), col("reason", T), col("evidence_json", J),
        col("source_url", T), col("matched_count", I), col("updated_at", DT),
    )
    op.create_index("ix_etsy_spy_keywords_tenant_keyword", "etsy_spy_keywords", ["tenant_id", "keyword"])
    op.create_table(
        "etsy_spy_listings",
        *tenant_agent(pk=True), col("source_session_id", S(150), pk=True),
        col("keyword", S(220), pk=True), col("source_kind", S(20), pk=True),
        col("listing_key", S(150), pk=True), col("title", S(700), nullable=False),
        col("url", T), col("badge", S(70)), col("search_badge", S(70)),
        col("source_shop_url", T), col("shop_position", I),
        col("search_position", I), col("bought24h", I), col("views24h", I),
        col("in_carts", I), col("bestseller", B), col("demand_score", F),
        col("tm_key", S(64)), col("metadata_json", J), col("observed_at", DT), col("updated_at", DT),
    )
    op.create_index("ix_etsy_spy_listings_tenant_observed", "etsy_spy_listings", ["tenant_id", "observed_at"])
    op.create_index("ix_etsy_spy_listings_tenant_keyword", "etsy_spy_listings", ["tenant_id", "keyword"])
    op.create_index("ix_etsy_spy_listings_tenant_tm_key", "etsy_spy_listings", ["tenant_id", "agent_id", "tm_key"])
    op.create_table(
        "etsy_spy_trademark_checks",
        *tenant_agent(pk=True), col("check_id", S(96), pk=True),
        col("kind", S(30), nullable=False), col("keyword", S(220)),
        col("title", S(700)), col("status", S(35)), col("risk", S(55)),
        col("live_count", I, nullable=False), col("dead_count", I, nullable=False),
        col("unknown_count", I, nullable=False), col("matched_json", J, nullable=False),
        col("source", S(80)), col("checked_at", DT), col("source_as_of", DT),
    )
    op.create_index("ix_etsy_spy_tm_tenant_title", "etsy_spy_trademark_checks", ["tenant_id", "agent_id", "check_id"])
    op.create_index("ix_etsy_spy_tm_tenant_checked", "etsy_spy_trademark_checks", ["tenant_id", "checked_at"])
    op.create_table(
        "etsy_spy_system_logs",
        *tenant_agent(pk=True), col("event_id", S(180), pk=True),
        col("source_session_id", S(150)), col("event_at", DT, nullable=False),
        col("level", S(25)), col("source", S(80)),
        col("category", S(80)), col("action", S(100)), col("phase", S(100)),
        col("keyword", S(220)), col("message", T), col("stack", T),
    )
    op.create_index("ix_etsy_spy_logs_tenant_at", "etsy_spy_system_logs", ["tenant_id", "event_at"])

    # Existing tenant administrators must receive the newly introduced keys.
    # Viewer/operator/billing roles do NOT get access by default.
    bind = op.get_bind()
    for key, desc in (
        ("etsy_spy.read", "Read Etsy Spy synchronized tenant data"),
        ("etsy_spy.manage", "Pair and revoke Etsy Spy source agents"),
    ):
        exists = bind.execute(
            sa.text("SELECT id FROM permissions WHERE permission_key=:key"), {"key": key}
        ).scalar_one_or_none()
        permission_id = exists or str(uuid4())
        if exists is None:
            bind.execute(sa.text(
                "INSERT INTO permissions (id,permission_key,description,status,created_at,updated_at) "
                "VALUES (:id,:key,:desc,'active',:now,:now)"
            ), {"id": permission_id, "key": key, "desc": desc,
                "now": datetime.now(timezone.utc)})
        admin_roles = bind.execute(sa.text(
            "SELECT id FROM roles WHERE role_key='tenant_admin' AND status='active'"
        )).scalars().all()
        for role_id in admin_roles:
            present = bind.execute(sa.text(
                "SELECT id FROM role_permissions WHERE role_id=:role AND permission_id=:permission"
            ), {"role": role_id, "permission": permission_id}).scalar_one_or_none()
            if present is None:
                bind.execute(sa.text(
                    "INSERT INTO role_permissions (id,role_id,permission_id,created_at) "
                    "VALUES (:id,:role,:permission,:now)"
                ), {"id": str(uuid4()), "role": role_id, "permission": permission_id,
                    "now": datetime.now(timezone.utc)})


def downgrade():
    tables = ("etsy_spy_system_logs", "etsy_spy_trademark_checks", "etsy_spy_listings",
              "etsy_spy_keywords", "etsy_spy_sessions", "etsy_spy_sync_batches", "etsy_spy_agents")
    bind = op.get_bind()
    for table in tables:
        count = bind.execute(sa.text("SELECT COUNT(*) FROM " + table)).scalar()
        if count:
            raise RuntimeError("refuse_to_drop_nonempty_etsy_spy_projection")
    for table in tables:
        op.drop_table(table)
    # Do not automatically remove production permissions or role grants.
