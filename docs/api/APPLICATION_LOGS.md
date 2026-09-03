# External application log API

This API accepts structured logs from other applications and keeps them for a maximum of 10 days. Each application has its own API key and optional JSON Schema, so one application cannot read another application's logs or submit a different shape accidentally.

## 1. Create an application credential

A signed-in tenant administrator calls:

```http
POST /api/v1/tenants/{tenant_id}/log-applications
Content-Type: application/json

{
  "slug": "order-worker",
  "display_name": "Order Worker",
  "payload_schema": {
    "type": "object",
    "required": ["order_id"],
    "properties": {
      "order_id": {"type": "string"},
      "duration_ms": {"type": "integer", "minimum": 0}
    },
    "additionalProperties": true
  }
}
```

The response contains `api_key` once. Store it in the external application's secret manager. CAM stores only its SHA-256 digest. Never commit the key or put it in a URL.

Management endpoints:

- `GET /api/v1/tenants/{tenant_id}/log-applications`
- `PATCH /api/v1/tenants/{tenant_id}/log-applications/{application_id}` updates name, schema, or active state
- `POST /api/v1/tenants/{tenant_id}/log-applications/{application_id}/rotate-key` invalidates the old key and returns a new key once

## 2. Store a log

```bash
curl -X POST 'https://creative-assets.ddns.net/api/v1/application-logs' \\
  -H 'Authorization: Bearer <CAM_LOG_API_KEY>' \\
  -H 'Idempotency-Key: <stable-event-id>' \\
  -H 'Content-Type: application/json' \\
  -d '{
    "level": "error",
    "event_type": "order.processing_failed",
    "message": "Payment provider rejected the request",
    "trace_id": "trace-01J...",
    "occurred_at": "2026-09-03T10:15:30+07:00",
    "payload": {"order_id": "ORD-123", "duration_ms": 812}
  }'
```

Fields:

- `level`: `trace|debug|info|warning|error|critical`; defaults to `info`.
- `event_type`: stable machine-readable event name, up to 128 characters.
- `message`: optional human-readable text, up to 20,000 characters.
- `trace_id`: optional correlation identifier used to find a request across services.
- `occurred_at`: optional ISO-8601 timestamp with timezone; server receipt time is used when absent.
- `payload`: application-specific JSON object, maximum 256 KiB, validated against the configured schema when present.
- `Idempotency-Key`: optional but recommended for retries. A duplicate with the same body returns the existing log with HTTP 200; reusing the key with a different body returns HTTP 409; a new log returns HTTP 201.

The response includes immutable `received_at` and `expires_at`. Expiry is always 10 days after server receipt; callers cannot extend it.

## 3. Get logs

```bash
curl 'https://creative-assets.ddns.net/api/v1/application-logs?level=error&event_type=order.processing_failed&limit=100&offset=0' \\
  -H 'Authorization: Bearer <CAM_LOG_API_KEY>'
```

Optional filters are `from`, `to` (ISO-8601), `level`, `event_type`, and `trace_id`. Results are newest first. `limit` is 1-200 and `offset` is 0-10,000. The bearer key can only retrieve logs belonging to its application.

Expired rows are excluded and deleted during both reads and writes. The existing daily retention worker also deletes expired rows in bounded batches; production must keep `PROCESSING_JOBS_ENABLED=true` and `RETENTION_CLEANUP_ENABLED=true`.

## Prompt to give the Codex task in another application

> Inspect this application's current logging and error-handling code. Propose an integration with the CAM External Application Log API described in APPLICATION_LOGS.md. Define a JSON Schema tailored to this application; map existing severity, event names, timestamps, trace/correlation IDs, and structured context; redact passwords, authorization headers, cookies, tokens, personal data, and full request/response bodies; use a stable Idempotency-Key for retryable delivery; add bounded timeouts and a local non-blocking retry buffer so CAM downtime never blocks the application. Do not implement yet. Return the proposed schema, field mapping, redaction rules, expected daily volume, and exact files that would change.
