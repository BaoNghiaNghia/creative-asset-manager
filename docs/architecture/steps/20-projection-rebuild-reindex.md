# Step 20 — Projection rebuild and Elasticsearch reindex

Operational search runs are tenant-scoped, paginated, resumable, cancellable,
and persisted in PostgreSQL. Projection rebuild reads only metadata_json and
profile search configuration; it never invokes AI.

Reindex operations create a versioned physical Elasticsearch index, write all
bounded batches directly to it, and atomically switch aliases only after a
successful run. Dry-run performs selection and metrics without changing
projections, Elasticsearch, or aliases.
