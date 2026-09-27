# Realistic Review UGC Browser Scout

This companion runtime runs on a user-controlled desktop or laptop with Chrome or Chromium. Pinterest profile data and cookies remain local.

## Setup

1. Create a Python virtual environment.
2. Install `apps/rrugc_scout/requirements.txt`.
3. Install the Playwright Chromium runtime, or pass `--chrome-executable` to use a local Chrome/Chromium binary.
4. Create a campaign in **Realistic Review UGC** and copy the one-time Scout command.

The first time, sign in to Pinterest manually using the persistent profile directory. The Scout does not automate login, solve CAPTCHA, hide automation, bypass source controls, or extract credentials.

## Runtime flow

The Scout only discovers visible Pinterest candidates. Analysis and storage happen asynchronously in Creative Asset Manager:

```text
Pinterest search
  ↓
bounded Scout scroll
  ↓
candidate URL/metadata submission
  ↓
rrugc_candidate_analyze worker
  ↓
Gemini visual assessment + deterministic threshold policy
  ├── rejected_* → retained with reason/metrics
  └── approved
        ↓
      rrugc_candidate_import (when auto-import is enabled)
        ↓
      Managed Google Drive
```

Default behavior:
- opens a normal Pinterest search URL;
- performs only the campaign's bounded number of scroll batches;
- extracts visible `/pin/` links and `*.pinimg.com` images;
- sends candidate metadata to CAM in bounded batches;
- polls campaign counters so target progress is based on **approved** references, or **Drive-ready** references when auto-import is enabled;
- never downloads Pinterest images directly to the Scout filesystem for persistence.

The CAM worker layer applies the configured reference filters (head ratio, visible head, headwear, occlusion, smile, quality, UGC style, product fit, and AI-risk signal). Only approved references are eligible for Drive import.

The visual analyzer must be configured on the CAM server. The current v1 analyzer uses the existing tenant-aware Gemini metadata provider; deterministic local CV can replace individual measurements later without changing the Scout protocol.
