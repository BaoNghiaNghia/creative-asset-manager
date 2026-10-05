# Realistic Review UGC Pinterest Auto Scout

This companion runtime runs on a user-controlled desktop or laptop with Chrome/Chromium. Pinterest cookies and profile data stay on that machine.

## One-click Windows launcher

For the Windows checkout (for example `D:\\Bot_Tool_Auto_Game\\scan_pinterest`), use the repository-root `START_SCOUT.bat` instead of invoking `scout.py` manually.

On every start the launcher:

1. refuses to overwrite tracked local edits;
2. fetches `origin/main` and fast-forwards the checkout when a new release exists;
3. reloads the launcher immediately when the update changed launcher code;
4. creates/reuses `.venv-rrugc`;
5. installs `apps/rrugc_scout/requirements.txt` only when its SHA-256 changes;
6. preserves the Git-ignored `pinterest-profile` and `scout.local.env`;
7. loads the Scout token through `RRUGC_SCOUT_TOKEN` so it is not exposed in the Python process command line;
8. starts Auto Scout with the configured Agent ID.

The first launch creates `scout.local.env` and prompts once in the terminal for:

```text
RRUGC_AGENT_ID=<agent id from Realistic Review UGC>
RRUGC_SCOUT_TOKEN=<agent token>
```

The defaults already use `https://creative-assets.ddns.net`, `<repo>\\pinterest-profile`, `careful` pace, and low-footprint detail mode with one reusable detail tab.

After this one-time configuration, the normal workflow is only:

```text
START_SCOUT.bat
```

Do not commit `scout.local.env`; it is intentionally ignored by Git.

## Stage 0 quote-scout keyword volume

A second terminal can reuse the same Scout Agent credentials without competing for the normal Pinterest reference claim lane. After the quote scout discovers keywords, submit them through Creative Asset Manager instead of calling AEBrowse directly. Stage 0 is an independent keyword table: CAM calls the AEBrowse Google Ads endpoint server-side, stores each normalized keyword once per tenant, and reuses fresh results for 24 hours.

On Windows, use the repository-root launcher:

```text
START_KEYWORD_SCOUT.cmd
```

It auto-updates from `origin/main` with the same safety rules as `START_SCOUT.bat`, reuses `scout.local.env` and `.venv-rrugc`, and opens an interactive loop. Paste up to 50 keywords, one per line, then submit a blank line. The terminal can stay open beside the normal Pinterest Scout; it never claims Stage 1 jobs.

Direct Python usage remains available:

```powershell
python apps\rrugc_scout\quote_keyword_volume.py `
  --keyword "matching couple hoodies" `
  --keyword "embroidered anniversary hoodie" `
  --keyword "custom initial hoodie"
```

The helper reads `RRUGC_AGENT_ID` and `RRUGC_SCOUT_TOKEN` from the environment. It accepts up to 50 unique keywords per request. Use `--keywords-file keywords.txt` for one keyword per line, `--json` for raw JSON output, and `--force` only when a fresh provider lookup is required. Stage 0 polls the stored results and displays monthly search volume, competition, CPC range, and last-check time automatically.

## Debug logs

Every Scout start now creates a structured JSONL debug log inside the persistent
Pinterest profile:

```text
<pinterest-profile>\logs\pinterest-scout.jsonl
```

The file rotates daily and retains 10 days of history. It records the client
version and timestamp plus operational events such as CAM request latency/status,
claim/no-claim results, idle diagnostics, source-plan readiness, candidate
submission starts, run completion, connection timeouts, HTTP retries, and Scout
runtime retries. Authentication tokens and request bodies are not written.

Examples:

```json
{"ts":"...","event":"cam_request","operation":"claim","status_code":200,"duration_ms":184}
{"ts":"...","event":"idle_diagnostics","first_campaign_reason":"source_plan_not_ready","first_campaign_source_plan_statuses":{"queued":1}}
{"ts":"...","event":"cam_request_failed","operation":"diagnostics","error_type":"ReadTimeout","duration_ms":45008}
```

When reporting a Scout issue, include this JSONL file together with the terminal
output. The server also emits structured Scout claim events and warns when the
diagnostics endpoint itself takes at least one second.

## Auto Scout v14 text-aware source-plan setup

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

The pairing is machine-level, not campaign-level. Once the Agent is online, every due source-image plan with **Auto Scout** enabled can be claimed automatically. Auto Scout v19 is source-plan aware, context-first, embroidery-text aware, and uses a 50-reference target. AI-derived direct scene queries lead, exact visible embroidery wording is searched through a dedicated text-match cluster, then adjacent lifestyle scene queries follow ahead of adaptive/legacy campaign queries. Pin history is persistent and Scout diagnostics remain available. The Scout remains quality-first and low-footprint: it enriches searches toward candid lifestyle photography, filters obvious AI/render/illustration metadata before submission, sends small batches, and stops adding candidates once that source image already has enough viable work in the analysis/import pipeline.

Search results are discovery-only. Before a bounded batch is submitted, the Scout resolves each discovered `pin_url` through the Pinterest Pin detail page and selects the best matching `pinimg.com` asset from Open Graph metadata, JSON-LD, close-up images, and `srcset`. It matches the rendition-independent asset path so a high-resolution related Pin cannot replace the discovered Pin. When a reference is later approved by RRUGC analysis (or explicitly accepted by a reviewer), that Pin may become a related-discovery seed: on a later run the Scout opens the approved Pin detail page, reads the first 20 related Pins, filters history/obvious synthetic metadata, resolves each retained Pin to its best rendition, and submits the candidates through the same RRUGC analysis pipeline. At most one approved seed is expanded per run, and expansion history is campaign-scoped so embroidery groups shared across product colors do not repeatedly scan the same seed.

The resolver keeps exactly one reusable Pin-detail tab beside the search tab and resolves Pins sequentially. It pauses between detail navigations, stops the current run immediately when Pinterest presents a login/challenge gate, and applies a five-minute cooldown when HTTP 429 rate limiting is detected. The legacy `--detail-concurrency` option is retained for command compatibility but production mode always forces a single detail tab.

Auto Scout v19 defaults to `--pace careful`. The careful pace is tuned to stay low-footprint while moving about 15–20% faster than the previous profile: 3.8–5.8 seconds of dwell after opening a keyword, 1.2–2.2 seconds before each visible-result inspection, gradual 420–700 px scroll steps with 0.55–0.95 second pauses, batches of 3 candidates, and 3–5 second pauses between keywords. Use `--pace balanced` only when a materially shorter scan is preferred.

The Scout does **not** automate Pinterest login, solve CAPTCHA/challenges, hide automation, bypass source controls, or extract credentials. If Pinterest shows a login/challenge screen, the current run stops immediately so it can be resolved manually; a later scheduled scan resumes only after normal access is available again.

## Automatic flow

```text
paired persistent Scout Agent
  ↓
claim next due source-image plan
  ↓
source_plan_id + Drive image path + AI context/search queries
  ↓
Pinterest search in local authenticated Chrome profile
  ↓
bounded scroll + visible Pin discovery
  ↓
one reusable Pin-detail tab (sequential resolver)
  ↓
select best matching original/high-resolution Pin image
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
