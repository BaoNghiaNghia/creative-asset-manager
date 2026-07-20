# Step 19 — Google Drive metadata sidecar

Metadata sidecars are idempotent exports derived only from PostgreSQL. They are
not authoritative and contain no credentials, signed URLs, or raw provider
authentication data.

Export state is durable and independent from completed AI analysis state.
Google Drive files are found by internal tenant, asset, and analysis identity;
retries update the same remote JSON file.
