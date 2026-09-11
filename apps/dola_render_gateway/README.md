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
