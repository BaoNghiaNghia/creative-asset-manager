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
