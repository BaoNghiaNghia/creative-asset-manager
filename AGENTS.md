# Repository instructions

These instructions apply to all AI agents and automated coding tools working in this repository.

## Read before changing the system

Before architecture or security-sensitive work, read:

- [docs/security/SECURITY_GOVERNANCE.md](docs/security/SECURITY_GOVERNANCE.md)
- [docs/architecture/AGENT.md](docs/architecture/AGENT.md)
- [docs/architecture/SECURITY.md](docs/architecture/SECURITY.md)

Then read the implementation-specific architecture document for the area being changed, such as `DATA_MODEL.md`, `PIPELINE.md`, `SEARCH.md`, provider documentation, or deployment/operations documentation.

The security governance document is the repository-wide security baseline. Do not weaken its invariants merely to make an implementation or test pass.

## Global architecture rules

Preserve these repository contracts unless the current user explicitly authorizes a reviewed architectural change:

- PostgreSQL is authoritative for assets, source relationships, metadata, durable processing state, authorization state, and search projection inputs.
- Elasticsearch and other search/embedding/projection layers are derived and rebuildable.
- Source asset deletion and derived-data deletion are separate concerns.
- Tenant/source/object authorization is enforced server-side and repository queries retain explicit tenant predicates.
- Externally visible processing is idempotent and retry-safe.
- Provider SDK/HTTP details remain behind provider/infrastructure adapters.
- Secrets never belong in source control, normal logs, search projections, client bundles, durable error messages, or test fixtures committed to Git.

## AI-agent permission boundary

AI agents may normally inspect and modify repository code, create tests and documentation, run local/dev checks, use local containers, and inspect authorized non-secret logs.

AI agents are not authorized by repository instructions alone to:

- SSH to production as `root` or with equivalent unrestricted authority;
- write directly to the production database;
- execute destructive production migrations;
- modify production secrets, IAM, DNS, firewall, or network policy;
- delete production source assets or storage at scale;
- deploy directly to production;
- disable or weaken CI/security controls in order to make a change pass.

Any temporary production elevation must come from an explicit current user instruction for that operation and must use the minimum required permission.

## Security-sensitive changes

Treat authentication, OAuth/session handling, RBAC/tenant scope, database migrations, external URL/file ingestion, encryption/secrets, backup/restore, asset deletion, production deployment, IAM/networking, and global emergency controls as high-risk.

For high-risk work:

1. Inspect current implementation and tests before editing.
2. Identify the security boundary and invariants affected.
3. Add negative/failure-path tests, not only happy-path tests.
4. Run the relevant unit/integration/security checks.
5. Document migration, production permission, known risk, and rollback impact.
6. Do not execute production-destructive steps unless explicitly authorized.

## Completion report

For security-sensitive changes report:

- change summary;
- files changed;
- behavior changed;
- security boundary/invariants affected;
- migration required or not;
- new dependency required or not;
- secrets/credential impact;
- tests actually run and results;
- production permissions required;
- known risks;
- rollback procedure.

## Public Review Portal work

Before planning, implementing, modifying, reviewing, or deploying the Public Review Portal or any public-review share/session/annotation behavior, read all of:

- [docs/architecture/PUBLIC_REVIEW.md](docs/architecture/PUBLIC_REVIEW.md)
- [docs/plans/PUBLIC_REVIEW_IMPLEMENTATION.md](docs/plans/PUBLIC_REVIEW_IMPLEMENTATION.md)
- [docs/plans/PUBLIC_REVIEW_CODEX_RUNBOOK.md](docs/plans/PUBLIC_REVIEW_CODEX_RUNBOOK.md)

These files are the implementation source of truth for Public Review unless the current user explicitly authorizes a reviewed architecture change. Work on exactly one documented phase at a time. Do not silently expand phase scope or replace the documented public-link/session, folder-scope authorization, asset/source identity, media-proxy, guest identity, annotation, or rich-text security contracts.

Public Review is security-sensitive because it introduces an internet-facing authorization boundary. Every implementation phase requires the negative tests, independent review pass, and completion report defined in the Public Review runbook. Completing an implementation phase never authorizes production deployment.

## Database backup work

Before planning, implementing, modifying, reviewing, or deploying database backup functionality, read:

- [docs/operations/DATABASE_BACKUP.md](docs/operations/DATABASE_BACKUP.md)
- [docs/plans/DATABASE_BACKUP_IMPLEMENTATION.md](docs/plans/DATABASE_BACKUP_IMPLEMENTATION.md)

Those documents are the repository source of truth for database-backup requirements unless the current user explicitly overrides them. Their existence does not authorize production backup actions.

When implementing backup work, start from current `main` on a feature branch unless the current user explicitly asks for another workflow, inspect the actual current code before editing, and reuse managed Google Drive credential/storage infrastructure where practical. Never use tenant or user Source Drive OAuth credentials for production database backups. Do not add migrations or frontend work unless a later explicit requirement needs them. Run the checks in the implementation plan and update the documentation if an intentional architectural decision changes.

Direct current user instructions override these repository instructions, but do not silently infer authorization for destructive production actions from a general coding request.
