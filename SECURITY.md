# Security Policy

Creative Asset Manager (CAM) treats tenant isolation, authorization, provider credentials, source assets, production operations, and recoverability as security-critical concerns.

The repository-wide security and AI-agent governance baseline is documented in:

- [`docs/security/SECURITY_GOVERNANCE.md`](docs/security/SECURITY_GOVERNANCE.md)
- [`docs/architecture/SECURITY.md`](docs/architecture/SECURITY.md)
- [`docs/architecture/AGENT.md`](docs/architecture/AGENT.md)
- [`AGENTS.md`](AGENTS.md)

Implementation-specific operational requirements, including database backup and restore expectations, remain documented under `docs/operations/` and `docs/plans/`.

## Reporting a vulnerability

Do not publish credentials, exploit details, tenant data, signed URLs, tokens, private logs, or other sensitive evidence in a public issue.

Use GitHub's private security-reporting / Security Advisory mechanism for this repository when available. If private reporting is not available, contact the repository owner through a private channel before disclosing sensitive technical details.

A useful report should include:

- affected component and version/commit if known;
- security boundary affected;
- minimal reproduction steps;
- expected versus observed behavior;
- likely impact and scope;
- whether credentials or tenant/source data may have been exposed;
- any safe mitigation already tested.

## Security-sensitive areas

Changes are considered high-risk when they affect authentication, OAuth/session handling, RBAC or tenant/object scope, external URL/file ingestion, encryption or secrets, source asset deletion, database migrations, backup/restore, production deployment, IAM/networking, or global emergency controls.

High-risk changes require negative/failure-path testing and an explicit rollback/recovery plan before production execution.

## Production and AI-agent boundary

Repository access does not by itself authorize an AI coding agent to perform production-destructive operations. By default, coding agents must not receive unrestricted production root access, production database write authority, secret-store administration, IAM/DNS/firewall administration, destructive storage authority, or unilateral production deployment authority.

The intended control chain is:

```text
implementation
  -> independent CI/tests/security checks
  -> human review for high-risk changes
  -> narrowly scoped deployment/operation
  -> production verification and rollback readiness
```

See [`docs/security/SECURITY_GOVERNANCE.md`](docs/security/SECURITY_GOVERNANCE.md) for the complete policy and security hardening roadmap.
