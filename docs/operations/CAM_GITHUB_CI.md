# CAM GitHub CI — reproducible builds and bounded tests

This document describes the main/PR CI workflow only. It never deploys production.

## What is enforced

- Ubuntu **24.04** instead of a moving ubuntu-latest image.
- Node **22.23.3** consistently for UI, Worker and desktop jobs.
- Node-24-compatible GitHub Actions: checkout@v6, setup-node@v5,
  setup-python@v6, upload-artifact@v6. Permissions remain contents:read.
- Manual workflow_dispatch is allowed. Per-ref concurrency cancels obsolete CI
  runs; it does not cancel an unrelated branch or a production deploy.
- API unit discovery is preserved and divided across four deterministic shards
  by test ID. Duplicate discovered IDs are run once. Errors and failures remain
  fatal. The existing security/regression/deployment smoke tests run on shard 0.
- Browser QA profiles are partitioned across creative, operations, and media
  groups, all with strict fixture checks. Failure evidence is uploaded per group.
- GitHub branch-protection compatibility: aggregate gate jobs retain the
  historical names "API, worker and provider unit tests" and
  "Fixture-backed Browser QA (9 workspaces)". They fail unless ALL shards pass.
- PostgreSQL 12.22 and 16.4 each run a fresh full Alembic upgrade,
  then a **single-revision** rollback of 0162 on disposable cam_integration,
  then re-upgrade. Unrelated historical migrations are NOT destructively
  rolled back into the 0012 era.
- Frontend npm ci uses the checked-in package-lock.json. The committed dist
  must still match a fresh build byte-for-byte. Build metadata is pinned to
  its source commit and UTC timestamp.
- .gitattributes excludes fonts, video and other binary assets from Git's
  LF normalization, even when stored inside apps/client/dist.

## Local verification

From repository root:

    python3 -m compileall -q scripts/ci_api_unit_shard.py
    python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
    cd apps/api
    .venv/bin/python -m pytest tests/modules/branding_dna tests/migrations/test_branding_dna_handoff_migration.py -q
    cd ../client
    npm run typecheck
    npm test -- --run app/branding-dna/BrandingDnaPage.test.tsx

To reproduce the exact frontend npm dependency environment, use a **detached
scratch worktree**, not npm ci in the running VPS source checkout:

    git worktree add --detach .ci-frontend-verify HEAD
    cd .ci-frontend-verify/apps/client
    npm ci
    BUILD_COMMIT="$(node -p 'require("./dist/build-info.json").build_commit')" \
      BUILD_UTC_TIMESTAMP="$(node -p 'require("./dist/build-info.json").build_utc_timestamp')" \
      npm run build
    git status --short -- dist

The expected status is empty. Clean up only this isolated worktree via
git worktree remove after inspecting results.

## Diagnosing failures

Open the CI run in GitHub Actions and inspect the failing individual shard.
Artifacts are named api-unit-failure-shard-<number>,
browser-qa-evidence-<group>, frontend-failure,
postgres-integration-failure, etc. A failed matrix member is not a pass
even if its aggregate job finishes.

Never bypass failing CI by skipping migration checks, changing tests to
advisory, or suppressing a failing test's exit code. CI success alone does
not authorize production deployment. The separate backup restore, release,
migration, and authenticated Browser QA gates still apply.
