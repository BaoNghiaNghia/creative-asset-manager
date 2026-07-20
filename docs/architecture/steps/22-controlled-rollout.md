# Step 22 — Controlled rollout

Step 22 adds operational documentation and does not enable or automate any
feature. The authoritative runbook is
[`docs/operations/CONTROLLED_ROLLOUT.md`](../../operations/CONTROLLED_ROLLOUT.md).

The runbook defines:

- deployment and migration checklists;
- safe feature-flag ordering;
- an isolated pilot-tenant procedure;
- general and Elasticsearch alias rollback;
- worker shutdown and lease-aware drain;
- AI budget emergency stop;
- external provider outage response;
- resumable backfill throttling.

All feature flags remain false by default. Current flags and worker claims are
process/global rather than hard tenant-gated, so shared multi-tenant rollout is
blocked until tenant allowlists and tenant-filtered worker claiming exist. An
isolated pilot deployment is required in the meantime.
