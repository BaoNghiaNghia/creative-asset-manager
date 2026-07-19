# External Asset Download Security

## Trust boundary

External URLs are untrusted input. The downloader is infrastructure code and
must not be exposed by a public route until Step 18. It is disabled by default
through `EXTERNAL_ASSET_DOWNLOADER_ENABLED`.

## Network controls

- HTTPS is mandatory and URL credentials are rejected.
- Hostnames must match an explicit exact-host or parent-domain allowlist.
- DNS is resolved and every returned address must be globally routable.
- Loopback, private, link-local, reserved, multicast, unspecified, and known
  cloud metadata addresses are rejected.
- Redirect following is manual. Every redirect repeats scheme, allowlist, DNS,
  and address validation and is subject to a maximum count.
- Connect and read timeouts are separately configurable.

## Content controls

- Responses stream into a private temporary file while enforcing a byte limit
  and calculating SHA-256.
- Image type is selected from magic bytes, never the HTTP Content-Type.
- Pillow verifies the encoded structure, dimensions and total pixel count,
  then performs a full decode.
- PNG, JPEG, GIF, WebP, TIFF and BMP signatures are accepted initially.
- Temporary files are deleted on download failure and when the async context
  manager exits, including caller exceptions.

## Logging

Only redacted URLs may be logged. Redaction removes user information, query
parameters and fragments, including signed URL credentials.

## DNS pinning

The request connects to the validated public IP while preserving the original
hostname in the HTTP Host header and TLS SNI. Environment proxy settings are
disabled so they cannot bypass the validated network destination.

## External ingestion API boundary

- The API is unavailable unless `EXTERNAL_INGESTION_API_ENABLED=true`.
- Bearer keys must be high entropy; only SHA-256 fingerprints are stored.
- Every key is bound to one tenant-owned `external_api` source.
- Database-backed per-credential rate limiting is applied before endpoint work.
- Content length and streamed bytes are both capped; validated canonical JSON is
  capped again before persistence.
- Download URLs must be HTTPS and cannot contain URL credentials or fragments.
- Validation errors omit submitted values so signed URLs are not reflected.
- Tenant/source-scoped reads return 404 for inaccessible ingestion IDs.
- Download URLs are persisted only for asynchronous workers and must never be
  written to application logs without the existing URL-redaction policy.
