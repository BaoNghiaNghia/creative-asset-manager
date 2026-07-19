# Step 07 — Secure external image downloader

## Scope

The streamed downloader is infrastructure-only and remains behind
`EXTERNAL_ASSET_DOWNLOADER_ENABLED=false`. No public ingestion route, worker
registration or existing provider download path is changed.

## Validation sequence

1. Require HTTPS, reject URL credentials, and enforce hostname allowlist.
2. Resolve DNS and reject every non-public or metadata-service address.
3. Connect to the validated IP with the original Host header and TLS SNI.
4. Re-run the complete URL and DNS validation for each bounded redirect.
5. Enforce connect/read timeouts and response byte limit while streaming.
6. Calculate SHA-256 and store bytes in a temporary file.
7. Detect format from magic bytes, inspect dimensions/pixels, verify structure,
   and fully decode with Pillow.
8. Yield the validated file through an async context manager and always delete
   it on exit.

Logs use only the redacted URL without credentials, query or fragment.

## Rollback

Keep `EXTERNAL_ASSET_DOWNLOADER_ENABLED=false`, remove direct callers, and
remove the downloader module and Pillow dependency. No database rollback is
required.
