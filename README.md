# Creative Asset Manager

A web-based explorer for creative assets stored in Google Drive. Source files and permissions remain in Drive; tags and workflow metadata live in Directus.

## Google Drive MVP

- Lazy folder browsing with breadcrumb navigation
- Grid view, folder search, multi-select and bulk tagging
- Directus tag storage using the stable key provider + item_id
- Demo mode when credentials are not configured
- Provider boundary ready for a later SharePoint adapter

## Development

API (creates/repairs the virtual environment and installs dependencies automatically):

    make api

Client:

    make client

You can also launch the API directly with:

    bash scripts/dev-api.sh

Copy .env.example into your runtime environment. Keep Google and Directus tokens server-side. With GOOGLE_DRIVE_ACCESS_TOKEN empty, the API returns demo data.

## Directus collections

Create `asset_tags` with `id`, `name` and `color` fields. Create `asset_tag_assignments` with `provider`, `item_id` and `tag_id` fields, and add a unique constraint on `provider + item_id + tag_id`.

Recursive search uses an `asset_metadata` collection as a Google Drive metadata index. Create these fields:

- `id`: string primary key, length 64
- `provider`, `account_id`, `item_id`, `parent_id`, `name`, `normalized_name`, `kind`, `mime_type`: string
- `size`: big integer, nullable
- `modified_at`, `indexed_at`: datetime, nullable
- `thumbnail_url`, `web_url`, `ancestor_path`: text, nullable
- `ancestor_ids`, `ancestor_names`: JSON
- `children_indexed`: boolean, default false

Give the static Directus token read/create/update permissions on `asset_metadata`. Add indexes for `account_id`, `parent_id`, `normalized_name`, and `ancestor_path`; add a unique index for `provider + account_id + item_id`.

Folder listings are indexed in the background. A search covers the current folder and every descendant, crawls only missing or stale branches, and keeps an in-memory fallback when Directus is unavailable. The first search of a large uncached tree can take longer; later searches reuse the Directus index until `DIRECTUS_METADATA_TTL_SECONDS` expires.

## Connect a Google Drive account

1. Create or select a project in Google Cloud Console.
2. Enable the Google Drive API.
3. Configure the OAuth consent screen. While the app is in Testing, add the Google accounts that may sign in as test users.
4. Create an OAuth client with application type Web application.
5. Add this exact authorized redirect URI:

       http://localhost:8000/api/auth/google/callback

6. Copy .env.example to .env and set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.
7. Restart FastAPI, open the client, and choose Sign in with Google.

The MVP requests openid, email, profile, and drive.readonly. The Drive scope permits read-only access to the account's complete Drive and is classified by Google as restricted. Public production deployments must complete Google's applicable OAuth verification requirements.

OAuth refresh tokens are held only in the API process for this local MVP. Restarting the API signs users out. Before production, persist refresh tokens in encrypted server-side storage and replace the in-memory session store with Redis or a database-backed session store.
