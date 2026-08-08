from __future__ import annotations

import argparse
import json

from sqlalchemy import select

from app.core.database import SessionLocal
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import OAuthConnectionModel
from app.providers.google.auth import DRIVE_WRITE_SCOPE


def main() -> int:
    parser = argparse.ArgumentParser(description="Safe Google OAuth connection diagnostic")
    parser.add_argument("--tenant-id")
    parser.add_argument("--connection-id")
    parser.add_argument("--source-id")
    args = parser.parse_args()

    with SessionLocal() as session:
        connection = None
        source = None
        if args.connection_id:
            connection = session.scalar(
                select(OAuthConnectionModel).where(
                    OAuthConnectionModel.id == args.connection_id,
                    OAuthConnectionModel.provider == "google",
                    *([OAuthConnectionModel.tenant_id == args.tenant_id] if args.tenant_id else []),
                )
            )
        if args.source_id:
            source = session.scalar(
                select(ExternalSourceModel).where(
                    ExternalSourceModel.id == args.source_id,
                    ExternalSourceModel.source_type == "google_drive",
                    *([ExternalSourceModel.tenant_id == args.tenant_id] if args.tenant_id else []),
                )
            )
            if connection is None and source is not None:
                connection_id = (source.source_metadata or {}).get("oauth_connection_id")
                if connection_id:
                    connection = session.scalar(
                        select(OAuthConnectionModel).where(
                            OAuthConnectionModel.id == connection_id,
                            OAuthConnectionModel.tenant_id == source.tenant_id,
                            OAuthConnectionModel.provider == "google",
                        )
                    )
        if connection is None:
            query = select(OAuthConnectionModel).where(OAuthConnectionModel.provider == "google")
            if args.tenant_id:
                query = query.where(OAuthConnectionModel.tenant_id == args.tenant_id)
            connection = session.scalar(query.order_by(OAuthConnectionModel.updated_at.desc()).limit(1))
        if connection is None:
            print(json.dumps({"connection": None, "source": None}, indent=2))
            return 0
        scopes = sorted({str(scope) for scope in (connection.scopes_json or ()) if str(scope)})
        print(json.dumps({
            "connection": {
                "id": connection.id,
                "tenant_id": connection.tenant_id,
                "account_email": connection.account_email,
                "status": connection.status,
                "scopes": scopes,
                "has_write_scope": DRIVE_WRITE_SCOPE in scopes,
                "token_expires_at": connection.access_token_expires_at.isoformat() if connection.access_token_expires_at else None,
                "last_refresh_at": connection.last_refresh_at.isoformat() if connection.last_refresh_at else None,
                "refresh_error": connection.refresh_error_json,
            },
            "source": {
                "id": source.id,
                "tenant_id": source.tenant_id,
                "oauth_connection_id": (source.source_metadata or {}).get("oauth_connection_id"),
            } if source else None,
        }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
