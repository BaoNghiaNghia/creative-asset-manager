# Step 26 — Tenant rollout and operational controls

PostgreSQL policies are authoritative. A job is eligible only when its global
stage flags, tenant pipeline/stage policy and matching provider policy allow it.
The worker applies this filter in the atomic claim query before lease acquisition.

Concurrency is reserved in the same claim transaction with durable per-tenant,
category and optional provider counters. Completion, retry, terminal failure and
cooperative release return capacity; expired final leases are reconciled before
terminalization. A pause blocks new claims but never mutates queued or running
jobs.

Administration is authenticated and restricted to an explicit platform-admin
allowlist or a session carrying an administrator role. Tenant administrators are
limited to their own tenant. All mutations append an audit event. The short policy
cache stores configured tenant rows only; global emergency bounds are always
recomputed.
