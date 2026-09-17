# Creative Asset Manager Security Governance and AI-Agent Hardening

**Status:** ACTIVE GOVERNANCE BASELINE  
**Applies to:** source code, infrastructure, CI/CD, database, search/indexing, workers, provider integrations, managed storage, backups, production operations, and AI coding agents  
**Primary branch:** `main`  
**Last updated:** 2026-09-17

This document is the umbrella security policy for Creative Asset Manager (CAM). It does not replace implementation-specific architecture documents. It defines the security invariants, change-control rules, AI-agent permissions, CI expectations, production boundaries, and recovery requirements that all implementation work must preserve.

Implementation-specific security details remain authoritative in:

- `docs/architecture/AGENT.md`
- `docs/architecture/SECURITY.md`
- `docs/architecture/DATA_MODEL.md`
- `docs/architecture/PIPELINE.md`
- `docs/architecture/SEARCH.md`
- `docs/operations/DATABASE_BACKUP.md`
- `docs/plans/DATABASE_BACKUP_IMPLEMENTATION.md`

If a security-sensitive implementation intentionally changes an invariant in this document, the change must be explicit, reviewed, tested, documented, and accompanied by a rollback plan. An AI agent must never silently weaken an invariant merely to make a task or test pass.

---

## 1. Security objectives

CAM is designed so that a defect, compromised dependency, misconfigured worker, or incorrect AI-generated change has a limited blast radius and can be detected and reversed.

The system must preserve these properties:

1. Tenant and source boundaries are enforced server-side.
2. PostgreSQL remains authoritative for application state and durable identity.
3. Elasticsearch and other derived projections are rebuildable and are never treated as the sole source of truth.
4. Source assets are not destroyed as a side effect of deleting or rebuilding derived data.
5. Externally visible processing is idempotent and retry-safe.
6. Authentication and authorization fail closed.
7. Secrets do not enter source control, logs, search documents, client bundles, or durable error payloads.
8. Production changes are attributable, reviewable, reversible, and limited to the minimum required privilege.
9. Backups are not considered reliable until restore has been demonstrated in an isolated environment.
10. AI coding agents may assist broadly in development, but must not become the sole authority for implementation, verification, and production execution.

The security goal is not to assume that bugs never occur. The goal is to make unsafe changes difficult to merge, limit the damage if they reach runtime, detect failures quickly, and provide a tested recovery path.

---

## 2. System source-of-truth model

CAM already defines the following architectural contract and this governance policy adopts it:

```text
PostgreSQL
  -> authoritative assets
  -> source relationships
  -> metadata
  -> tenant/user/RBAC state
  -> processing/job state
  -> durable audit/security state
  -> search projection inputs

Asset/source provider or managed storage
  -> authoritative asset bytes where defined by provider/storage architecture

Elasticsearch
  -> derived, rebuildable search index

AI metadata/search projections/previews/embeddings
  -> derived data unless an implementation-specific document explicitly states otherwise
```

Deleting or rebuilding a derived system must not implicitly delete authoritative source data.

### Identity invariants

The architecture identity rules remain:

- Source identity: `tenant_id + external_source_id + external_asset_id`.
- Content identity: `tenant_id + SHA-256 content hash`.
- Filename, URL, folder path, modified timestamp, display name, and provider metadata are not permanent content identity.
- Provider IDs must not be accepted as internal CAM asset IDs unless explicitly translated and authorized.

---

## 3. Trust boundaries

Every security review must reason about at least these boundaries:

```text
User / Desktop / Browser
          |
          v
      CAM API
       / | \
      /  |  \
PostgreSQL  Elasticsearch
      |          |
      v          v
 Durable      Derived
  state       search
      |
      +-------------------+
      |                   |
      v                   v
   Workers          Provider adapters
      |                   |
      v                   v
 AI services        Drive / Graph /
 storage / render   external sources
```

Untrusted or partially trusted inputs include:

- browser and desktop requests;
- OAuth/provider responses;
- external URLs;
- filenames and paths;
- image/video/document contents;
- AI/provider output;
- webhook/external ingestion payloads;
- search query syntax;
- PR titles, branch names, issue content, and other CI event metadata;
- third-party package metadata;
- worker job payloads created from external input.

Data crossing a trust boundary must be authenticated where applicable, authorized, validated, bounded, and safely logged.

---

## 4. Non-negotiable security invariants

These invariants must be encoded in tests wherever practical.

### 4.1 Tenant and object isolation

- A principal may access an asset only through an authorized tenant context.
- Repository queries for tenant-owned resources retain explicit tenant predicates even when service-layer authorization exists.
- Search must not return assets outside the authorized tenant/source/folder scope.
- A user guessing or enumerating another tenant's object ID must receive no unauthorized object data.
- Authorization must not rely on frontend visibility.
- Email domain, provider ownership, folder naming, OAuth scope, or provider account type must not implicitly grant CAM tenant roles.

Minimum regression scenarios:

```text
principal_A -> asset_B                       => denied
principal_A -> search tenant_B              => no tenant_B results
viewer_scope_X -> asset outside scope_X     => denied
provider_object_id -> internal asset lookup => no boundary bypass
inactive membership -> protected API        => denied
revoked session -> protected API            => denied
```

### 4.2 Source and derived-data safety

- Elasticsearch may be deleted and rebuilt without deleting source assets.
- Search projections, embeddings, previews, thumbnails, and AI metadata may not acquire ownership semantics that can destroy source data.
- Reindex/reprojection operations must preserve asset provenance.
- Deleting derived data must require only derived-data authority.
- Destructive source deletion must use a separate explicit operation and authorization path.

### 4.3 Job and worker safety

- Retrying a job must not create duplicate durable effects.
- Duplicate delivery must be safe.
- Worker crashes must not silently mark incomplete work successful.
- Provider timeout, rate limit, or encoder saturation must not corrupt authoritative asset state.
- Job state transitions must be bounded and auditable.
- Cancel/retry/force operations must preserve the authorization model already defined for AI Operations.

Required regression themes include:

```text
same job executed twice       -> one logical durable result
worker crashes mid-operation  -> retryable/failed, never false success
provider returns 429/5xx      -> bounded retry/backoff behavior
embedding generation fails    -> source asset remains valid
reindex fails halfway         -> authoritative source remains intact
```

### 4.4 File, media, and external URL safety

Existing controls in `docs/architecture/SECURITY.md` remain mandatory, including HTTPS-only external downloads, host allowlisting, DNS/IP validation, redirect revalidation, private-network/metadata-address blocking, size limits, magic-byte type checks, bounded image decoding, temporary-file cleanup, redacted URL logging, and proxy bypass prevention.

Additional invariant expectations:

- User-controlled paths cannot escape a managed root.
- Archive extraction, if introduced, must defend against path traversal and decompression bombs.
- File extension alone is never sufficient type validation.
- Media parsing failures must not expose local paths or secrets.
- Any future URL-fetch feature must reuse the hardened downloader boundary rather than implementing a parallel unrestricted HTTP client.

### 4.5 Secrets and credentials

Secrets include database credentials, OAuth access/refresh tokens, encryption keys, API keys, SSH keys, deployment keys, managed-storage credentials, service-account credentials, backup credentials, and provider secrets.

Secrets must never be:

- committed to Git;
- embedded in client/Electron bundles;
- printed to normal application logs;
- stored in Elasticsearch;
- included in test fixtures that reach the repository;
- placed in filenames, branch names, issue titles, commit messages, or artifact names;
- passed to an AI agent unless the operation explicitly requires the secret and the environment is authorized for that operation.

Production credentials must be distinct from development/test credentials. Prefer short-lived/OIDC or narrowly scoped credentials where available.

### 4.6 Authentication and authorization

The current architecture establishes `CurrentPrincipal`, tenant RBAC, separate platform administration, durable sessions, provider-subject identity, and fail-closed login admission. New protected routes must reuse these mechanisms rather than introduce parallel authorization logic.

Any change to auth/session/RBAC code is high-risk and requires:

- negative authorization tests;
- tenant mismatch tests;
- disabled/revoked-state tests;
- review of audit events;
- explicit rollback notes.

### 4.7 Database and migration safety

Every schema migration must:

- have a single clear head;
- have downgrade/rollback notes, or explicitly document why downgrade is unsafe;
- avoid destructive data loss during normal deployment unless explicitly approved;
- preserve tenant constraints and ownership boundaries;
- be exercised in integration CI against supported PostgreSQL versions;
- consider mixed-version deploy behavior when applicable.

An AI agent must not execute a destructive migration against production without explicit human authorization for that exact operation.

---

## 5. AI coding-agent governance

CAM assumes AI agents such as Codex may write a significant portion of code. Security therefore depends on constraining agent authority rather than manually reviewing every generated line.

### 5.1 Principle

```text
AI may implement.
Independent controls must verify.
Humans retain authority over high-blast-radius production changes.
```

The agent that writes a change must not be able to unilaterally weaken the test, approve the change, and deploy it to production as one uninterrupted authority chain.

### 5.2 Default AI permissions

Allowed by default:

- read repository code and documentation;
- create/edit source code on an authorized working tree or branch;
- create tests and documentation;
- run formatter, linter, type checker, unit tests, integration tests, build, and local containers;
- inspect development/staging logs that do not expose secrets;
- inspect sanitized production logs when explicitly permitted;
- propose migrations and deployment changes;
- create a rollback plan.

Not allowed by default:

- unrestricted `root` production SSH;
- production database write access;
- production secret-store modification;
- production IAM changes;
- DNS/firewall/network-policy changes;
- destructive object-storage operations;
- deleting production assets;
- executing production migrations;
- deploying directly to production;
- disabling or weakening CI/security checks to make a change pass;
- changing security boundaries without documenting and testing the new boundary.

Any temporary elevation must be explicit, task-specific, time-bounded where possible, and limited to the minimum necessary operation.

### 5.3 Mandatory reading before sensitive changes

Before modifying any of the following, an AI agent must read this document plus the relevant implementation documents:

| Change area | Required documents |
| --- | --- |
| Architecture/data model | `docs/architecture/AGENT.md`, `DATA_MODEL.md` |
| Auth/RBAC/OAuth/session | this document, `docs/architecture/SECURITY.md` |
| Search/Elasticsearch | this document, `docs/architecture/SEARCH.md` |
| Workers/jobs/AI operations | this document, `docs/architecture/PIPELINE.md`, `SECURITY.md` |
| URL/file ingestion | this document, `docs/architecture/SECURITY.md` |
| Database backup | `docs/operations/DATABASE_BACKUP.md`, `docs/plans/DATABASE_BACKUP_IMPLEMENTATION.md` |
| Production/deployment | this document and relevant `docs/deployment/` documents |

### 5.4 Required completion report

For security-sensitive changes the agent must report:

```text
Change summary
Files changed
Behavior changed
Security boundary affected
Invariants affected
Data migration? yes/no
New dependency? yes/no
Secrets/credentials impact
Production permissions required
Tests added/updated
Tests actually run and results
Known risks
Rollback procedure
```

---

## 6. Change risk classification

### Low risk

Examples: documentation, styling, isolated non-security UI, tests that do not change production behavior.

Normal CI is sufficient unless the change unexpectedly touches a security-sensitive path.

### Medium risk

Examples: search ranking, worker scheduling, provider adapter behavior, new dependency, non-destructive schema extension, upload behavior, background retries.

Requires relevant integration tests and review of failure/idempotency behavior.

### High risk

Examples:

- authentication/session/OAuth;
- authorization/RBAC/tenant scope;
- production deployment;
- database migrations that modify or delete existing data;
- secrets/encryption/key rotation;
- file/URL ingestion security;
- asset deletion;
- backup/restore;
- IAM, DNS, firewall, networking;
- billing/provider credential changes;
- global emergency controls.

High-risk changes require explicit human review before production execution.

---

## 7. Development and merge workflow

Target workflow:

```text
AI/human implementation
        |
        v
small focused diff
        |
        v
format / lint / typecheck
        |
        v
unit tests
        |
        v
security invariant tests
        |
        v
PostgreSQL / Elasticsearch / pipeline integration tests
        |
        v
SAST / secret / dependency / container checks as applicable
        |
        v
human review for high-risk changes
        |
        v
merge
        |
        v
staged/reviewed deployment
        |
        v
production verification + rollback readiness
```

Direct production edits that bypass Git history should be emergency-only. Any emergency production change must be backported immediately into source control and documented.

Small commits and focused changes are preferred because they reduce review complexity and improve rollback precision.

---

## 8. CI security baseline

CAM already runs substantial CI coverage for frontend, API/worker/provider unit tests, PostgreSQL migrations/integration, Elasticsearch integration, durable pipeline E2E, and Windows Electron packaging. These controls remain mandatory.

Security hardening should extend the existing CI rather than replace it.

### 8.1 Static application security testing

Recommended baseline:

- CodeQL for supported Python and JavaScript/TypeScript code;
- Semgrep only where it adds useful CAM-specific rules or coverage;
- avoid duplicate scanners with identical purpose unless there is a documented gap.

Security scanners are advisory until tuned. After false positives are understood, high-confidence/high-severity rules may become merge-blocking.

### 8.2 Secret scanning

Add automated secret scanning using Gitleaks or an equivalent repository/CI control.

Expected policy:

- newly introduced verified secrets fail CI;
- test/example values are clearly fake;
- false-positive suppressions are narrow and documented;
- historical leaks trigger credential rotation, not only source deletion.

### 8.3 Dependency and container scanning

Use Trivy, GitHub dependency tooling, or equivalent to identify known vulnerabilities and image/configuration issues.

Do not blindly fail every build on every CVE. Merge-blocking policy should prioritize:

- exploitable critical/high vulnerabilities;
- directly reachable vulnerable dependencies;
- vulnerable internet-facing/runtime packages;
- known exploited vulnerabilities;
- insecure container/IaC configuration with real production impact.

Accepted risk must be explicit and time-bounded where practical.

### 8.4 GitHub Actions hardening

The following should be the target state:

- default `GITHUB_TOKEN` permissions remain least-privilege;
- third-party actions are reviewed and preferably pinned to immutable SHAs;
- untrusted PR metadata is not interpolated directly into shell commands;
- `pull_request_target` is avoided for workflows that execute untrusted code;
- production secrets are exposed only to protected deployment jobs/environments;
- workflow changes receive owner/security review;
- GitHub Actions must not be permitted to self-approve security-sensitive PRs.

---

## 9. Security invariant test suite

CAM should maintain a small, high-value suite of tests focused on behaviors that must never regress. Exact names may differ, but coverage should include the following concepts:

```text
asset_cannot_escape_tenant_scope
user_cannot_read_foreign_asset
search_never_returns_foreign_tenant_asset
viewer_folder_scope_is_server_enforced
provider_id_cannot_bypass_internal_asset_authorization
revoked_session_cannot_access_protected_route
inactive_membership_cannot_access_tenant_route

derived_index_delete_does_not_delete_source_asset
reindex_preserves_asset_provenance
projection_rebuild_requires_no_new_ai_call

failed_job_is_safe_to_retry
retry_does_not_duplicate_durable_effect
worker_crash_does_not_mark_job_successful
encoder_busy_response_does_not_corrupt_asset_state

invalid_crop_cannot_escape_image_bounds
user_path_cannot_escape_managed_storage_root
external_url_cannot_resolve_to_private_or_metadata_network
redirect_rechecks_ssrf_policy
oversized_download_is_rejected
signed_url_is_redacted_from_logs

secret_is_not_exposed_in_error_payload
secret_is_not_written_to_search_projection
```

These tests are more valuable than pursuing coverage percentage alone.

---

## 10. Production access and deployment

Production should follow the principle:

```text
read broadly where operationally necessary;
write narrowly;
destructive actions require explicit approval.
```

Preferred deployment chain:

```text
working branch / reviewed change
        -> CI
        -> approval for high-risk changes
        -> narrowly scoped deploy identity
        -> production
```

Avoid a model where one agent simultaneously has repository write access, unrestricted production SSH, production database credentials, secret-store access, and service restart permission. Combining those capabilities creates excessive blast radius.

### Production identities

Where feasible, separate:

- read-only observability identity;
- deployment identity;
- database migration identity;
- backup identity;
- emergency administrative identity.

Do not reuse tenant/user provider OAuth credentials as infrastructure credentials.

### Destructive operations

The following require explicit approval and a recovery/rollback plan:

- production table/column/data deletion;
- source asset deletion at scale;
- storage bucket/folder deletion;
- Elasticsearch destructive lifecycle operations if recovery is uncertain;
- encryption-key retirement;
- identity/RBAC bulk changes;
- DNS/firewall/IAM modifications;
- production backup retention changes that may delete the last known-good recovery points.

---

## 11. Backup and restore governance

`docs/operations/DATABASE_BACKUP.md` remains authoritative for CAM database backup behavior.

Security governance adds these requirements:

- a backup file existing is not sufficient proof of recoverability;
- restore must be tested in a temporary/test PostgreSQL environment;
- production restore must never be improvised directly against live production;
- restore validation must include schema/migration state and representative application data;
- after restore, CAM must be able to start and resolve authoritative asset/source relationships;
- rebuildable search/index state should be rebuilt from authoritative data rather than treated as backup authority;
- backup credentials must remain separate from tenant/source OAuth credentials;
- restore exercises must not expose production secrets in logs or test artifacts.

Recommended recovery drill:

```text
select known-good backup
  -> download
  -> checksum/metadata verification
  -> pg_restore --list
  -> isolated PostgreSQL restore
  -> schema and data validation
  -> start compatible CAM build
  -> validate representative tenant/source/asset relationships
  -> rebuild search/index if required
  -> record measured recovery result and issues
```

---

## 12. Observability and audit

Security-relevant operations should be traceable without logging secrets.

Where applicable include stable correlation identifiers such as:

```text
request_id
user_id / actor_id
active_tenant_id
asset_id
external_source_id
job_id
ingestion_id
provider name
operation type
result/error class
build/commit identifier
```

Do not log access tokens, refresh tokens, encryption keys, signed URL query strings, database passwords, raw authorization headers, or full secret-bearing environment dumps.

Audit events should be append-oriented and include actor, target, action, result, and reason where relevant.

Alerts should prioritize conditions such as repeated authorization failures, abnormal enumeration, excessive retry storms, worker failure loops, backup failures, authentication anomalies, provider credential failures, and unexpected destructive operations.

---

## 13. Incident and rollback policy

When a security or severe production regression is suspected:

1. Stop the damaging path first without deleting evidence.
2. Revoke or rotate exposed credentials if compromise is plausible.
3. Preserve relevant logs and audit records.
4. Identify the first bad build/commit and affected data window.
5. Prefer reversible application rollback before ad-hoc production editing.
6. If data changed, determine whether forward repair, database restore, or targeted reconciliation is safer.
7. Rebuild derived indexes from authoritative sources when appropriate.
8. Add a regression/invariant test before considering the incident fully closed.
9. Document root cause, blast radius, detection gap, recovery, and preventive control.

Never conceal a production incident by deleting logs or force-rewriting Git history.

---

## 14. External cybersecurity skill/library policy

External security playbooks may be used as reference material, including community skill libraries such as `mukul975/Anthropic-Cybersecurity-Skills`, OWASP guidance, NIST guidance, and MITRE mappings.

CAM must not blindly vendor or activate an entire offensive/dual-use skill catalogue for its coding agent.

Preferred use:

```text
external playbook
      -> review applicability
      -> adapt to CAM architecture
      -> encode as CAM documentation/test/CI control
      -> independently review before production enforcement
```

Recommended defensive topics for CAM include:

- threat modeling;
- GitHub Actions hardening;
- SAST;
- secret scanning;
- dependency/container/IaC scanning;
- API authorization/BOLA testing;
- secure SDLC and supply-chain review.

Offensive capabilities such as credential theft, persistence, lateral movement, phishing, exploit execution, command-and-control, or privilege escalation are not normal development dependencies for CAM and must not be granted to a production-capable coding agent merely because they exist in an external security library.

Do not execute third-party security scripts against production without source review, clear scope, and explicit authorization.

---

## 15. Dependency governance

AI agents must not add dependencies casually.

For every new production dependency consider:

- why existing dependencies cannot provide the capability;
- maintenance/release activity;
- license compatibility;
- transitive dependency footprint;
- native/binary execution implications;
- network behavior;
- access to filesystem/environment/secrets;
- known vulnerability history;
- rollback/removal cost.

Security tooling belongs in development/CI scope where possible and must not become an unnecessary runtime dependency.

---

## 16. Security review checklist

Before merging a security-sensitive change answer all applicable questions:

### Architecture

- Does this preserve PostgreSQL/source-of-truth rules?
- Does it introduce a new source of truth?
- Does it create a new trust boundary or public endpoint?
- Can derived-data cleanup damage authoritative source assets?

### Authentication and authorization

- Is the route authenticated?
- Is tenant scope enforced server-side?
- Is object-level authorization enforced?
- Are negative authorization cases tested?
- Does any frontend check accidentally become the real security boundary?

### Input and data

- Is external input bounded and validated?
- Could this create path traversal, SSRF, unsafe deserialization, command injection, or oversized resource consumption?
- Could sensitive values enter logs/search/errors?

### Workers and retries

- Is the operation idempotent?
- What happens after timeout/crash/duplicate delivery?
- Can retry duplicate money, files, assets, metadata, or provider operations?

### Database

- Is a migration required?
- Is rollback understood?
- Are ownership/tenant constraints preserved?
- Is destructive behavior explicit?

### Dependencies and CI

- Is a new dependency necessary?
- Do existing tests cover failure paths?
- Are security invariant tests needed?
- Does CI still run independently of the implementing agent?

### Production

- What production permission is required?
- What is the blast radius?
- How is the change rolled back?
- Is backup/restore relevant?
- How will failure be detected?

A high-risk change is not ready merely because happy-path tests pass.

---

## 17. Hardening roadmap

### P0 - permission and governance controls

Target outcome: an AI coding mistake cannot directly become an unrestricted production action.

- Enforce this document through `AGENTS.md`.
- Keep production database write, unrestricted SSH/root, secret modification, IAM/DNS/firewall, and destructive storage permissions outside default AI access.
- Require explicit approval for high-risk production changes.
- Keep CI independent from the agent's assertion that its work is correct.

### P1 - invariant and supply-chain controls

Target outcome: high-impact regressions become difficult to merge.

- Add/expand tenant/object/search isolation tests.
- Add job retry/idempotency tests.
- Add source-vs-derived-data deletion tests.
- Add secret scanning.
- Add SAST tuned to CAM languages.
- Add dependency/container scanning with practical severity gates.
- Harden GitHub Actions and protect workflow changes.

### P2 - recovery and detection controls

Target outcome: serious failures are detectable and recoverable.

- Perform and document database restore drills.
- Confirm search/index rebuild procedures.
- Improve structured security/audit logging.
- Add alerts for authentication/authorization anomalies, repeated worker failures, backup failures, and destructive operations.
- Periodically review production credentials and least-privilege boundaries.

---

## 18. Definition of done for security hardening

CAM can consider this baseline operationally established when:

```text
[ ] security governance is referenced by AGENTS.md
[ ] default AI access excludes unrestricted production write/destructive authority
[ ] protected code changes run independent CI
[ ] tenant/object/search negative authorization tests exist
[ ] worker retry/idempotency invariants are tested
[ ] source-vs-derived deletion boundary is tested
[ ] secret scanning is active
[ ] SAST is active and tuned
[ ] dependency/container scanning is active with practical gates
[ ] workflow/deployment secrets are least-privilege
[ ] production deployment has an explicit approval boundary for high-risk changes
[ ] database backups are verified
[ ] at least one isolated restore drill has succeeded and is documented
[ ] security-relevant operations are auditable without leaking secrets
[ ] rollback procedures are documented for high-risk changes
```

---

## 19. Maintenance

Review this policy when any of the following materially change:

- authentication/authorization architecture;
- tenant model;
- storage/source provider model;
- search/index architecture;
- worker/job architecture;
- deployment topology;
- production access model;
- backup/restore strategy;
- AI coding-agent capabilities;
- major new public ingestion/API surface.

Security documentation should describe the system that actually exists. When code intentionally changes a documented security boundary, update the corresponding document in the same change.
