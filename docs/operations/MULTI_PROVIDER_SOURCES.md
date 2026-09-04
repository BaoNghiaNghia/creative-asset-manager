# Multi-provider cloud sources

## Authentication boundary

CAM application authentication identifies the user and active tenant. Cloud source OAuth authorizes a specific external source. They are independent: an owner may sign into CAM with Google, connect Google Drive, OneDrive as design@outlook.com, and SharePoint as employee@company.com.

The browser obtains application identity from /api/v1/auth/identity. Connected sources come from /api/sources; no access token, refresh token, OAuth connection ID, Graph cursor, or download URL is returned to the browser.

Credential resolution is column-authoritative: external_source_id -> ExternalSource.oauth_connection_id -> OAuthConnection -> refreshed provider token. source_metadata.oauth_connection_id is migration history only and is never runtime credential authority. Source-sync jobs contain external_source_id and reconciliation state, never an OAuth credential ID. A reconnect changes the source binding, so queued jobs use the current credential.

## Microsoft configuration

Both Microsoft application login and source connect use MICROSOFT_REDIRECT_URI (production: https://assets.example.com/api/auth/microsoft/callback). The one-time PKCE state records application_login, onedrive_connect, or sharepoint_connect.

OneDrive uses delegated User.Read and Files.Read through the common authority. SharePoint uses User.Read, Sites.Read.All, and Files.Read.All through the organizational authority. Do not request Files.ReadWrite for OneDrive. Configure the exact redirect URI and supported account audience in the Entra app registration before enabling connections.

## OneDrive behavior

OneDrive supports My Files browsing, folder traversal, streaming download with Range, and initial/incremental Graph delta sync. It is read-only. It does not support writes, Shared With Me discovery, or shared-link import.

Item identity includes drive and item IDs. Explorer uses Graph children endpoints; source sync uses root delta and an internal opaque deltaLink, not recursive Explorer crawling. Graph continuation URLs must be HTTPS graph.microsoft.com/v1.0; preauthenticated content redirects are used only during an immediate stream and are not persisted.

Disconnect marks only that ExternalSource as disconnected. It does not log out CAM, delete source assets, assets, AI metadata, or unrelated sources. Reconnect preserves source history and OneDrive rejects a changed drive/account identity.

## Rollout and rollback

1. Configure Microsoft client ID/secret, redirect URI, authority/audience, and delegated consent before enabling source connections.
2. Back up production according to the database-backup SOP, run python -m alembic upgrade head, and verify one head.
3. Deploy API/workers, health-check Google and SharePoint, then deploy compatible frontend.
4. Canary a tenant: connect OneDrive, browse root/folder, download one permitted file, sync, reconnect, disconnect, and confirm CAM identity/history remain intact.
5. Expand only after audit/error review.

To disable the feature, stop presenting/using OneDrive connections and roll back the application if necessary; do not delete source records or OAuth data. The schema can remain at 0057. Database downgrade is guarded: if duplicate tenant/provider/account rows exist across purposes, old schema cannot represent them and downgrade refuses without deleting credentials.
