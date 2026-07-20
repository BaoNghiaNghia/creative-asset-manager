# CI and integration test runbook

## Step 31 audit

Before Step 31 the repository had two CI jobs:

- `client` used Node 22, cached npm downloads from `package.json`, ran
  `npm install`, and built the Vite application.
- `api` used Python 3.12, installed API requirements without a pip cache,
  checked one shell script, compiled the API, ran the unittest suite, imported
  the FastAPI app, and ran two inline OAuth mapper checks.

The old workflow omitted the frontend type check and component tests, worker
startup validation, PostgreSQL migrations and locking behavior, Elasticsearch
v2, and the durable pipeline. It started no services and uploaded no failure
artifacts. OAuth checks used synthetic environment values and no production
secrets, but were inline duplicates of repository tests. A typical run was
expected to take about two minutes.

## Current CI jobs

All jobs run on `ubuntu-latest`, use Node 22 or Python 3.12, and run in
parallel. No job calls Google Drive, SharePoint, Gemini, or another production
provider.

| Job | Coverage | Services | Expected time |
| --- | --- | --- | --- |
| Frontend checks | locked install, TypeScript, Vitest, Vite build | none | 1-3 min |
| API, worker and provider unit tests | syntax/import, API, worker and mock adapters | none | 1-3 min |
| PostgreSQL migrations and repositories | empty upgrade, one head, downgrade/re-upgrade, constraints, isolation, claims and leases | PostgreSQL 16.4 | 2-4 min |
| Elasticsearch v2 integration | strict mapping, bulk upsert, query fixtures, aliases and cleanup | Elasticsearch 8.15.3 | 2-4 min |
| Durable pipeline end-to-end | authenticated ingestion through real worker runtime and persisted searchable asset, plus bounded failure paths | PostgreSQL 16.4 and Elasticsearch 8.15.3 | 3-7 min |

The expected pull-request wall time is about 5-8 minutes because jobs run in
parallel. Every job and long-running test command has a bounded timeout.

Python has no configured formatter, linter, or static type checker in the
current repository. CI therefore performs `compileall` and the complete
unittest suite rather than pretending those tools ran. The frontend likewise
has no ESLint configuration; `npm run typecheck` is its configured static
validation. Coverage collection is not configured, so no misleading coverage
artifact is generated.

## Services and lifecycle

CI pins PostgreSQL to `postgres:16.4` and Elasticsearch to
`8.15.3`. Both have explicit health checks. Databases and Elasticsearch data
are ephemeral and never cached. Dependency caches include only npm's download
cache and pip's download cache; build output, OAuth state, tokens, databases,
and index data are excluded.

Failure artifacts are retained for seven days and contain test/migration logs
and safe Elasticsearch cluster, index, and alias diagnostics. They must not be
extended with environment dumps, request bodies, OAuth tokens, signed URLs, or
raw sensitive metadata.

## Local equivalent

Requirements:

- Docker with the Compose plugin
- the API virtual environment with `apps/api/requirements.txt` installed

Run the same real-service integration modules with:

```bash
make integration-test
```

The runner uses random loopback host ports, waits up to 150 seconds for service
health, applies every Alembic migration, verifies a single head, downgrades to
Step 30's prior revision, upgrades again, and then runs PostgreSQL,
Elasticsearch, and pipeline E2E tests. Containers, volumes, and indices are
removed on exit. On failure, sanitized logs remain under
`artifacts/integration/`.

To select a Python interpreter explicitly:

```bash
PYTHON_BIN=/path/to/python make integration-test
```

The E2E source/downloader, managed storage, and Gemini adapters are in-process
fakes. PostgreSQL, Elasticsearch, worker claim/lease logic, repositories,
pipeline state transitions, hashes, metadata analyses, projections, and search
queries are real.

## Troubleshooting

- PostgreSQL startup failures: inspect `artifacts/integration/services.log`.
- Migration failures: inspect `migration-upgrade.log` and
  `migration-downgrade.log`.
- Elasticsearch failures: confirm at least 1 GB of Docker memory is available.
- Port conflicts should not occur locally because Compose publishes random
  loopback ports; CI service ports are isolated per GitHub-hosted runner.
