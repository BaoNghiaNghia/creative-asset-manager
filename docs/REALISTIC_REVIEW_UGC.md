# Realistic Review UGC — Workflow Architecture & Implementation Specification

> **Product surface:** top-level Creative Asset Manager tab
> **Route:** `/realistic-review-ugc`
> **Status:** Approved product concept; implementation may proceed incrementally
> **Primary first use case:** discover realistic lifestyle people references, generate product-on-person hat imagery, supervise geometry/product fidelity, export approved results to Google Drive, and report daily throughput
> **Architecture rule:** Workflow orchestration is independent from AI Operations. Existing Asset Explorer, Job Queue, Review Board, PostgreSQL, R2, Google Drive managed storage, visual encoder/SigLIP, auth, and worker conventions must be reused rather than duplicated.

---

## 1. Product objective

Realistic Review UGC is a dedicated workflow surface for producing realistic user-generated-content-style product imagery from externally discovered lifestyle references.

The initial end-to-end workflow is:

```text
Search / Discover reference images
        ↓
Fast candidate filtering
        ↓
Full context + suitability analysis
        ↓
Approved reference library
        ↓
Product Reference Registry
        ↓
Generation Worker Skill
        ↓
Supervisor Skill
        ↓
PASS ───────────────→ Final asset
 │
 └─ FAIL
      ↓
Correction feedback
      ↓
Generation retry
      ↓
Human review after bounded attempts
        ↓
R2 + Google Drive
        ↓
Daily reporting
```

The first product family is hats/caps. The architecture must remain product-agnostic so later product profiles can support apparel, totes, towels, shoes, accessories and other personalized products.

---

## 2. Navigation and ownership

Realistic Review UGC is a top-level workspace tab.

```text
Creative assets

Asset Explorer
AI Operations
Realistic Review UGC
Job Queue
Video Generation
Review Board
Access Management
```

It is not an AI Operations sub-tab.

Responsibilities are intentionally separated:

```text
Realistic Review UGC = workflow orchestration + domain-specific workflow UX
Job Queue            = individual task execution and retries
Asset Explorer       = canonical asset browsing/search
Review Board         = human review decisions
PostgreSQL           = authoritative workflow/business metadata
R2                   = primary binary storage
Google Drive         = delivery/collaboration mirror
```

---

## 3. Core entities

The workflow uses three orchestration levels.

### 3.1 Workflow Template

A versioned reusable pipeline definition.

Examples:

- Hat US Lifestyle References
- Hat Lifestyle Generation
- Hat Full Pipeline
- QA Only
- Drive Export Only

### 3.2 Workflow Run

One execution of a template with a frozen configuration snapshot.

Example:

```text
Template: Hat US Lifestyle
Run: RUN-20260927-001
Product: HAT-001
Target references: 300
Target generated: 300
```

Template edits must never mutate an already-running run.

### 3.3 Job / Task

Small execution units delegated to the existing job system.

Examples:

- `reference_browser_scan`
- `reference_thumbnail_analysis`
- `reference_full_analysis`
- `reference_download`
- `reference_drive_export`
- `product_generate`
- `supervisor_qa`
- `generated_drive_export`
- `workflow_daily_report`

---

## 4. Five-stage product pipeline

The UI and metrics use five stable stages:

| Stage | Name | Purpose |
|---|---|---|
| 1 | Source / Discovery | Search external sources and discover candidates |
| 2 | Filter / Analysis | Analyze candidate suitability and approve references |
| 3 | Generation | Generate product-on-person imagery |
| 4 | QA / Review | Supervisor validation, correction and human review |
| 5 | Export / Delivery | Persist, export to Drive and report |

Every stage exposes:

- total
- queued
- running
- retrying
- completed
- failed
- skipped
- average duration
- last activity time

---

## 5. Source / Discovery

### 5.1 Source adapter contract

Pinterest is the first browser source but must not be hard-coded into the orchestration layer.

```text
ReferenceSourceAdapter
├── pinterest_browser
├── uploaded_folder
├── google_drive
├── licensed_stock
├── internal_asset_library
└── future_source
```

### 5.2 Pinterest Browser Scout

The first browser workflow is user-authorized, browser-assisted Pinterest search.

Normal behavior:

```text
Search keyword
   ↓
Open Pinterest search in an authenticated persistent Chrome profile
   ↓
Scroll search results
   ↓
Extract visible Pin/card metadata
   ↓
Run cheap thumbnail filtering
   ↓
Open promising Pin
   ↓
Run full-image analysis
   ↓
Approve
   ↓
Download
   ↓
Dedupe
   ↓
R2 / Drive registration
```

The Scout must use normal browser interaction only. It must not implement CAPTCHA bypass, credential theft, anti-bot evasion or access to content the authenticated user cannot normally view. Product and legal teams remain responsible for ensuring use complies with source terms and content rights.

### 5.3 Browser Scout placement

Do not run a persistent GUI Chromium session on the production VPS.

Preferred topology:

```text
Creative Asset Manager VPS
           │
           │ authenticated agent API
           ▼
Browser Scout Machine
           │
           ├── Chrome / Chromium
           ├── persistent profile
           ├── Playwright / CDP
           └── optional agent/JEV fallback
```

The browser machine owns cookies/session state. CAM stores only an agent ID, status, capabilities and last heartbeat.

### 5.4 Browser decision strategy

Use deterministic Playwright/CDP first:

- open search URL
- type query
- submit
- scroll
- detect Pin cards
- open selected Pin
- navigate back
- collect visible metadata

Only use JEV/LLM browser reasoning when:

- a selector disappears
- Pinterest DOM changes
- a modal blocks navigation
- the next action cannot be resolved deterministically

This keeps browser cost and latency low.

---

## 6. Search Campaign

Discovery work is grouped under a Search Campaign.

Example:

```text
Campaign: Hat / US / Female / Casual
Target approved references: 300
```

Queries:

```text
happy woman casual photo
woman smiling outdoor candid
woman laughing smartphone photo
female friends casual selfie
woman cafe candid photo
```

Each query records:

- scanned
- thumbnail passed
- opened
- approved
- rejected
- duplicate
- download failed
- average final score
- approval conversion rate

Low-yield queries can be deprioritized; strong queries can receive more scanning budget.

Campaign completion can be target based:

```text
stop when approved_drive_ready >= target
```

---

## 7. Candidate state machine

```text
discovered
  ↓
thumbnail_analyzing
  ↓
thumbnail_pass
  ↓
opened
  ↓
full_analyzing
  ↓
approved
  ↓
downloading
  ↓
downloaded
  ↓
storage_ready
  ↓
drive_uploading
  ↓
drive_ready
```

Reject states:

- `rejected_no_person`
- `rejected_head_ratio`
- `rejected_expression`
- `rejected_existing_headwear`
- `rejected_head_occlusion`
- `rejected_pose`
- `rejected_quality`
- `rejected_ai_risk`
- `rejected_duplicate`
- `rejected_policy`

Operational error states:

- `browser_failed`
- `source_failed`
- `analysis_failed`
- `download_failed`
- `storage_failed`
- `drive_upload_failed`

---

## 8. Reference suitability rules

Initial hat workflow requirements:

| Rule | Default |
|---|---|
| Contains people | Required |
| Person count | 1+ |
| Primary head ratio | 20–45% of image height |
| Visible smile / positive expression | Preferred / configurable threshold |
| Existing headwear | Reject |
| Head visible | Required |
| Head occlusion | Low |
| Pose | Usable for hat placement |
| Image quality | Above threshold |
| AI risk | Low |
| Casual/UGC visual style | Preferred |
| Smartphone-photo style | Preferred |

Head ratio:

```text
head_ratio = head_bbox_height / image_height
PASS when 0.20 <= head_ratio <= 0.45
```

For multiple people the analyzer stores all detected head ratios plus the chosen primary subject.

No face recognition or identity matching is required.

---

## 9. Two-stage analysis

### 9.1 Fast thumbnail analysis

Run local/cheap CV before opening or downloading high-resolution images.

Recommended stack:

- YOLO person/head detector
- MediaPipe face landmarks/blendshapes
- OpenCV quality measurements
- SigLIP semantic scoring
- lightweight headwear classifier

Example cascade:

```text
10,000 visible Pin candidates
  ↓
4,500 contain people
  ↓
1,300 composition candidates
  ↓
500 promising thumbnails
  ↓
200 full-image analyses
  ↓
80 approved references
```

### 9.2 Full-image analysis

Only promising candidates enter deeper analysis.

Example normalized result:

```json
{
  "people_count": 1,
  "primary_head_ratio": 0.32,
  "smile_score": 0.91,
  "head_visible": true,
  "existing_headwear": false,
  "head_occlusion": 0.04,
  "pose_yaw": 9,
  "pose_pitch": 3,
  "mobile_ugc_score": 0.84,
  "quality_score": 0.90,
  "ai_risk_score": 0.07,
  "product_fit_score": 0.92,
  "final_score": 0.89
}
```

A VLM is reserved for context/ambiguous cases; deterministic measurements remain preferred for geometry.

---

## 10. AI-origin risk

Do not trust a single AI-image detector.

Aggregate signals from:

- C2PA / Content Credentials when available
- EXIF
- known generator metadata
- visual artifact detectors
- model-based AI-risk classifier
- VLM sanity review

Suggested classes:

```text
LOW_RISK
REVIEW
HIGH_RISK
```

Absence of provenance metadata is not proof that an image is real.

---

## 11. Download and deduplication

Browser discovery does not automatically persist every visible image.

Only approved references enter download.

```text
approved candidate
   ↓
secure/bounded download
   ↓
verify MIME by decode
   ↓
enforce max bytes + decoded pixel limit
   ↓
SHA-256 while streaming
   ↓
pHash / visual duplicate check
   ↓
store or reuse existing content identity
```

Deduplication signals:

- SHA-256 exact duplicate
- pHash near duplicate
- visual embedding near duplicate

A duplicate file is not stored twice, but source occurrences can still be recorded for search-yield analytics.

Temporary local files must be bounded and deleted after transfer.

---

## 12. Product Reference Registry

Each SKU owns a versioned Product Reference Set.

Example:

```text
HAT-001
├── front
├── front_45_left
├── front_45_right
├── side_left
├── side_right
├── back
├── top
├── logo_closeup
├── embroidery_closeup
└── material_closeup
```

Product metadata includes geometry:

```json
{
  "product_id": "HAT-001",
  "type": "baseball_cap",
  "color": "beige",
  "brim_type": "curved",
  "logo_placement": "front_center",
  "logo_scale": 0.27,
  "hat_head_width_ratio": {
    "min": 1.05,
    "ideal": 1.14,
    "max": 1.23
  },
  "crown_head_height_ratio": {
    "min": 0.47,
    "max": 0.62
  }
}
```

Reference and geometry versions are immutable for historical runs.

### B3 implementation status

The production registry now persists tenant-scoped hat SKU geometry and versioned product reference views. Supported views are `front`, `front_45_left`, `front_45_right`, `side_left`, `side_right`, `back`, `top`, `logo_closeup`, `embroidery_closeup`, and `material_closeup`.

Reference uploads are bounded to 20 MB and decoded through CAM's existing safe visual-image path before storage. SHA-256 is the stable binary identity: replaying the same bytes for the same SKU/view is idempotent, while the same bytes assigned to another view reuse the existing Managed Drive object and create only the new registry relationship. New bytes for the same view create the next immutable reference version.

Product and reference deletion is soft archival. Historical versions therefore remain addressable for future generation-run provenance. The UI exposes the latest reference matrix, geometry revision, version history, Drive link, and an authenticated image preview streamed from Managed Drive.

Product Reference Registry files are durable creative inputs rather than temporary AI staging objects. They intentionally do not create `AssetStorageObjectModel` staging rows, so the Managed Storage cleanup lifecycle cannot expire them as transient analysis files. Database registry rows remain the durable tenant-scoped source of truth for product/reference ownership and versioning.

---

## 13. Worker Skill

Each product/product family has a versioned Generation Worker Skill.

Inputs:

- approved person reference
- product reference set
- product geometry
- composition constraints
- model/provider configuration
- previous Supervisor feedback

The first implementation should prefer reference-conditioned generation/inpainting so the original person, background, camera perspective and composition remain stable.

### Phase 6 implementation status

Campaigns can bind an active Product Registry SKU. The binding freezes the exact product geometry revision and latest active Product Reference versions into immutable campaign snapshots. If the SKU geometry or active reference set changes later, the campaign is marked stale and must explicitly refresh its product snapshot before new generation work can be prepared.

A generation attempt can be prepared only when:

- the person reference is durable in Managed Drive;
- the campaign has a bound active product;
- the bound product snapshot contains at least the required front reference;
- the binding is not stale.

Attempts are persisted in `rrugc_generation_attempts` with the exact candidate snapshot, product revision, product-reference snapshot, generation variant and Worker Skill version. The idempotency key includes the immutable binding fingerprint, so replaying the same preparation returns the same attempt while refreshing the product binding creates a new traceable attempt.

Prepared attempts can now be queued through the existing Processing Job runtime as `rrugc_generate`. Execution uses a dedicated provider-neutral reference-conditioned contract and a Gemini multi-reference adapter; the square-expansion image-generation flow remains separate. The worker loads the immutable person/product references from Managed Drive, sends the person reference first followed by labeled product views, stores the generated image back to Managed Drive, and persists provider request ID, model, output dimensions/hash/storage IDs, timestamps and bounded error state on the attempt.

The operator UI reports provider capability truthfully, polls queued/running attempts, exposes terminal errors, and provides the stored output when generation completes. Retryable provider/storage failures return the attempt to the queue with bounded retries; the final processing attempt is synchronized to terminal `failed` so the UI cannot remain falsely stuck in `queued`.

The worker should preserve, where supported:

- face/appearance
- hair except necessary physical hat interaction
- body
- background
- camera/perspective
- lighting
- composition

Only the product region and physically necessary blend area should change.

---

## 14. Generation provider abstraction

The workflow must not depend on one image provider.

```text
ImageGeneratorProvider
├── OpenAI-compatible provider
├── Google provider
├── Seedream/provider adapter
├── Flux/provider adapter
├── ComfyUI/local adapter
└── future providers
```

The orchestration contract is provider-neutral.

Only fine-tune/LoRA a SKU or family after measured prompt/reference generation demonstrates persistent fidelity problems.

---

## 15. Supervisor Skill

The Supervisor is not a free-form “does this look okay?” prompt.

Recommended split:

```text
deterministic CV measurements
        +
product similarity
        +
logo/text validation
        +
VLM contextual QA
```

Hat measurements:

- hat width / head width
- crown height / head height
- brim width / face width
- brim position relative to eye line
- crown position
- product/head intersection
- hair occlusion
- logo scale
- logo position
- product color similarity
- product visual similarity

Structured failure example:

```json
{
  "status": "FAIL",
  "reason": "HAT_TOO_LARGE",
  "metrics": {
    "hat_head_width_ratio": 1.31
  },
  "expected": {
    "max": 1.23
  },
  "correction": {
    "scale": 0.91,
    "preserve_brim_angle": true
  }
}
```

---

## 16. Correction loop and human review

```text
Generate
   ↓
Supervisor
   ├── PASS → Final
   └── FAIL
        ↓
structured correction
        ↓
retry generation
```

Default:

```text
MAX_GENERATION_ATTEMPTS = 3
```

After the attempt budget is exhausted:

```text
NEEDS_HUMAN_REVIEW
```

Review Board receives:

- original reference
- generated result
- product refs/version
- all generation attempts
- Supervisor measurements
- failure reason
- correction history

Reviewer actions:

- Approve
- Reject
- Retry
- Update correction
- Change product/ref selection

---

## 17. Storage architecture

Responsibilities:

```text
PostgreSQL = authoritative workflow metadata/state
R2         = authoritative binary/object storage
Drive      = collaboration/delivery mirror
```

Google Drive should not be the only storage record for workflow state.

Each exported asset records:

- CAM asset ID
- workflow run ID
- product ID
- R2 key
- Drive file ID
- Drive folder ID
- SHA-256
- export status
- timestamps

---

## 18. Google Drive layout

Recommended default:

```text
Creative AI Generation/
├── Product References/
│   ├── HAT-001/
│   └── HAT-002/
├── Reference Library/
│   └── YYYY-MM-DD/
│       ├── approved/
│       └── review/
├── Daily Output/
│   └── YYYY/MM/DD/
│       ├── HAT-001/
│       └── HAT-002/
└── Reports/
    └── YYYY/MM/
```

Use Drive IDs as durable identifiers; folder display names are presentation only.

Reference filename:

```text
REF_<reference_id>.<ext>
```

Generated filename:

```text
GEN_<product_id>_<run_id>_<asset_id>.<ext>
```

---

## 19. Realistic Review UGC UI

Top-level page:

```text
Realistic Review UGC

Overview
Workflows
References
Products
Runs
Schedule
Reports
Settings
```

### Overview KPI cards

- Total Workflows
- Active Runs
- Today Output
- Storage Used
- Drive Exported
- Queue Waiting

### Workflow pipeline

```text
① Source / Discovery
→ ② Filter / Analysis
→ ③ Generation
→ ④ QA / Review
→ ⑤ Export / Delivery
```

### Recent Workflows table

Columns:

- Name
- Type
- Product
- Progress
- Status
- Stats
- Updated
- Actions

Actions:

- View details
- Pause
- Resume
- Stop
- Duplicate
- Edit template
- Run again
- Archive

### Running Workflow panel

Shows current run, progress, candidate counts, analysis/download queue and recent output thumbnails.

---

## 20. Workflow creation wizard

```text
Select workflow type
  ↓
Select product
  ↓
Configure source/search
  ↓
Configure candidate rules
  ↓
Configure generation
  ↓
Configure QA
  ↓
Configure destination
  ↓
Schedule
  ↓
Review + create
```

Example configuration:

```json
{
  "name": "Hat US Lifestyle",
  "source": {
    "type": "pinterest_browser",
    "queries": [
      "happy woman casual outdoor",
      "woman laughing lifestyle photo"
    ],
    "target_references": 300
  },
  "filters": {
    "head_ratio_min": 0.20,
    "head_ratio_max": 0.45,
    "smile_min": 0.65,
    "ai_risk_max": 0.20,
    "existing_headwear": false
  },
  "product_id": "HAT-001",
  "generation": {
    "target": 300,
    "max_attempts": 3,
    "worker_skill": "worker-hat-001-v4"
  },
  "qa": {
    "supervisor_skill": "supervisor-hat-001-v7"
  },
  "destination": {
    "r2": true,
    "google_drive": true
  }
}
```

---

## 21. Scheduling

Supported modes:

- Manual
- Daily
- Weekly
- Cron
- Target-based
- Continuous campaign

Examples:

```text
Every day at 06:00 → collect 100 approved references
```

or:

```text
Continue until approved references >= 3,000
```

No autonomous source scan should run unless a workflow/campaign is explicitly enabled.

---

## 22. Retry and error classification

Every job records:

- attempt_count
- max_attempts
- next_attempt_at
- retry policy
- error code
- error category

Categories:

```text
TRANSIENT
PROVIDER
NETWORK
RATE_LIMIT
INPUT
CONTENT
POLICY
PERMANENT
UNKNOWN
```

Do not automatically retry permanent, policy or invalid-input failures.

All side-effecting steps must be idempotent.

Example generation idempotency key:

```text
workflow_run_id + reference_id + product_id + generation_variant
```

Drive export idempotency:

```text
asset_id + destination_folder_id
```

---

## 23. Database model proposal

New bounded domain tables:

```text
rrugc_workflow_templates
rrugc_workflow_runs
rrugc_stage_runs
rrugc_search_campaigns
rrugc_search_queries
rrugc_reference_candidates
rrugc_reference_sources
rrugc_product_profiles
rrugc_product_reference_sets
rrugc_product_reference_images
rrugc_skill_versions
rrugc_generation_attempts
rrugc_supervisor_results
rrugc_exports
rrugc_daily_metrics
rrugc_browser_agents
```

Do not duplicate existing Asset or Processing Job models. Reference/generated binaries that become managed assets must link to existing CAM assets.

---

## 24. Workflow/run states

Workflow run:

```text
draft
scheduled
queued
running
paused
stopping
completed
completed_with_errors
failed
cancelled
```

Stage:

```text
pending
queued
running
completed
failed
skipped
```

Browser Agent:

```text
offline
ready
busy
needs_login
error
```

---

## 25. Permissions

Recommended permissions:

- `realistic_review_ugc.read`
- `realistic_review_ugc.create`
- `realistic_review_ugc.run`
- `realistic_review_ugc.stop`
- `realistic_review_ugc.configure`
- `realistic_review_ugc.review`
- `realistic_review_ugc.products.manage`
- `realistic_review_ugc.skills.manage`

The existing Access Management framework remains authoritative.

---

## 26. Security and privacy

Browser session rules:

- Pinterest/browser cookies remain on Browser Scout Machine.
- Browser credentials are never stored in CAM database.
- CAM authenticates Browser Scout Agents with a scoped credential.
- Agent credentials must be revocable.
- Agent API never exposes Google refresh tokens or CAM secrets.

Image rules:

- no face recognition
- no person identity inference
- no face embedding for unique identity matching
- only geometry, visible expression, pose and quality needed by the workflow
- bound remote download sizes and decoded pixels
- reject unsafe/unsupported MIME types by decode, not extension
- never fetch arbitrary URLs from unauthenticated client requests

Source provenance and usage-right metadata should be retained where available.

---

## 27. Reporting

Daily KPIs:

- References discovered
- Thumbnail passed
- Full analysis approved
- Downloads completed
- Generated images
- QA passed
- QA failed
- Human review
- Drive exported
- Average generation attempts
- Average processing duration
- Estimated provider cost

Funnel example:

```text
10,000 scanned
  ↓
4,821 person detected
  ↓
1,237 thumbnail passed
  ↓
641 full analyzed
  ↓
310 approved references
  ↓
300 generated
  ↓
244 QA passed
  ↓
231 Drive exported
```

Reject breakdown example:

- head too small
- existing headwear
- head occlusion
- AI risk
- no smile
- poor quality
- unusable pose
- duplicate

Product QA reporting must include pass rate and failure reasons per Product Reference/Skill version.

---

## 28. Skill analytics

Track quality by version.

Example:

```text
Worker Skill v3  → 71% first-pass QA
Worker Skill v4  → 85% first-pass QA

Supervisor v6 → 7% human disagreement
Supervisor v7 → 2% human disagreement
```

A workflow run always records the exact template, product-ref, Worker Skill, Supervisor Skill and provider versions used.

---

## 29. Observability and safety controls

Metrics:

- queue depth
- jobs started/completed/failed
- retries
- average latency by stage
- Browser Scout heartbeat
- provider latency/error rate
- Drive export failures
- QA pass rate
- model/provider cost
- worker CPU/RAM/GPU where relevant

Circuit breakers:

- pause generation if recent QA failure rate exceeds configured threshold
- rotate/deprioritize a search query when approval yield is too low
- pause browser campaign when agent/session is unhealthy
- pause when daily cost cap is reached

Budget controls:

- max candidates
- max generations
- max daily model cost
- max workflow duration
- max retry count

---

## 30. Existing-system integrations

### Asset Explorer

Approved references and generated outputs become normal CAM assets with workflow tags/metadata.

### Job Queue

Shows individual execution tasks; Realistic Review UGC aggregates them into workflow/run/stage progress.

### Review Board

Receives only explicit human-review cases rather than every generated image.

### Visual Search / SigLIP

Reuse existing embedding/index infrastructure for duplicate/semantic analysis where appropriate. This workflow is separate from the existing Pinterest-style *visual search UX* documented in `docs/plans/CAM_VISUAL_SEARCH_PINTEREST_IMPLEMENTATION_GUIDE.md`.

### Google Drive

Reuse managed-storage credentials/provider conventions. Do not introduce a second Drive credential store.

---

## 31. Browser Scout MVP contract

The first executable milestone must support:

1. register a Browser Scout Agent;
2. agent heartbeat/status;
3. create a Pinterest search campaign with one or more queries;
4. CAM issues bounded scan work to the agent;
5. agent opens a Pinterest search page in a persistent authenticated Chrome profile;
6. agent scrolls a bounded number of result batches;
7. agent extracts visible candidate metadata and image URLs;
8. agent submits candidates to CAM;
9. CAM dedupes candidate/source identity;
10. approved/download-selected candidates can be downloaded through the bounded import worker;
11. imported references are stored/registered as CAM assets;
12. export worker uploads approved references to a configured Google Drive folder;
13. UI displays campaign progress and errors.

MVP deliberately does **not** include CAPTCHA bypass, stealth evasion, autonomous login or unbounded crawling.

---

## 32. Browser Scout Agent protocol

Preferred deployment is a small companion runtime on a user-controlled machine.

Agent configuration:

```text
CAM_BASE_URL
SCOUT_AGENT_ID
SCOUT_AGENT_TOKEN
CHROME_EXECUTABLE
CHROME_PROFILE_DIR
HEADLESS=false
POLL_INTERVAL_SECONDS
```

Conceptual endpoints:

```text
POST /api/v1/realistic-review-ugc/agents/register
POST /api/v1/realistic-review-ugc/agents/{id}/heartbeat
POST /api/v1/realistic-review-ugc/agents/{id}/claim
POST /api/v1/realistic-review-ugc/scan-jobs/{id}/candidates
POST /api/v1/realistic-review-ugc/scan-jobs/{id}/complete
```

CAM must remain usable if no Browser Scout Agent is online.

---

## 33. Google Drive export contract

Export worker:

```text
approved/reference asset
   ↓
resolve configured destination folder
   ↓
stream from authoritative storage
   ↓
GoogleDriveAssetStorage
   ↓
save drive_file_id / folder_id
   ↓
mark export complete
```

Drive retries are isolated from reference approval. A Drive outage must not lose the canonical R2/CAM asset.

---

## 34. Delivery phases

### Phase 1 — Foundation

- top-level Realistic Review UGC navigation
- overview shell
- workflow/campaign schema
- permissions
- API contracts
- Browser Agent registration/status

### Phase 2 — Browser Scout

- local persistent Chrome agent
- Pinterest query launch
- bounded scroll
- DOM/card extraction
- candidate ingestion
- campaign counters

### Phase 3 — Reference analysis

- thumbnail CV
- full analysis
- approval/rejection
- duplicate detection

### Phase 4 — Reference storage/export

- download/import
- R2/CAM asset registration
- Google Drive export
- source provenance

### Phase 5 — Product Registry

- Product Reference Sets
- geometry profiles
- versioning

### Phase 6 — Generation Worker

- provider abstraction
- generation attempts
- skill versioning

### Phase 7 — Supervisor

- deterministic measurements
- structured QA result
- bounded correction retry
- Review Board handoff

### Phase 8 — Reporting/optimization

- daily dashboard
- search-query yield
- product/skill QA metrics
- circuit breakers
- adaptive query prioritization

---

## 35. MVP Definition of Done

The first complete hat workflow is done when:

```text
User opens Realistic Review UGC
        ↓
creates/starts a Pinterest reference campaign
        ↓
Browser Scout opens Pinterest
        ↓
searches configured query
        ↓
collects candidate images
        ↓
CAM filters/analyzes references
        ↓
approved images download
        ↓
content is deduplicated
        ↓
asset stored/registered
        ↓
approved reference exported to Google Drive
        ↓
campaign metrics update
        ↓
product HAT-001 is selected
        ↓
Generation Worker produces output
        ↓
Supervisor validates hat
        ↓
fail → bounded correction retry
        ↓
pass → R2 + Drive
        ↓
daily report
```

For every final generated image, the system must trace:

```text
Final asset
→ Supervisor result
→ Generation attempt
→ Worker/Supervisor skill versions
→ Product reference version
→ Approved person reference
→ Source candidate / Pin
→ Search query
→ Search campaign
→ Workflow run
```

---

## 36. Non-negotiable implementation rules

1. PostgreSQL remains authoritative for workflow metadata.
2. R2 remains authoritative for managed binary storage.
3. Google Drive is export/collaboration, not workflow state.
4. Reuse existing Processing Job patterns; do not introduce a parallel queue.
5. Browser GUI/session state lives outside the production VPS.
6. No autonomous CAPTCHA bypass or credential circumvention.
7. All downloads are bounded and decode-validated.
8. Side effects are idempotent.
9. All skills/configuration are versioned.
10. Human review is explicit and bounded.
11. Do not perform face recognition.
12. Existing Asset Explorer, Search, Job Queue, Review Board and AI Operations must remain independently usable.
13. Realistic Review UGC is a top-level product surface and must not be implemented as an AI Operations sub-tab.

---

## 37. Initial implementation decision

The first engineering slice is:

```text
RRUGC-00 docs + route/navigation
RRUGC-01 Browser Agent + Pinterest bounded scan contract
RRUGC-02 candidate persistence + campaign UI
RRUGC-03 approved reference download/import
RRUGC-04 managed Google Drive export
```

Generation and Supervisor work starts only after the reference acquisition pipeline is measurable and reliable.

---

## 38. Implemented reference-analysis slice

The reference acquisition pipeline now separates browser discovery from analysis and storage:

```text
Pinterest Browser Scout
  ↓
candidate persistence
  ↓
rrugc_candidate_analyze Processing Job
  ↓
tenant-aware Gemini visual assessment (v1 metric source)
  ↓
deterministic CAM threshold policy
  ├── rejected_* + metrics + reason
  └── approved
        ↓
      rrugc_candidate_import Processing Job
        ↓
      Managed Google Drive
```

The v1 analyzer returns visible composition metrics only. It must not identify people or infer demographic/protected attributes. `ai_risk_score` is treated as a risk signal rather than proof of image origin.

Campaign-level filter defaults for hats are:

- primary head ratio: 20–45% of full image height
- minimum smile score: 0.65
- maximum head occlusion: 0.25
- maximum AI-risk signal: 0.20
- minimum quality score: 0.55
- minimum casual/mobile UGC score: 0.55
- minimum hat/product-fit score: 0.55
- visible head required
- existing headwear rejected

These thresholds are persisted on each campaign. The deterministic policy owns PASS/FAIL decisions; the model only supplies the structured visible measurements. Individual measurements can later migrate to YOLO/MediaPipe/local CV without changing the Scout, database workflow, or approval state machine.

Only an `approved` reference can be queued for Drive. Auto-import queues a separate idempotent storage job after analysis passes, so Pinterest scanning no longer blocks on VLM or Drive network latency.

---

## 39. Implemented Supervisor QA slice

Phase 7 now adds a durable Supervisor after every safely stored generated image:

```text
completed RRUGC generation
  ↓
rrugc_supervisor_qa Processing Job
  ↓
bounded comparison sheet
  ├── generated output
  ├── frozen person/scene source
  └── frozen product-reference snapshot (up to 4 views)
        ↓
tenant-aware Gemini structured visible QA
        ↓
deterministic Supervisor policy
  ├── PASS
  ├── FAIL + structured correction
  └── NEEDS_HUMAN_REVIEW after retry budget
```

Supervisor results are persisted in `rrugc_supervisor_results` with provider/model provenance, explicit metrics, expected thresholds, failure reason, structured correction and Processing Job linkage. The Supervisor prompt is limited to visible product fidelity and scene-preservation properties. It must not identify the person or infer protected/demographic attributes.

The deterministic Supervisor currently evaluates product visual similarity, product color similarity, logo/embroidery fidelity, placement, scale, person/scene preservation, photorealism, artifact risk and an estimated hat/head width ratio when visible. Representative failure reasons include `HAT_TOO_LARGE`, `HAT_TOO_SMALL`, `PRODUCT_VISUAL_MISMATCH`, `PERSON_SCENE_CHANGED` and `ARTIFACT_RISK_HIGH`.

A failed Supervisor result can prepare a corrected generation attempt. Correction preparation copies the exact frozen person/product provenance from the source generation and appends only the structured Supervisor correction to the worker prompt. It does **not** automatically execute another provider generation; an operator must explicitly queue the prepared attempt. The generation budget is capped at `MAX_GENERATION_ATTEMPTS = 3` per campaign/candidate path. Once that budget is exhausted, the Supervisor result is surfaced as `needs_human_review`.

The Realistic Review UGC UI now surfaces Supervisor status, failure reason, important metrics, summary, correction preparation, and the human-review state next to each generation attempt.

Phase 7 hands terminal Supervisor outcomes to Phase 8 through durable review provenance.

---

## 40. Implemented Review Board handoff

Phase 8 adds an auditable review gate between Supervisor QA and export:

```text
Supervisor PASS
  └── standard-priority RRUGC review task
Supervisor NEEDS_HUMAN_REVIEW
  └── high-priority RRUGC review task
Supervisor FAIL with retry budget remaining
  └── correction workflow only; no review task
        ↓
existing Review Board
  ├── Shared feedback
  └── Realistic UGC
        ↓
reviewer decision
  ├── APPROVED → export_ready
  └── REJECTED → not_exportable
```

Review tasks are persisted in `rrugc_review_tasks` and are unique per generated attempt. The task stores the Supervisor result, campaign/candidate/product provenance, queue reason, priority, reviewer identity, optional bounded note and review timestamps. The generated attempt itself also carries `review_status`, `review_task_id`, `reviewed_by_user_id`, `reviewed_at`, `review_note` and `export_status`, so final approval/rejection provenance remains attached to the exact stored output.

The handoff is idempotent. A retried Supervisor job reuses the existing review task instead of creating duplicates. A `PASS` result creates a standard-priority task; `needs_human_review` creates a high-priority task. A normal `FAIL` result that can still use the correction loop is intentionally excluded from Review Board.

The existing Review Board now has two sources: **Shared feedback** and **Realistic UGC**. Access to the page is visible when the user has either `public_review.read` or `realistic_review_ugc.read`; backend permissions remain source-specific. Realistic UGC mode uses the authenticated generation-output endpoint for image preview, shows product/campaign and Supervisor evidence, highlights high-priority human-review cases, and exposes Approve/Reject only to users with `realistic_review_ugc.run`.

Approval/rejection is atomic across the review task and generation attempt. Repeating the same terminal decision is idempotent; attempting the opposite terminal transition returns a conflict. Approval does not copy or re-upload the Managed Drive image—it only marks the exact stored output `export_ready`. Rejection marks it `not_exportable`.

A bounded reconcile path can backfill review tasks for older terminal Supervisor results that predate this handoff.

Phase 8 ends when an approved generation is marked `export_ready`.

---

## 41. Implemented export + final asset catalog registration

Phase 9 turns approved RRUGC outputs into first-class Creative Asset Manager assets without duplicating the generated file:

```text
review approved
  ↓
export_ready generation
  ↓
single or bounded campaign export
  ↓
canonical AssetModel (dedupe by tenant + content hash)
  ↓
durable managed-storage registration
  ↓
rrugc_exports provenance
  ↓
generation export_status = exported
```

Export is a metadata/catalog operation, not another image transfer. The generation output already lives in Managed Google Drive, so Phase 9 reuses its exact `remote_file_id`, folder and web URL. When the canonical catalog asset does not yet exist, the exporter creates the asset from the frozen output content hash, MIME type and byte size, then registers the existing Drive object as its durable managed-storage object. It does **not** copy or re-upload the file.

Every exported generation has one durable `rrugc_exports` row containing the campaign, generation attempt, approved review task, canonical catalog asset, content hash, storage location, exporting user and timestamp. The generation attempt also carries `export_record_id`, `catalog_asset_id`, `exported_by_user_id` and `exported_at`. Export is idempotent per tenant/generation attempt; retries return the existing export record.

The RRUGC API now supports:

- exporting one approved generation,
- bounded campaign export for all currently `export_ready` outputs,
- listing export records,
- campaign export summary for generated, review-pending, approved, rejected, export-ready and exported counts.

The Realistic Review UGC generation workspace exposes the Phase 9 summary and a bounded **Export ready** action. Generation attempts show `Review pending`, `Export ready`, `Not exportable` or `Cataloged`. The Review Board also displays `Cataloged` after an approved output has been registered.

Read operations remain protected by `realistic_review_ugc.read`; export mutations require `realistic_review_ugc.run`. All lookups and writes remain tenant-scoped.

Phase 9 ends when approved outputs are registered as canonical catalog assets.

---

## 42. Implemented downstream delivery + lifecycle controls

Phase 10 adds an explicit delivery layer after catalog registration:

```text
cataloged outputs
  ↓
explicit delivery destination
  ↓
delivery package + immutable item manifest
  ↓
Google Drive server-side copy
  ↓
delivered / partial_failed
  ↓
optional campaign auto-completion
  ↓
retention lifecycle reconciliation
```

A delivery destination is tenant-scoped configuration with a human-readable name, destination type, Google Drive folder ID and retention period. The first supported destination type is `google_drive_folder`. Destinations are intentionally explicit; the workflow never guesses a folder from the campaign or product.

Delivery does not download and re-upload the generated image. The Managed Google Drive provider performs a Drive server-side copy from the cataloged managed object into the configured destination folder. Each delivery item writes `cam_rrugc_delivery_item` into Drive `appProperties`, so a retry can discover an already-created copy after a network timeout instead of producing duplicates.

Every delivery creates durable provenance:

- `rrugc_delivery_packages` records campaign, destination, immutable export manifest, counts, delivery timestamps and retention expiry,
- `rrugc_delivery_items` records the source export/catalog asset, source Drive ID, delivered Drive ID, delivered URL, status and any bounded error,
- deterministic package idempotency prevents the same campaign/export set from producing duplicate packages for the same destination,
- `partial_failed` packages can be retried in place; already-delivered items are skipped.

Campaign lifecycle policy can optionally auto-complete the workflow for one selected destination. Completion is only recorded when the chosen destination has a fully delivered package covering all current cataloged outputs, there are no `export_ready` outputs, and there are no pending human-review tasks. The campaign stores the selected completion destination and `completed_at` timestamp. Existing campaign status semantics remain compatible with the earlier scouting workflow; `completed_at` is the stronger delivery-completion marker introduced here.

Retention is deliberately non-destructive to the asset catalog. A delivered package receives `expires_at` from the destination retention policy. Lifecycle reconciliation marks the package `expired` after that time, but does **not** delete the canonical AssetModel, the managed original, or its Phase 9 export provenance. Delivery copies can therefore be governed independently without risking the source-of-truth asset.

The RRUGC API now supports destination create/list/archive, campaign lifecycle policy, campaign delivery, package listing, delivery summary and bounded lifecycle reconciliation. Read endpoints require `realistic_review_ugc.read`; destination, policy, delivery and lifecycle mutations require `realistic_review_ugc.run`.

The Realistic Review UGC workspace exposes **PHASE 10 · DELIVERY + LIFECYCLE** with destination creation/selection, server-side delivery, campaign auto-completion policy, package outcome counters and retention reconciliation. The UI explicitly states that delivery expiry leaves catalog originals untouched.

A future phase can add channel-specific adapters (for example ad-platform or commerce destinations), scheduled retention reconciliation independent of UI/API activity, delivery webhooks/notifications and richer cross-campaign operations reporting. Those integrations are not part of Phase 10.

