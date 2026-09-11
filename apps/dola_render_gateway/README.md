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

## DG-02 internal API (source-only)

The CAM-owned wrapper exposes authenticated internal endpoints only: POST /internal/v1/video-generations, GET /internal/v1/video-generations/{generation_id}, and GET /internal/v1/video-generations/{generation_id}/content.

Every endpoint requires Authorization: Bearer <DOLA_INTERNAL_API_KEY>. Submit additionally requires a non-empty Idempotency-Key (maximum 200 printable characters) and multipart/form-data: a JSON metadata part with prompt, model, aspect_ratio, and duration_seconds, plus zero or more ordered references image files. Supported models are seedance-2.0 and seedance-2.5; ratios are 16:9, 9:16, 1:1, 4:3, and 3:4; durations are 10, 15, or 30 seconds.

References are private uploads only: JPEG, PNG, or WEBP; at most 8 files, 15 MiB each and 30 MiB aggregate. No external reference URL is accepted. Files are staged only under the configured runtime root.

Fingerprint version v1 is SHA-256 over canonical JSON of normalized model, prompt, ratio, duration, and ordered SHA-256 reference bytes. Filenames, local paths, multipart framing, bearer values, and timestamps are excluded. Same idempotency key plus same fingerprint returns the existing generation without another submission; same key with a different fingerprint returns 409 idempotency_key_conflict. The durable mapping is /var/lib/dola-render-gateway/db/gateway.db.

Completed output remains Dola-local and is available only through the authenticated content endpoint; CAM Managed Storage integration is NOT IMPLEMENTED. CAM integration and production deployment are NOT PERFORMED. Because upstream has no external idempotency primitive, a process crash after upstream acceptance but before durable task-ID persistence has a residual external duplicate-risk window; the gateway never blindly resubmits an accepted record after restart.

## DG-02A uncertain submission safety

Before crossing the upstream submit boundary the gateway durably records submission_attempted_at and exposes state submission_unknown. This means the gateway cannot prove whether the upstream provider accepted the generation because an external submission attempt may have occurred before the upstream task identity was safely stored. Same key and fingerprint returns this same generation with idempotent replay; it is never automatically submitted again. Status remains uncertain and content is unavailable until explicit future recovery. Future DG-05 may associate a verified upstream task, mark it failed after external verification, or perform a controlled retry only after an operator proves no upstream job exists. The deliberate policy is NO DUPLICATE SUBMISSION over AUTOMATIC RETRY.
