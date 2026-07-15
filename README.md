# Creative Asset Manager

A web-based explorer for creative assets stored in Google Drive. Source files and permissions remain in Drive; tags and workflow metadata live in Directus.

## Google Drive MVP

- Lazy folder browsing with breadcrumb navigation
- Grid view, folder search, multi-select and bulk tagging
- Directus tag storage using the stable key provider + item_id
- Demo mode when credentials are not configured
- Provider boundary ready for a later SharePoint adapter

## Development

API:

    cd apps/api
    python -m venv .venv
    pip install -r requirements.txt
    uvicorn app.main:app --reload

Client:

    cd apps/client
    npm install
    npm run dev

Copy .env.example into your runtime environment. Keep Google and Directus tokens server-side. With GOOGLE_DRIVE_ACCESS_TOKEN empty, the API returns demo data.

## Directus collections

Create asset_tags with id, name and color fields. Create asset_tag_assignments with provider, item_id and tag_id fields, and add a unique constraint on provider + item_id + tag_id.

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
