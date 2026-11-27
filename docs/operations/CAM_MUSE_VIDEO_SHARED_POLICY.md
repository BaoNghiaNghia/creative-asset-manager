# CAM Muse video shared policy V1

One video-only policy registry under the stable production ops directory:
`ops/muse-video-policy/registry.json`. The directory must be private (0700) and writable by the CAM API service account. Registry files and lock are 0600. No database migration, external service, or direct Drive connection.

## Worker contract

Use existing account-scoped authentication and refresh GET /api/v1/muse-bridge/workers/config. Read sharedVideoPolicy and its revision, SHA and settings. New Story Lite job claims pin the snapshot inside the existing story_contract_v33 receipt. The authoritative prompt is the returned pinned per-aspect storySpecV33. If a response is truncated, fetch the existing per-listing story-contract. Never replace a suitable approved hero solely to match a new casting preference. Old pins, strict legacy jobs, image orchestration, worker permissions, provider quotas, leases and uploads remain unchanged.

Synthetic casting profiles are deterministic descriptive variations (480 by default, 100-750 supported), not stored reference photos or independently verified identities. The profile is derived from listing, artwork, aspect and hero request identity; both accounts receive the same profile for a given source. No claim of hundreds of unique generated faces or globally non-repeating actual identities.

Fixed constraints stay in the existing job contract: exact artwork, C1717, native video, allowed duration, 150 mm target, locked background/camera, natural adult identity consistency, provider budget/cooldown, private storage. The registry allows bounded creative settings only: castingVariantCount, facePresentation, expressionStyle, motionStyle, lightingStyle.

## One-location update

A user may request a global change at any worker. That worker uses its existing credential to submit POST /api/v1/muse-bridge/video-policy/proposals with:

```json
{"expectedRevision":1,"changes":{"castingVariantCount":600,"expressionStyle":"natural"}}
```

Read expectedRevision from the actual config, not this example. The response is PENDING_OPERATOR_APPROVAL or ALREADY_ACTIVE. Report the proposal ID and status; never claim the proposal is active before approval.

An authorized operator (existing distinct CAM command credential, never distributed to workers) reads GET /api/v1/muse-bridge/video-policy/operator. POST /video-policy/operator/decision with expectedRevision, action=approve and proposalId. Direct authorized changes use POST /video-policy/operator with the same body as the proposal. Reject uses action=reject; rollback uses action=rollback and rollbackRevision. Rollback publishes a new monotonically increasing revision with previous settings. Stale revision returns 409; refresh and review. Each worker has at most one pending proposal. No raw executable prompt/code or arbitrary policy key is accepted.

Workers read new revisions at normal config refresh and use pinned story spec on new work. No additional poll loop, scheduler, direct worker credentials or duplicated automation. In-flight and pre-existing pins do not change. Owner should verify each runner consumes the returned reference and motion prompts and submits proposals correctly; a server configuration read alone is not runner acknowledgment.

## Deployment and tests

Pre-create the private directory using the same authorized service account that owns the worker registry. Keep it outside immutable releases. Run Muse test suites. Deploy through reviewed standard backend release procedure with no migration. Verify authenticated config policy, existing health, worker/operator auth separation, and story readback without claiming work. Registry updates thereafter are atomic hot-reloads without API restarts. Use version rollback for creative changes, standard immutable release rollback for code regression. Do not force render as a test.
