# Realistic Review UGC Pinterest Auto Scout

This companion runtime runs on a user-controlled desktop or laptop with Chrome/Chromium. Pinterest cookies and profile data stay on that machine.

## One-click Windows launcher

For the Windows checkout (for example `D:\\Bot_Tool_Auto_Game\\scan_pinterest`), use `START_SCOUT_REVIEW.cmd` for the existing review/reference scout and `START_SCOUT_KEYWORD.cmd` for Stage 0 keyword analysis. The legacy repository-root `START_SCOUT.bat` is intentionally preserved and still launches the same review scout.

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

After this one-time configuration, the normal Review Scout workflow is:

```text
START_SCOUT_REVIEW.cmd
```

`START_SCOUT.bat` remains a supported legacy entry point with its original behavior.

Do not commit `scout.local.env`; it is intentionally ignored by Git.

## Stage 0 quote-scout keyword volume

A second terminal can reuse the same Scout Agent credentials without competing for the normal Pinterest reference claim lane. After the quote scout discovers keywords, submit them through Creative Asset Manager instead of calling AEBrowse directly. Stage 0 is an independent keyword table: CAM calls the AEBrowse Google Ads endpoint server-side, stores each normalized keyword once per tenant, and reuses fresh results for 24 hours.

On Windows, use the repository-root launcher:

```text
START_SCOUT_KEYWORD.cmd
```

Use `START_SCOUT_REVIEW.cmd` for the existing Pinterest review/reference scout and `START_SCOUT_KEYWORD.cmd` for Stage 0 keyword analysis. `START_SCOUT.bat` remains a backward-compatible Review Scout entry point. The two CMD windows are designed to run together: they serialize only shared startup work (Git update, pairing/config, venv and dependency setup), then release that lock and run independently. Review and Keyword each own a separate Chrome profile, browser process, history and local log. A mode-specific runner mutex allows exactly one Review + one Keyword at the same time while blocking accidental duplicate Review+Review or Keyword+Keyword starts. Startup also rejects profile paths that are identical or nested, preventing one Scout from touching the other Scout's Chrome lock/runtime files.

Keyword Scout is autonomous. It searches Pinterest for the fixed seed query `Saying Trucker hat`, opens image Pins through the same high-quality Pin-detail resolver used by Review Scout, and sends each resolved image to Creative Asset Manager vision analysis. CAM transcribes only text visibly printed/embroidered on target caps, ignoring Pinterest captions, watermarks and background text. Text is grouped by physical cap: when one cap has multiple lines, all readable lines are kept in natural reading order and concatenated into one Stage 0 keyword; multiple caps still produce separate keywords. New quotes are deduplicated locally and sent immediately to CAM's Stage 0 keyword-volume endpoint. CAM upserts and commits each discovered quote to the database before calling AEBrowse/Google Ads, so provider failures never lose a scanned quote; volume data is filled in on the same request or a later retry.

Keyword Scout has its own Chrome profile (`RRUGC_KEYWORD_PROFILE_DIR`, default `<repo>\pinterest-profile-keyword`) and its own durable `keyword-scout-history.json`. This prevents Chrome profile locks and Pin/history collisions when Review Scout and Keyword Scout run at the same time. On the first Keyword Scout run, sign into Pinterest in the separate Chrome window if prompted; the terminal waits and resumes automatically. It never claims Stage 1 jobs. The launcher runs continuously until its terminal is closed; empty-result cycles self-reload the Pinterest search and retry quickly instead of stopping.

Keyword Scout also keeps a dedicated rotating JSONL debug log at `<keyword-profile>\logs\keyword-scout.jsonl`. It records startup/cycles, Pinterest empty-search diagnostics and reloads, every processed Pin, vision extraction result/error, quote dedupe decisions, AEBrowse volume success/defer, retry/recovery events, and batch summaries. The same structured events are uploaded asynchronously in batches to Creative Asset Manager using the existing Scout agent token and stored centrally in `application_logs` for 5 days; the retention worker deletes them after `expires_at`. The local JSONL rotates daily with a 10-day local retention. Starting with Scout v39, both modes also persist outgoing API log events into a profile-local SQLite spool before transmission; an HTTP outage, full in-memory wake queue, or Scout restart does not discard unsent events. Successful API acknowledgment removes them using a stable event ID, and stale unsent entries expire after 10 days. The API retains ingested events for 5 days. Neither log payload nor the spool stores Scout credentials.

Scout v39 also warns when Review completes a scan without newly created candidates, and automatically reduces Keyword polling after three consecutive quote-less cycles instead of hammering the same exhausted Pinterest results. AEBrowse partial responses are retried: a missing keyword-volume row is not treated as a real search-volume value of zero.

Direct Python usage remains available:

```powershell
python apps\rrugc_scout\quote_keyword_volume.py `
  --keyword "matching couple hoodies" `
  --keyword "embroidered anniversary hoodie" `
  --keyword "custom initial hoodie"
```

The helper reads `RRUGC_AGENT_ID` and `RRUGC_SCOUT_TOKEN` from the environment. It accepts up to 50 unique keywords per request. Use `--keywords-file keywords.txt` for one keyword per line, `--json` for raw JSON output, and `--force` only when a fresh provider lookup is required. Stage 0 polls the stored results and displays monthly search volume, competition, CPC range, and last-check time automatically.

## Debug logs

Every Scout start now creates a structured JSONL debug log inside its own persistent Pinterest profile. Review Scout and Keyword Scout also upload structured events independently to Creative Asset Manager using the same agent credentials; server-side logs are tagged by `scout_type` and retained for 5 days, so concurrent runs remain distinguishable:

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

Complete Pinterest sign-in manually in that normal Chrome window. For dedicated Scout profiles, prefer Pinterest email/password rather than **Continue with Google** because Google can reject OAuth with “This browser or app may not be secure”. Current Scout launchers also perform this check automatically: before any campaign/search is claimed, they open Pinterest with the persistent profile and detect login, CAPTCHA/challenge, and the **Verifying browser...** interstitial. If access is not ready, automated scouting stays paused, normal Chrome is opened for manual verification/login, and scouting starts only after you close that window and the session verifies successfully.

Auto Scout now prefers an installed Google Chrome automatically when available. You can still pin a specific binary with:

```powershell
--chrome-executable "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
```

The pairing is machine-level, not campaign-level. Once the Agent is online, every due source-image plan with **Auto Scout** enabled can be claimed automatically. Auto Scout v29 is source-plan aware, context-first, embroidery-text aware, and uses a 50-reference target. AI-derived direct scene queries lead, exact visible embroidery wording is searched through a dedicated text-match cluster, then adjacent lifestyle scene queries follow ahead of adaptive/legacy campaign queries. Pin history is persistent and Scout diagnostics remain available. The Scout remains quality-first and low-footprint: it enriches searches toward candid lifestyle photography, filters obvious AI/render/illustration metadata before submission, sends small batches, and stops adding candidates once that source image already has enough viable work in the analysis/import pipeline.

Search results are discovery-only. Before a bounded batch is submitted, the Scout resolves each discovered `pin_url` through the Pinterest Pin detail page and selects the best matching `pinimg.com` asset from Open Graph metadata, JSON-LD, close-up images, and `srcset`. It matches the rendition-independent asset path so a high-resolution related Pin cannot replace the discovered Pin. When a reference is later approved by RRUGC analysis (or explicitly accepted by a reviewer), that Pin may become a related-discovery seed: on a later run the Scout opens the approved Pin detail page, reads the first 60 related Pins, filters history/obvious synthetic metadata, resolves each retained Pin to its best rendition, and submits the candidates through the same RRUGC analysis pipeline. At most one approved seed is expanded per run, and expansion history is campaign-scoped so embroidery groups shared across product colors do not repeatedly scan the same seed.

The resolver keeps exactly one reusable Pin-detail tab beside the search tab and resolves Pins sequentially. It pauses between detail navigations, stops the current run immediately when Pinterest presents a login/challenge gate, and applies a five-minute cooldown when HTTP 429 rate limiting is detected. The legacy `--detail-concurrency` option is retained for command compatibility but production mode always forces a single detail tab.

Auto Scout v29 defaults to `--pace careful`. The careful pace is tuned to stay low-footprint while moving about 15–20% faster than the previous profile: 3.8–5.8 seconds of dwell after opening a keyword, 1.2–2.2 seconds before each visible-result inspection, gradual 420–700 px scroll steps with 0.55–0.95 second pauses, batches of 3 candidates, and 3–5 second pauses between keywords. Use `--pace balanced` only when a materially shorter scan is preferred.

The Scout does **not** automate Pinterest login, solve CAPTCHA/challenges, hide automation, bypass source controls, or extract credentials. Startup is now gated on a verified Pinterest session: no campaign/search work begins while login, CAPTCHA/challenge, or browser-verification UI is present. Manual resolution happens in normal Chrome using the same persistent profile; after it is closed, the Scout rechecks Pinterest and starts automatically only when access is ready. Mid-run gates still pause the affected work rather than bypassing Pinterest controls.

Unexpected runtime failures are supervised separately from expected Pinterest login/rate-limit gates and ordinary CAM network retries. After 5 consecutive unexpected runtime errors, the affected Review or Keyword Scout performs a full Scout-runtime restart. Up to 3 automatic restarts are allowed without a successful run/cycle in between, using a short increasing backoff. If the restarted Scout still cannot make healthy progress, it stops with exit code 70, writes a fatal local/remote Scout log event, and reports the Agent as error with review_scout_restart_limit_exceeded or keyword_scout_restart_limit_exceeded so the system can surface the failure instead of looping forever.

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
