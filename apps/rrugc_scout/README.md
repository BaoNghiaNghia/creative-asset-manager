# Realistic Review UGC Pinterest Auto Scout

This companion runtime runs on a user-controlled desktop or laptop with Chrome/Chromium. Pinterest cookies and profile data stay on that machine.

## Auto Scout v3 quality-first setup

1. Create a Python virtual environment and install `apps/rrugc_scout/requirements.txt`.
2. Install Playwright Chromium, or pass `--chrome-executable` for a local Chrome/Chromium binary.
3. In **Realistic Review UGC → Pinterest Auto Scout**, choose **Pair local Scout**.
4. Copy the one-time Agent command to the browser machine and keep that process running.
5. Sign in to Pinterest manually in the persistent browser profile the first time.

### Google sign-in says “This browser or app may not be secure”

Google can refuse OAuth sign-in from a browser that is being controlled by Playwright. Do not try to bypass that check. Bootstrap the persistent Scout profile once in a normal local Chrome window instead:

```powershell
python apps\rrugc_scout\scout.py --profile-dir "D:\\Bot_Tool_Auto_Game\\scan_pinterest\\pinterest-profile" --bootstrap-login
```

Complete Pinterest sign-in manually in that normal Chrome window. If you use **Continue with Google**, do it there. After Pinterest is fully signed in, close the bootstrap Chrome window, then start Auto Scout with the same `--profile-dir`.

Auto Scout now prefers an installed Google Chrome automatically when available. You can still pin a specific binary with:

```powershell
--chrome-executable "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
```

The pairing is machine-level, not campaign-level. Once the Agent is online, every running campaign with **Auto Scout** enabled can be claimed automatically when its next scan is due. Auto Scout v3 is quality-first: it enriches generic searches toward candid lifestyle photography, filters obvious AI/render/illustration metadata before submission, sends small batches, and stops adding candidates once the server already has enough viable work in the analysis/import pipeline.

The Scout does **not** automate Pinterest login, solve CAPTCHA/challenges, hide automation, bypass source controls, or extract credentials. If Pinterest shows a login/challenge screen, the browser stays open for manual resolution and the Agent resumes automatically when access is restored.

## Automatic flow

```text
paired persistent Scout Agent
  ↓
claim next due running campaign
  ↓
Pinterest search in local authenticated Chrome profile
  ↓
bounded scroll + visible Pin extraction
  ↓
candidate submission in bounded batches
  ↓
rrugc_candidate_analyze
  ↓
deterministic reference policy
  ├── rejected_* → reason/metrics retained
  └── approved
        ↓
      rrugc_candidate_import (when Auto Import is enabled)
        ↓
      Managed Google Drive
  ↓
schedule next scan until campaign target is reached
```

Server-side campaign leases prevent two Scout Agents from running the same campaign simultaneously. The Agent heartbeats extend the lease while a scan is active. A stale lease expires automatically and another Agent can recover the campaign.

Auto Scout scans are bounded by each campaign's `max_scroll_batches`. After a successful scan the next scan uses the configured interval. Consecutive scans that find no new Pins back off progressively, capped at one hour. Login/challenge or runtime errors release the lease and schedule a later retry.

Pinterest Pin URLs are canonicalized and used as the stable discovery identity, so alternate Pin image CDN renditions do not create duplicate candidates. Content hashing during the existing import path remains the second deduplication layer.

## Legacy one-campaign mode

The previous `--campaign-id` mode remains available for diagnostics and backwards compatibility, but new production usage should pair one Auto Scout Agent with `--agent-id` and leave it running.
