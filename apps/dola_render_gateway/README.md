# CAM Dola Render Gateway

## Purpose

Vendored upstream Dola browser execution gateway for future CAM video generation.

## Current state

**SOURCE ONLY**
**NOT WIRED INTO CAM**
**NOT DEPLOYED**

- CAM API does not currently call Dola.
- CAM worker does not import Patchright.
- No VIDEO_GENERATE JobType exists yet.
- No Dola CAM feature flag exists yet.
- No systemd Dola service exists yet.
- No production Dola account or profile is created.
- DG-00 performs no deployment.

## Target architecture

    CAM API
        |
    video_generation_run
        |
    processing job
        |
    CAM Video Worker
        |
    localhost authenticated HTTP
        |
    Dola Render Gateway
        |
    Patchright / Chromium
        |
    dola.com
        |
    generated MP4
        |
    CAM Managed Storage
        |
    CAM Asset Registry

## Authority model

CAM PostgreSQL is the business authority.

Dola SQLite is an execution/recovery cache only.

## Isolation model

    ONE GIT REPO         YES
    ONE SOURCE TREE      YES

    ONE PROCESS          NO
    ONE VENV             NO
    ONE SYSTEM USER      NO
    ONE DATABASE         NO
    ONE FAILURE DOMAIN   NO

Later tasks harden and isolate the runtime. Do not run this source from the CAM main virtual environment.

## DG-01 supported runtime contract

This contract is source-only and remains NOT DEPLOYED, NOT CONNECTED TO CAM, and NOT PRODUCTION ENABLED.

- Host: 127.0.0.1 by default
- Port: 8100 by default
- State root: /var/lib/dola-render-gateway
- Runtime root: /run/dola-render-gateway
- Required secret: DOLA_INTERNAL_API_KEY

The CAM-owned wrapper is cam_runtime/. It rejects a missing, empty, whitespace-only, or obvious placeholder internal key. Non-loopback hosts are rejected unless DOLA_ALLOW_NON_LOOPBACK=true is explicitly set. All routes except GET /health/live require Authorization: Bearer DOLA_INTERNAL_API_KEY. The upstream web/admin UI is mounted behind that same bearer boundary.

### State layout

    /var/lib/dola-render-gateway/
      db/tasks.db
      db/pool_usage.db
      accounts/
      profiles/<account-id>/
      downloads/
      artifacts/
    /run/dola-render-gateway/tmp/

No persistent state is written to the repository, the source checkout, or the current working directory. Browser profiles live under profiles/; SQLite remains execution/recovery state only.

The supported provisioning path must not put passwords, TOTP secrets, cookies, or session tokens on argv. The legacy upstream CLI was patched to accept only an account name on argv and prompt for secrets; future protected tooling may use stdin.

DG-02 will implement the internal generation API and idempotency contract.
