"""Separate application OAuth from source credentials and add OneDrive source state.

Revision ID: 0057_multi_provider_sources
Revises: 0056_application_logs
"""
from __future__ import annotations

from collections import defaultdict

from alembic import op
import sqlalchemy as sa


revision = "0057_multi_provider_sources"
down_revision = "0056_application_logs"
branch_labels = None
depends_on = None

PURPOSES = (
    "application_login",
    "google_drive_source",
    "onedrive_source",
    "sharepoint_source",
    "managed_storage",
    "legacy_mixed",
)
SOURCE_STATUSES = ("active", "reconnect_required", "disconnected")
SOURCE_PROVIDER = {
    "google_drive": "google",
    "onedrive": "microsoft",
    "sharepoint": "microsoft",
}
SOURCE_PURPOSE = {
    "google_drive": "google_drive_source",
    "onedrive": "onedrive_source",
    "sharepoint": "sharepoint_source",
}


def _legacy_connection_id(metadata: object) -> str | None:
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("oauth_connection_id")
    return value if isinstance(value, str) and value.strip() else None


def upgrade() -> None:
    bind = op.get_bind()

    with op.batch_alter_table("oauth_connections") as batch:
        batch.add_column(
            sa.Column(
                "connection_purpose",
                sa.String(length=32),
                nullable=True,
                server_default="legacy_mixed",
            )
        )

    connections = sa.table(
        "oauth_connections",
        sa.column("id", sa.String),
        sa.column("tenant_id", sa.String),
        sa.column("provider", sa.String),
        sa.column("provider_metadata_json", sa.JSON),
        sa.column("connection_purpose", sa.String),
    )
    sessions = sa.table(
        "auth_sessions",
        sa.column("connection_id", sa.String),
        sa.column("tenant_id", sa.String),
    )
    sources = sa.table(
        "external_sources",
        sa.column("id", sa.String),
        sa.column("tenant_id", sa.String),
        sa.column("source_type", sa.String),
        sa.column("source_metadata", sa.JSON),
        # Added immediately before the legacy-binding backfill below. Include
        # both columns in this lightweight table so SQLAlchemy can compile the
        # PostgreSQL UPDATE against the migrated schema.
        sa.column("oauth_connection_id", sa.String),
        sa.column("status", sa.String),
    )

    session_refs = {
        (row.tenant_id, row.connection_id)
        for row in bind.execute(sa.select(sessions.c.tenant_id, sessions.c.connection_id))
    }
    source_refs: dict[tuple[str, str], set[str]] = defaultdict(set)
    source_rows = list(bind.execute(
        sa.select(
            sources.c.id,
            sources.c.tenant_id,
            sources.c.source_type,
            sources.c.source_metadata,
        )
    ).mappings())
    for source in source_rows:
        connection_id = _legacy_connection_id(source["source_metadata"])
        if connection_id:
            source_refs[(source["tenant_id"], connection_id)].add(
                str(source["source_type"])
            )

    connection_rows = list(bind.execute(
        sa.select(
            connections.c.id,
            connections.c.tenant_id,
            connections.c.provider,
            connections.c.provider_metadata_json,
        )
    ).mappings())
    connection_index = {
        (row["tenant_id"], row["id"]): row for row in connection_rows
    }

    for row in connection_rows:
        key = (row["tenant_id"], row["id"])
        source_types = source_refs.get(key, set())
        session_ref = key in session_refs
        metadata = row["provider_metadata_json"]
        metadata_purpose = (
            metadata.get("connection_purpose")
            if isinstance(metadata, dict)
            else None
        )
        if session_ref and source_types:
            purpose = "legacy_mixed"
        elif len(source_types) == 1:
            source_type = next(iter(source_types))
            expected_provider = SOURCE_PROVIDER.get(source_type)
            purpose = (
                SOURCE_PURPOSE[source_type]
                if expected_provider == row["provider"]
                else "legacy_mixed"
            )
        elif session_ref:
            purpose = "application_login"
        elif metadata_purpose == "managed_storage":
            purpose = "managed_storage"
        else:
            purpose = "legacy_mixed"
        bind.execute(
            connections.update()
            .where(connections.c.id == row["id"])
            .values(connection_purpose=purpose)
        )

    with op.batch_alter_table("oauth_connections") as batch:
        batch.alter_column("connection_purpose", nullable=False, server_default=None)
        batch.drop_constraint("uq_oauth_connections_identity", type_="unique")
        batch.create_unique_constraint(
            "uq_oauth_connections_identity_purpose",
            ["tenant_id", "provider", "provider_account_id", "connection_purpose"],
        )
        batch.create_check_constraint(
            "ck_oauth_connections_purpose",
            "connection_purpose IN ('application_login','google_drive_source','onedrive_source','sharepoint_source','managed_storage','legacy_mixed')",
        )

    with op.batch_alter_table("external_sources") as batch:
        batch.add_column(sa.Column("oauth_connection_id", sa.String(length=36), nullable=True))
        batch.add_column(
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="active",
            )
        )

    for source in source_rows:
        connection_id = _legacy_connection_id(source["source_metadata"])
        connection = connection_index.get((source["tenant_id"], connection_id))
        expected_provider = SOURCE_PROVIDER.get(str(source["source_type"]))
        values: dict[str, object] = {}
        if (
            connection is not None
            and expected_provider == connection["provider"]
            and str(source["source_type"]) in SOURCE_PURPOSE
        ):
            values["oauth_connection_id"] = connection_id
        elif connection_id:
            values["status"] = "reconnect_required"
        if values:
            bind.execute(
                sources.update().where(sources.c.id == source["id"]).values(**values)
            )

    with op.batch_alter_table("external_sources") as batch:
        batch.create_foreign_key(
            "fk_external_sources_tenant_oauth_connection",
            "oauth_connections",
            ["tenant_id", "oauth_connection_id"],
            ["tenant_id", "id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            "ck_external_sources_status",
            "status IN ('active','reconnect_required','disconnected')",
        )
        batch.create_index(
            "ix_external_sources_tenant_connection",
            ["tenant_id", "oauth_connection_id"],
        )
        batch.alter_column("status", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(sa.text("""
        SELECT tenant_id, provider, provider_account_id
        FROM oauth_connections
        GROUP BY tenant_id, provider, provider_account_id
        HAVING COUNT(*) > 1
    """)).first()
    if duplicates:
        raise RuntimeError(
            "Cannot downgrade multi-provider OAuth connections while a provider "
            "account has more than one connection purpose. Disconnect or "
            "consolidate those credentials first; no token is deleted automatically."
        )

    with op.batch_alter_table("external_sources") as batch:
        batch.drop_index("ix_external_sources_tenant_connection")
        batch.drop_constraint(
            "fk_external_sources_tenant_oauth_connection", type_="foreignkey"
        )
        batch.drop_constraint("ck_external_sources_status", type_="check")
        batch.drop_column("status")
        batch.drop_column("oauth_connection_id")

    with op.batch_alter_table("oauth_connections") as batch:
        batch.drop_constraint("ck_oauth_connections_purpose", type_="check")
        batch.drop_constraint(
            "uq_oauth_connections_identity_purpose", type_="unique"
        )
        batch.create_unique_constraint(
            "uq_oauth_connections_identity",
            ["tenant_id", "provider", "provider_account_id"],
        )
        batch.drop_column("connection_purpose")
