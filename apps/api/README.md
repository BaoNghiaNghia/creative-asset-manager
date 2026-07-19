# Creative Asset Manager API

## Asset metadata database

The API owns workflow metadata independently from Google Drive and SharePoint. Cloud
files remain in their source system; this database stores application-only data:

- system and custom tags;
- tag assignments;
- 1–5 star ratings;
- the cloud account, provider, and external item ID used to identify each asset.

Local development uses SQLite automatically:

```text
apps/api/data/creative_asset_manager.db
```

The directory is ignored by Git. Start the API normally with `make api`; tables and
the `public` / `draft` system tags are created idempotently during startup.

For production, set a PostgreSQL connection string:

```dotenv
DATABASE_URL=postgresql+psycopg://cam:password@postgres:5432/cam
```

Metadata endpoints:

- `GET /api/tags`
- `POST /api/tags/assign`
- `POST /api/metadata/query`
- `PUT /api/metadata/rating`

`public` and `draft` belong to the same visibility group, so assigning one removes
the other. These tags describe application workflow state; they do not modify Google
Drive or SharePoint sharing permissions.
