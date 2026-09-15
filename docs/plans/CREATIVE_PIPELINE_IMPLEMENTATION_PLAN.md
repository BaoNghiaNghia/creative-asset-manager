# Creative Pipeline — Listing-to-Video Automation Plan

> **Repository:** `BaoNghiaNghia/creative-asset-manager`  
> **Document date:** 2026-09-15  
> **Status:** CP-00 architecture/contracts complete. Detailed normative semantics are in [CREATIVE_PIPELINE_CP00_CONTRACTS.md](CREATIVE_PIPELINE_CP00_CONTRACTS.md). This document does **not** authorize production deployment, destructive source-folder operations, provider credential changes, or bulk generation runs.
> **Feature name:** `Creative Pipeline`  
> **Navigation target:** place the new tab between **Visual Search** and **Inventory Daily**, reusing the visual language, spacing, controls, drawers, filters, status chips, and interaction patterns already established elsewhere in the application.

---

## 1. Purpose

Creative Pipeline turns the existing listing-folder hierarchy into a durable, retryable, versioned creative-production workflow.

The system must discover new listing folders automatically, create one durable listing task per folder, and run the listing through the following node sequence:

```text
Input Data
    ↓
Idea Story
    ↓
Prompt
    ↓
Generating
    ↓
Video Output
    ↓
Watermark & Smart Enhance
```

Every node is independently retryable. Successful upstream work must not be repeated when only a downstream node fails.

The initial platform profiles are:

```text
Amazon
├── 16:9 video output
└── 9:16 video output

Etsy
└── 1:1 video output
```

The architecture must preserve complete lineage from listing input through idea, prompt, generation attempt, raw video, and enhanced final video.

---

## 2. Existing source-folder structure

The current source hierarchy already exists and should remain intact.

Real example:

```text
Etsy - EmbrolyShop/
├── listing - 4527798886/
│   ├── Source/
│   └── UGC - Macro Vid/
│
└── listing - 4540775224/
    ├── Source/
    └── UGC - Macro Vid/
```

The implementation must **not** redesign, move, rename, or overwrite these existing user-managed folders.

### 2.1 Parent folder

The parent folder represents one logical source/shop group.

Examples:

```text
Etsy - EmbrolyShop
Etsy - Shop A
Amazon - Store B
```

This maps to the `SourceGroup` domain object.

### 2.2 Listing folder

A child folder using the naming pattern:

```text
listing - <listing_key>
```

represents one stable `ListingTask`.

Examples:

```text
listing - 4527798886
listing - 4540775224
listing - B0ABC12345
```

Do not make `ASIN` the universal domain field.

Use the generic field:

```text
listing_key
```

Platform meaning:

```text
Amazon → listing_key = ASIN
Etsy   → listing_key = Etsy Listing ID
```

The original folder name remains the user-facing display identity.

### 2.3 Existing user-managed folders

The following folders remain outside system ownership:

```text
Source/
UGC - Macro Vid/
```

They may be used as pipeline input/reference data, but Creative Pipeline must not destructively modify their contents.

---

## 3. System-owned `Pipeline` folder

Creative Pipeline creates exactly one system-owned folder named:

```text
Pipeline
```

inside each listing folder.

Resulting structure:

```text
Etsy - EmbrolyShop/
│
├── listing - 4527798886/
│   ├── Source/
│   ├── UGC - Macro Vid/
│   └── Pipeline/
│
└── listing - 4540775224/
    ├── Source/
    ├── UGC - Macro Vid/
    └── Pipeline/
```

### 3.1 Ownership boundary

The contract is:

```text
Source/             → user/source-owned
UGC - Macro Vid/    → user/reference-owned
Pipeline/           → system-owned
```

Creative Pipeline may create and update files under `Pipeline/` only.

It must not use destructive operations on sibling user-managed folders.

### 3.2 Recommended `Pipeline` structure

```text
Pipeline/
├── Input/
├── Idea Story/
├── Prompt/
│   ├── seedance/
│   └── google_omni/
├── Generating/
├── Video Output/
├── Watermark & Smart Enhance/
└── Logs/
```

Node-to-folder mapping:

```text
Input Data                  → Pipeline/Input/
Idea Story                  → Pipeline/Idea Story/
Prompt                      → Pipeline/Prompt/
Generating                  → Pipeline/Generating/
Video Output                → Pipeline/Video Output/
Watermark & Smart Enhance   → Pipeline/Watermark & Smart Enhance/
Operational file logs       → Pipeline/Logs/
```

The physical folder hierarchy is for artifacts and operator visibility. **Database state remains the source of truth for pipeline execution state.**

The system must never infer that a node is completed only because a file happens to exist.

---

## 4. Platform-specific physical output structure

### 4.1 Etsy

Etsy V1 produces `1:1` video.

Recommended structure:

```text
listing - 4527798886/
├── Source/
├── UGC - Macro Vid/
└── Pipeline/
    ├── Input/
    │   ├── input_v001.json
    │   └── input_manifest_v001.json
    │
    ├── Idea Story/
    │   ├── idea_v001.json
    │   ├── idea_v002.json
    │   └── current.json
    │
    ├── Prompt/
    │   ├── seedance/
    │   │   ├── prompt_v001.json
    │   │   └── prompt_v002.json
    │   └── google_omni/
    │       └── prompt_v001.json
    │
    ├── Generating/
    │   ├── run_0001.json
    │   ├── run_0002.json
    │   └── run_0003.json
    │
    ├── Video Output/
    │   └── 1x1/
    │       ├── v001.mp4
    │       ├── v002.mp4
    │       └── v003.mp4
    │
    ├── Watermark & Smart Enhance/
    │   └── 1x1/
    │       ├── v001_enhanced.mp4
    │       ├── v002_enhanced.mp4
    │       └── v003_enhanced.mp4
    │
    └── Logs/
        ├── pipeline.json
        └── errors.json
```

### 4.2 Amazon

Amazon V1 produces `16:9` and `9:16`.

```text
Pipeline/
├── ...
├── Video Output/
│   ├── 16x9/
│   │   ├── v001.mp4
│   │   └── v002.mp4
│   └── 9x16/
│       ├── v001.mp4
│       └── v002.mp4
│
└── Watermark & Smart Enhance/
    ├── 16x9/
    │   ├── v001_enhanced.mp4
    │   └── v002_enhanced.mp4
    └── 9x16/
        ├── v001_enhanced.mp4
        └── v002_enhanced.mp4
```

### 4.3 No destructive overwrite

Every intentional regeneration creates a new version.

Example:

```text
first successful generation  → v001.mp4
regenerate                   → v002.mp4
regenerate again             → v003.mp4
```

`v001.mp4` must not be overwritten by `v002.mp4`.

The same rule applies to idea stories, prompts, generation records, and enhanced outputs.

---

## 5. Product goals

The first production-ready version should support this end-to-end flow:

```text
Configured source hierarchy
        ↓
Scheduled daily scan
        ↓
Detect new listing folders
        ↓
Create ListingTask exactly once
        ↓
Ensure Pipeline/ exists
        ↓
Create initial PipelineRun
        ↓
Input Data snapshot
        ↓
Idea Story via OpenAI + Knowledge Pack
        ↓
Model-specific prompts via OpenAI + Knowledge Pack
        ↓
Seedance / Google Omni generation
        ↓
Versioned raw Video Output inside listing/Pipeline
        ↓
Watermark Removal + Smart Enhance
        ↓
Versioned final artifact
```

The operator should be able to answer from one UI:

- Which shops/groups contain active listings?
- Which listing folders were discovered recently?
- What node is each listing currently on?
- Which node failed and why?
- How many retries occurred?
- Which Idea Story version produced a Prompt?
- Which Prompt produced a GenerationRun?
- Which GenerationRun produced a raw video?
- Which enhanced video came from which raw video?
- How many versions exist per model/aspect ratio?
- Which work is waiting, running, retrying, blocked, failed, or complete?

---

## 6. Non-goals for V1

V1 does not include:

- automatic publishing to Amazon or Etsy;
- automatic ad campaign creation;
- automatic conversion-performance optimization;
- full user-editable DAG/workflow builder;
- arbitrary marketplace support;
- vector-database/RAG infrastructure unless the Knowledge Pack becomes too large for deterministic file selection;
- automatic destructive cleanup of older generated versions;
- moving or deleting `Source/` or `UGC - Macro Vid/`;
- cross-listing generation state sharing;
- destructive replacement of generated outputs.

---

## 7. Core domain model

The domain hierarchy is:

```text
SourceGroup
    ↓
ListingTask
    ↓
PipelineRun
    ↓
NodeRun / GenerationRun / Artifact
```

### 7.1 SourceGroup

Represents the persisted parent folder/shop grouping.

Recommended fields:

```text
id
platform                  amazon | etsy
name
source_provider
external_source_id
external_folder_id
source_path
active
scan_enabled
scan_schedule
timezone
last_scan_at
last_successful_scan_at
created_at
updated_at
```

Example:

```text
name = Etsy - EmbrolyShop
platform = etsy
```

Do not derive this relationship only by splitting folder paths at UI-render time.

### 7.2 ListingTask

Represents one discovered listing folder.

Recommended fields:

```text
id
source_group_id
platform
listing_key
folder_name
folder_path
external_folder_id
pipeline_folder_id
status
current_pipeline_run_id
first_discovered_at
last_seen_at
created_at
updated_at
```

Do not require an `asin` field as the primary identity.

If Amazon-specific ASIN lookup is useful later, expose it as platform-specific metadata or a derived alias of `listing_key`.

### 7.3 PipelineRun

Represents one intentional production branch/run for a listing.

```text
id
listing_task_id
run_number
status
trigger_type              discovery | manual | regenerate
triggered_by
knowledge_snapshot_id
input_artifact_id
started_at
completed_at
created_at
updated_at
```

One listing may retain many historical runs:

```text
listing - 4527798886
├── Pipeline Run #1
├── Pipeline Run #2
└── Pipeline Run #3
```

### 7.4 NodeRun

Represents one node execution.

```text
id
pipeline_run_id
node_type
status
attempt_count
max_attempts
input_version
output_version
started_at
completed_at
next_retry_at
last_error_code
last_error_message
created_at
updated_at
```

Canonical node types:

```text
input_data
idea_story
prompt
video_generation
video_output
watermark_smart_enhance
```

### 7.5 GenerationRun

One prompt may create many generation attempts and outputs.

```text
id
pipeline_run_id
prompt_artifact_id
provider
model_name
aspect_ratio
generation_number
status
provider_request_id
attempt_count
started_at
completed_at
last_error_code
last_error_message
created_at
updated_at
```

### 7.6 Artifact

Artifact records preserve lineage between database state and physical files.

```text
id
listing_task_id
pipeline_run_id
node_run_id
generation_run_id
artifact_type
version
provider
model_name
aspect_ratio
storage_kind
relative_path
content_hash
mime_type
size_bytes
metadata_json
created_at
```

Examples:

```text
input snapshot
idea story JSON
Seedance prompt JSON
Google Omni prompt JSON
generation request metadata
raw generated video
enhanced video
render metadata
```

The system must be able to answer:

> Which exact input, knowledge snapshot, idea, prompt, and generation attempt produced this video?

---

## 8. Folder discovery and daily scanner

### 8.1 Scanner scope

The scanner inspects configured parent groups and their direct/allowed listing children.

Real target structure:

```text
Etsy - EmbrolyShop/
├── listing - 4527798886/
└── listing - 4540775224/
```

The scanner should focus on two domain levels:

```text
SourceGroup parent
    ↓
listing - <listing_key>
```

Folders nested under a listing such as:

```text
Source
UGC - Macro Vid
Pipeline
Input
Prompt
Video Output
```

must never be interpreted as separate listing tasks.

### 8.2 Recognition rule

V1 convention:

```text
listing - <listing_key>
```

Parsing must be deterministic and normalized.

Do not assume the extracted identifier is always an Amazon ASIN.

### 8.3 Idempotent discovery identity

Preferred identity:

```text
source_group_id + external_folder_id
```

Fallback when the provider lacks a stable folder ID:

```text
source_group_id + normalized_folder_path
```

Do not use `listing_key` alone because the same listing identifier could theoretically appear in multiple source groups.

### 8.4 Daily scanner behavior

```text
load configured SourceGroups
        ↓
list listing-level child folders
        ↓
match `listing - <listing_key>`
        ↓
resolve stable listing identity
        ↓
ListingTask exists?
    ├── yes
    │    ├── update last_seen_at / safe metadata
    │    └── DO NOT create duplicate initial run
    │
    └── no
         ├── create ListingTask
         ├── ensure listing/Pipeline exists
         ├── create initial PipelineRun
         └── make Input Data runnable
```

### 8.5 `Pipeline` folder creation

For a newly discovered listing:

```text
Pipeline exists?
├── no  → create Pipeline/ and required child folders lazily or eagerly
└── yes → reuse it
```

`Pipeline/` existence is not itself evidence that a ListingTask already exists. Database identity is authoritative.

Conversely, a known ListingTask whose `Pipeline/` folder was removed should enter a recoverable storage/error state rather than creating a duplicate task.

### 8.6 Existing listing behavior

A daily scan of an already-known listing should normally:

```text
update presence metadata
validate expected source context
continue existing pipeline state
```

It must not automatically regenerate completed videos every day.

### 8.7 Missing listing folders

Do not delete history immediately if a listing folder disappears.

Suggested lifecycle:

```text
active
missing_source
archived
```

Temporary provider outages must not destroy pipeline history.

### 8.8 Schedule

Support:

```text
scheduled daily scan
manual Scan now
future event/webhook discovery
```

Scheduled execution must be durable rather than dependent only on an API-process timer.

---

## 9. Platform profiles

Marketplace-specific generation behavior should be centralized in a platform profile.

### 9.1 Etsy profile

```text
platform = etsy
required_aspect_ratios = [1:1]
```

### 9.2 Amazon profile

```text
platform = amazon
required_aspect_ratios = [16:9, 9:16]
```

### 9.3 Future profile settings

A profile may later define:

```text
required_aspect_ratios
target_duration_rules
model_preferences
prompt_rules
output_count
artifact naming rules
watermark policy
enhancement policy
```

Node implementations should consume the profile instead of scattering `if amazon` / `if etsy` logic throughout the codebase.

---

## 10. Pipeline execution model

The logical flow is sequential until generation fan-out:

```text
Input Data
    ↓
Idea Story
    ↓
Prompt
    ↓
Generating
    ├── provider/model/aspect ratio branch A
    ├── provider/model/aspect ratio branch B
    └── provider/model/aspect ratio branch C
              ↓
         Video Output
              ↓
Watermark & Smart Enhance
```

### 10.1 Durable queue substrate

Reuse the application's durable processing/job infrastructure where practical rather than introducing a completely separate queue implementation.

Conceptually:

```text
Creative domain state
ListingTask / PipelineRun / NodeRun
                ↓
Durable processing job
                ↓
Worker claims node execution
                ↓
Artifact persisted
                ↓
Node state committed
                ↓
Orchestrator unlocks downstream work
```

Domain tables express creative workflow state; processing jobs perform execution.

### 10.2 Orchestrator ownership

One orchestration service owns dependency transitions.

Examples:

```text
Input completed
→ Idea Story becomes ready

Idea Story completed
→ Prompt becomes ready

Prompt completed
→ required GenerationRuns are created

Generation completed
→ raw video Artifact registered

Raw video registered
→ Watermark & Smart Enhance becomes runnable
```

Workers and UI components must not independently implement competing node-transition logic.

### 10.3 Canonical node states

Recommended internal states:

```text
pending
ready
running
retry_wait
completed
failed
blocked
cancelled
```

UI may map these to simpler labels:

```text
Waiting
Running
Retrying
Completed
Failed
Blocked
```

### 10.4 Listing state

Listing status should be derived from its current PipelineRun.

Example priority:

```text
failed     → required active node failed terminally
running    → one or more nodes are running
retrying   → one or more nodes await retry
queued     → runnable work waits for worker capacity
blocked    → required human/config/input dependency missing
completed  → all required final outputs complete
```

---

## 11. Retry and regenerate semantics

Retry exists at every node.

### 11.1 Retry granularity

Retry the smallest failed unit.

```text
Idea Story failure
→ retry Idea Story only

Seedance 16:9 failure
→ retry that GenerationRun only

Smart Enhance v003 failure
→ retry enhancement for v003 only
```

Do not rerun completed upstream nodes unnecessarily.

### 11.2 Retry metadata

Every retryable node/run should retain:

```text
attempt_count
max_attempts
next_retry_at
last_error_code
last_error_message
```

Provider-specific transient errors should be normalized into canonical error codes.

### 11.3 Automatic retry

Potential retryable classes:

```text
provider busy
HTTP 429
HTTP 5xx
timeout
temporary network failure
```

Potential terminal classes:

```text
missing required input
invalid structured output
unsupported format
invalid provider configuration
invalid permission
```

Use bounded exponential backoff with jitter for transient failures.

### 11.4 Retry versus regenerate

These are distinct commands:

```text
Retry
→ same logical input/version
→ another attempt to achieve the same intended output

Regenerate
→ new creative/output version or branch
→ old successful versions preserved
```

Examples:

```text
Retry prompt_v002 generation request
→ still belongs to prompt_v002

Regenerate Prompt
→ creates prompt_v003
→ creates new downstream GenerationRuns
```

---

## 12. Input Data node

The Input Data node creates an immutable snapshot used by downstream nodes.

Possible sources:

```text
listing identity
platform
listing_key
source group/shop
Source/ folder files
UGC - Macro Vid/ reference files
product images
existing videos
listing title
folder notes
selected metadata
product description if available
operator notes
required aspect ratios
```

Output:

```text
Pipeline/Input/input_v001.json
Pipeline/Input/input_manifest_v001.json
```

Downstream nodes should consume the snapshot rather than repeatedly reading mutable source state.

This improves reproducibility and lineage.

---

## 13. Knowledge Pack

Idea Story and Prompt nodes use OpenAI together with a controlled Knowledge Pack.

V1 should avoid unnecessary RAG complexity.

Recommended repository-managed knowledge structure:

```text
knowledge/
├── global_rules.md
├── idea_story_rules.md
├── amazon_rules.md
├── etsy_rules.md
├── seedance_2_5_rules.md
├── google_omni_rules.md
├── hooks_library.md
└── examples/
```

### 13.1 Deterministic KnowledgeLoader

Example Idea Story selection:

```text
platform = etsy
node = idea_story

load:
global_rules
+ etsy_rules
+ idea_story_rules
+ hooks_library
```

Example Prompt selection:

```text
platform = amazon
node = prompt
model = seedance_2_5

load:
global_rules
+ amazon_rules
+ seedance_2_5_rules
+ selected examples
```

### 13.2 Knowledge snapshot

A PipelineRun should record which knowledge revision/snapshot was used.

Do not let future edits to knowledge files make historical generation lineage ambiguous.

The initial implementation may use content hashes / manifest hashes instead of introducing a dedicated vector database.

---

## 14. Idea Story node

The Idea Story node converts listing/input context into a structured creative intermediate representation.

It should not immediately emit provider-specific video prompts.

Suggested JSON contract:

```json
{
  "concept": "...",
  "audience": "...",
  "hook": "...",
  "story_beats": [
    {
      "order": 1,
      "duration_hint": "0-2s",
      "action": "...",
      "dialogue": "...",
      "visual_note": "..."
    }
  ],
  "product_focus": ["..."],
  "must_show": ["..."],
  "must_avoid": ["..."],
  "tone": "...",
  "platform_notes": "..."
}
```

Store versioned artifacts:

```text
Pipeline/Idea Story/idea_v001.json
Pipeline/Idea Story/idea_v002.json
```

Retries for transport/provider failure may remain the same logical version.

Explicit `Regenerate Idea` creates a new version.

The OpenAI response must be schema-validated before the node is marked completed.

---

## 15. Prompt node

Prompt converts one Idea Story into model-specific generation prompts.

Initial targets:

```text
Seedance 2.5
Google Omni
```

Prompt generation is provider/model specific because prompting conventions differ.

Suggested artifact contract:

```json
{
  "prompt_version": 3,
  "idea_story_version": 2,
  "platform": "amazon",
  "outputs": [
    {
      "provider": "seedance",
      "model": "seedance-2.5",
      "aspect_ratio": "16:9",
      "prompt": "..."
    },
    {
      "provider": "seedance",
      "model": "seedance-2.5",
      "aspect_ratio": "9:16",
      "prompt": "..."
    },
    {
      "provider": "google_omni",
      "model": "configured-model",
      "aspect_ratio": "16:9",
      "prompt": "..."
    }
  ]
}
```

Physical artifacts:

```text
Pipeline/Prompt/seedance/prompt_v001.json
Pipeline/Prompt/google_omni/prompt_v001.json
```

Prompt JSON should retain upstream lineage:

```text
input version
idea version
knowledge snapshot
platform profile
model target
created_at
```

---

## 16. Generation provider layer

Generation orchestration must not contain provider-specific networking logic directly.

Recommended provider abstraction:

```text
VideoGenerationProvider
├── SeedanceProvider
└── GoogleOmniProvider
```

Common conceptual operations:

```text
submit()
poll()
cancel()
fetch_result()
normalize_error()
```

Each provider adapter owns:

```text
authentication
request serialization
provider request ID
provider polling semantics
provider status mapping
response normalization
error normalization
```

Provider adapters should never own pipeline dependency transitions.

### 16.1 Generation fan-out

Example Amazon prompt:

```text
Prompt v002
├── Seedance 2.5 / 16:9 / generation #1
├── Seedance 2.5 / 9:16 / generation #1
├── Google Omni / 16:9 / generation #1
└── Google Omni / 9:16 / generation #1
```

The exact required model matrix should be configuration-driven rather than hardcoded into the orchestrator.

---

## 17. Generating node and generation records

`Generating` is the execution stage; `GenerationRun` is the durable record.

Recommended generation metadata artifact:

```text
Pipeline/Generating/run_0001.json
```

Example content:

```json
{
  "generation_run_id": "...",
  "provider": "seedance",
  "model": "seedance-2.5",
  "aspect_ratio": "1:1",
  "prompt_version": 2,
  "attempt": 1,
  "provider_request_id": "...",
  "status": "completed"
}
```

Do not store raw provider secrets/tokens in these files.

---

## 18. Video Output node

A successful generation must produce a versioned raw video Artifact and physical file inside the listing's `Pipeline/Video Output/` hierarchy.

Artifact version allocation must be concurrency safe.

Do not determine `vNNN` only by listing existing files without synchronization, because two workers may select the same next version.

Preferred sequence:

```text
reserve Artifact/version transactionally
        ↓
write/upload physical output
        ↓
verify output
        ↓
mark Artifact available
```

If physical storage write fails, the artifact should remain failed/incomplete and be retryable without silently overwriting another version.

---

## 19. Watermark & Smart Enhance node

Each raw video Artifact becomes an independent enhancement work item.

Conceptually:

```text
raw v003
    ↓
watermark removal
    ↓
smart enhance
    ↓
final v003_enhanced
```

The node should integrate with the existing watermark/smart-enhance processing implementation through a stable service boundary rather than duplicating enhancement algorithms in Creative Pipeline.

Retry is artifact-specific.

Example:

```text
v001_enhanced ✅
v002_enhanced ✅
v003_enhanced ❌
```

Retrying `v003` must not rerun `v001` or `v002`, and must not regenerate the raw video.

Raw output must always be preserved.

---

## 20. Source of truth and filesystem reconciliation

### 20.1 Database authority

The database is authoritative for:

```text
ListingTask identity
PipelineRun state
NodeRun state
GenerationRun state
Artifact identity/version/lineage
retry state
error state
```

### 20.2 Filesystem/provider storage authority

The listing folder is authoritative for the physical artifact bytes that the Artifact row points to.

### 20.3 Never infer execution state from files alone

Bad behavior:

```text
if Pipeline/Video Output/1x1/v001.mp4 exists:
    mark generation completed
```

Instead, reconcile explicitly:

```text
DB says Artifact available
+ physical object exists and matches expected identity
→ healthy
```

If one side is missing, expose a repairable consistency error.

### 20.4 Pipeline folder protection

The scanner must know that `Pipeline/` is system-owned and should not recursively discover its child directories as listing inputs.

Input Data may intentionally read selected source/reference folders, but downstream generated artifacts must not recursively become new creative inputs unless a future explicit feature enables that behavior.

---

## 21. UI placement and navigation

The new top-level tab appears in this order:

```text
Visual Search
Creative Pipeline
Inventory Daily
```

Reuse existing application layout and interaction conventions.

Do not create a visually unrelated standalone admin application.

---

## 22. Creative Pipeline main UI

The primary hierarchy is parent-group first.

Example:

```text
Etsy - EmbrolyShop
├── listing - 4527798886
└── listing - 4540775224
```

### 22.1 SourceGroup row/header

Example:

```text
Etsy - EmbrolyShop
32 listings · 4 running · 2 failed · 21 completed · 5 waiting
Last scan: 09:30

[ Scan now ] [ Retry failed ] [ ... ]
```

Groups should support collapse/expand.

### 22.2 Listing row

Keep rows compact enough for dozens or hundreds of listings.

Example:

```text
listing - 4527798886

Input     Idea     Prompt     Generate     Output     Enhance
  ✓         ✓        ✓           ●            3          ○

Etsy · 1:1 · 3 video versions
```

Amazon example:

```text
listing - B0ABC12345

Input     Idea     Prompt     Generate     Output     Enhance
  ✓         ✓        ✓           ●            5          3

Amazon · 16:9 + 9:16
```

### 22.3 Filters

Suggested controls:

```text
Search listing...

[ All ] [ Running ] [ Failed ] [ Waiting ] [ Completed ]

Group:    [ All shops ▾ ]
Platform: [ Amazon ] [ Etsy ]
```

### 22.4 Group actions

Potential actions:

```text
Scan now
Retry failed
Pause new work
Resume
```

Bulk actions must call canonical backend services and must never directly edit statuses in the UI.

---

## 23. Listing detail drawer

Clicking a listing should open a detail drawer rather than expanding the main row into a large workflow editor.

Suggested content:

```text
listing - 4527798886
Etsy · Etsy - EmbrolyShop

Source folder
Source/
UGC - Macro Vid/
Pipeline/

Current Run #4

Input Data
  Completed
  input_v003.json

Idea Story
  Completed
  idea_v004.json

Prompt
  Completed
  Seedance prompt_v006
  Google Omni prompt_v003

Generating
  Running
  3 completed / 1 running / 1 failed

Video Output
  v001
  v002
  v003

Watermark & Smart Enhance
  2 completed / 1 pending

Run History
Artifacts
Errors
```

Actions may include:

```text
Retry failed node
Regenerate Idea
Regenerate Prompt
Generate another video
Retry Enhance
Cancel pending work
```

Every action must preserve historical artifacts and lineage.

---

## 24. Backend API shape

Exact routes should follow current application conventions, but the semantic capabilities should include:

```text
GET  creative-pipeline/groups
GET  creative-pipeline/listings
GET  creative-pipeline/listings/{id}
GET  creative-pipeline/listings/{id}/runs
GET  creative-pipeline/runs/{id}
GET  creative-pipeline/runs/{id}/artifacts

POST creative-pipeline/groups/{id}/scan
POST creative-pipeline/listings/{id}/run
POST creative-pipeline/nodes/{id}/retry
POST creative-pipeline/listings/{id}/regenerate-idea
POST creative-pipeline/listings/{id}/regenerate-prompt
POST creative-pipeline/generations/{id}/retry
POST creative-pipeline/generations/{id}/cancel
POST creative-pipeline/artifacts/{id}/retry-enhance
```

Do not expose provider credentials or raw secret-bearing responses through APIs.

The server must enforce tenant/source authorization independently of UI visibility.

---

## 25. Idempotency

Idempotency is required at several levels.

### 25.1 Discovery

```text
source_group + external listing folder identity
```

must produce one stable ListingTask.

### 25.2 Initial pipeline creation

Repeated scanner runs must not create duplicate initial PipelineRuns.

### 25.3 Node execution

A durable queue retry must not create duplicate successful artifacts when an earlier attempt actually succeeded but acknowledgement failed.

Use node/run-specific idempotency keys.

### 25.4 Generation submission

Provider submit calls should use local durable state to avoid accidental duplicate remote jobs after timeout/restart where possible.

Persist provider request IDs before polling.

### 25.5 Artifact version

Version numbers must be reserved transactionally.

---

## 26. Concurrency and resource control

The pipeline will eventually process many listings simultaneously.

Support configurable capacity such as:

```text
OpenAI Idea concurrency
OpenAI Prompt concurrency
Seedance generation concurrency
Google Omni generation concurrency
Watermark/Enhance concurrency
per-source-group generation concurrency
global generation concurrency
```

Example:

```text
100 listings pending
        ↓
3 generation slots available
        ↓
workers claim only 3 generation jobs
```

Provider rate limits should map into retryable queue behavior, not uncontrolled immediate loops.

---

## 27. Knowledge/OpenAI reliability

Idea Story and Prompt calls should record safe operational metadata:

```text
model
request started/completed timestamps
latency
token usage if available
knowledge snapshot
schema version
attempt
normalized error code
```

Do not write API keys, authorization headers, or sensitive credentials into Pipeline artifact JSON/log files.

Structured-output validation failures should be retryable only within bounded policy.

---

## 28. Security and authorization

Every persisted domain row must be tenant-bound.

Every backend operation must verify:

```text
active tenant
source-group access
listing ownership/source relationship
requested run/node belongs to listing/tenant
```

Never trust arbitrary client-supplied provider paths as authorization proof.

Provider folder identifiers should be resolved through the existing authorized source/explorer infrastructure.

The system-owned `Pipeline/` folder must inherit the same source/provider authorization boundary as its listing folder.

---

## 29. Observability

Track operational metrics such as:

```text
source groups scanned
listings discovered/day
new ListingTasks/day
pipeline runs started/completed/failed
queue depth by node
oldest waiting job
node latency p50/p95
node failure rate
retry rate
OpenAI latency/error rate
Seedance latency/error rate
Google Omni latency/error rate
generation success rate
enhance success rate
artifacts produced/day
cost per listing/run when provider cost data exists
```

Use canonical error codes so failures can be grouped reliably.

Useful listing-level event history:

```text
listing discovered
Pipeline folder created
run started
node started
node retry scheduled
node completed
generation submitted
generation completed
artifact stored
enhance completed
run completed
```

---

## 30. Testing strategy

### 30.1 Folder scanner tests

Cover:

```text
new parent group
new listing folder
existing listing folder
duplicate daily scan
Pipeline folder ignored as listing
Source folder ignored as listing
UGC - Macro Vid ignored as listing
invalid listing folder naming
same listing_key in different groups
missing source folder
cross-tenant folder isolation
```

### 30.2 Orchestrator tests

Cover:

```text
node dependency ordering
successful progression
failed node blocks downstream work
retry only failed node
restart/resume
cancel
regenerate creates new version branch
successful upstream nodes reused
```

### 30.3 Idea/Prompt tests

Cover:

```text
correct Knowledge Pack selection
structured output validation
provider timeout
malformed output
retry
regenerate versioning
lineage to Input and Knowledge snapshot
```

### 30.4 Generation tests

Cover:

```text
Seedance adapter
Google Omni adapter
provider submit timeout
polling
provider failure normalization
retry
cancel
multiple model branches
Amazon dual ratios
Etsy 1:1
```

### 30.5 Artifact tests

Cover:

```text
version reservation
no overwrite
concurrent version creation
physical write failure
lineage preservation
raw video retained after enhance
```

### 30.6 Enhance tests

Cover:

```text
one raw video → one enhanced version
retry only failed artifact
multiple independent video versions
source raw artifact untouched
```

### 30.7 End-to-end test

Minimum V1 E2E scenario:

```text
create/discover parent folder
        ↓
create/discover listing folder
        ↓
scanner creates ListingTask
        ↓
Pipeline/ ensured
        ↓
Input Data completed
        ↓
Idea Story completed
        ↓
Prompt completed
        ↓
Generation completed
        ↓
raw output stored under Pipeline/Video Output
        ↓
Watermark & Smart Enhance completed
        ↓
final output stored under Pipeline/Watermark & Smart Enhance
        ↓
ListingTask current run = completed
```

Also test service restart between nodes to verify durable recovery.

---

## 31. Implementation roadmap

Implementation should proceed in bounded phases.

> **Roadmap authority:** the CP-01 through CP-16 sequence in the CP-00 normative contracts supersedes earlier broad phase numbering below; these retained sections are implementation detail references only.

### CP-00 — Architecture and contracts — COMPLETE

The normative source of truth is [CREATIVE_PIPELINE_CP00_CONTRACTS.md](CREATIVE_PIPELINE_CP00_CONTRACTS.md). It locks source-folder ownership, SourceGroup/Listing identity, `listing_key`, PlatformProfiles, node graph/state machine, durable retry/idempotency, versioning/artifact lineage, input/knowledge/Idea/Prompt contracts, provider shape, security, recovery, and open configuration decisions.

Acceptance:

```text
[x] Source/ and UGC - Macro Vid/ are explicitly user-owned.
[x] Pipeline/ is explicitly system-owned.
[x] Amazon/Etsy listing identifier semantics use listing_key.
[x] Node and artifact contracts are documented.
[x] CP-01 through CP-16 bounded phase map is locked in the normative contract.
```

### CP-01 — Domain model and migrations

Implement:

```text
SourceGroup
ListingTask
PipelineRun
NodeRun
GenerationRun
Artifact
```

Acceptance:

```text
[ ] tenant isolation constraints
[ ] stable listing identity
[ ] version uniqueness
[ ] required indexes
[ ] migration rollback verified
```

### CP-02 — Folder scanner and idempotent discovery

Implement:

```text
parent-group recognition
listing parser
daily/manual scan
ListingTask creation
Pipeline/ ensure/create
idempotency
missing-source lifecycle
```

Acceptance:

```text
[ ] new listing discovered once
[ ] repeated scan creates no duplicate task/run
[ ] Pipeline subfolders ignored as listings
[ ] user-managed folders untouched
```

### CP-03 — Orchestrator and retry engine

Implement:

```text
node state machine
dependency transitions
durable execution jobs
retry/backoff
manual retry
cancel
restart recovery
```

Acceptance:

```text
[ ] smallest failed unit retries
[ ] completed upstream work reused
[ ] process restart does not lose work
[ ] UI cannot directly mutate states
```

### CP-04 — Input Data and Artifact framework

Implement:

```text
input snapshot
artifact registry
physical Pipeline paths
version reservation
content hashes
artifact lineage
```

Acceptance:

```text
[ ] immutable input snapshot
[ ] no destructive overwrite
[ ] concurrent version reservation safe
[ ] DB ↔ physical artifact mapping deterministic
```

### CP-05 — Knowledge Pack

Implement:

```text
KnowledgeLoader
platform/node/model rule selection
knowledge manifests/hashes
run-level knowledge snapshot
```

Acceptance:

```text
[ ] deterministic knowledge selection
[ ] historical run can identify knowledge revision
[ ] no RAG dependency required for V1
```

### CP-06 — OpenAI Idea Story and Prompt

Implement:

```text
Idea Story structured output
Prompt structured output
Seedance prompt generation
Google Omni prompt generation
schema validation
retry/error handling
versioning
```

Acceptance:

```text
[ ] Idea Story persisted under Pipeline/Idea Story
[ ] prompts persisted under Pipeline/Prompt
[ ] retries do not silently create new creative versions
[ ] explicit regenerate creates new version
```

### CP-07 — Video generation adapters

Implement:

```text
Seedance provider adapter
Google Omni provider adapter
GenerationRun lifecycle
submit/poll/fetch/cancel
platform aspect-ratio fan-out
```

Acceptance:

```text
[ ] Etsy 1:1 works
[ ] Amazon 16:9 and 9:16 work
[ ] provider-specific errors normalized
[ ] duplicate submit risk bounded
```

### CP-08 — Video Output and Watermark/Smart Enhance

Implement:

```text
versioned raw output storage
Artifact registration
existing watermark-removal integration
Smart Enhance integration
artifact-level retry
final output storage
```

Acceptance:

```text
[ ] raw output never overwritten
[ ] final output linked to exact raw version
[ ] failed enhance retries only affected video
[ ] files remain under listing/Pipeline
```

### CP-09 — Backend APIs

Implement read/action APIs for:

```text
groups
listings
runs
nodes
generations
artifacts
scan
retry
regenerate
cancel
```

Acceptance:

```text
[ ] tenant authorization
[ ] no direct status mutation endpoint
[ ] safe error responses
[ ] no provider secrets exposed
```

### CP-10 — Creative Pipeline UI

Implement tab between:

```text
Visual Search
Creative Pipeline
Inventory Daily
```

Implement:

```text
grouped parent-folder view
compact listing pipeline rows
filters
status summaries
listing detail drawer
run history
artifact versions
retry/regenerate actions
```

Acceptance:

```text
[ ] UI style follows existing application patterns
[ ] Etsy - EmbrolyShop groups its listing tasks
[ ] pipeline node state visible without opening drawer
[ ] version/history accessible from detail drawer
```

### CP-11 — Scheduler, observability, E2E hardening and rollout

Implement:

```text
durable daily schedule
manual scan now
provider concurrency controls
metrics
operational diagnostics
full E2E tests
canary rollout controls
```

Acceptance:

```text
[ ] daily scan durable
[ ] queue capacity bounded
[ ] retries observable
[ ] complete listing E2E test passes
[ ] rollout can start with one group/listing
```

---

## 32. Recommended rollout sequence

Do not enable broad automated generation immediately.

Recommended rollout:

```text
1. scanner dry run
2. one Etsy SourceGroup
3. one newly detected listing
4. Input Data only
5. Idea Story + Prompt
6. one generation provider
7. one 1:1 raw output
8. Watermark & Smart Enhance
9. verify physical Pipeline folder and DB lineage
10. several listings
11. one complete shop/group
12. enable daily scheduled discovery
13. add Amazon dual-ratio workload
14. broader rollout
```

Production rollout must have an immediate way to pause new pipeline work without deleting existing state or artifacts.

---

## 33. V1 definition of done

Creative Pipeline V1 is complete when all of the following are true:

```text
[ ] Creative Pipeline tab exists between Visual Search and Inventory Daily.
[ ] Parent source folders are displayed as groups.
[ ] Listing tasks appear under their real parent folder.
[ ] Existing Source/ and UGC - Macro Vid/ content is preserved.
[ ] Pipeline/ is the only system-owned artifact folder inside each listing.
[ ] Daily scanner discovers new listing folders idempotently.
[ ] Existing listing folders do not create duplicate ListingTasks.
[ ] listing_key supports Etsy Listing ID and Amazon ASIN semantics.
[ ] Input Data creates immutable snapshots.
[ ] Idea Story uses OpenAI + selected Knowledge Pack.
[ ] Prompt generates Seedance 2.5 and Google Omni prompt artifacts.
[ ] Etsy generates 1:1 outputs.
[ ] Amazon generates 16:9 and 9:16 outputs.
[ ] Every raw video output is versioned and kept under Pipeline/Video Output.
[ ] Watermark & Smart Enhance outputs are versioned separately.
[ ] Retry exists for every executable node.
[ ] Retry does not rerun successful upstream nodes.
[ ] Regenerate creates a new version/branch and preserves history.
[ ] DB state is authoritative for workflow state.
[ ] Physical artifacts can be traced back to exact input/idea/prompt/generation lineage.
[ ] Worker restart does not lose pipeline state.
[ ] Tenant/source authorization is enforced end to end.
[ ] Provider credentials are never persisted in artifacts/logs.
[ ] Queue/provider concurrency is bounded.
[ ] Full end-to-end tests pass.
[ ] Canary rollout succeeds before broad scheduled automation is enabled.
```

---

## 34. Key architectural rules

The following rules should be treated as invariants during implementation:

1. **Do not modify user-owned source/reference folders destructively.**
2. **All generated/system artifacts live under the listing's `Pipeline/` folder.**
3. **Database state, not folder existence, is authoritative for execution state.**
4. **Use `listing_key`, not universal `asin`, as marketplace-neutral identity.**
5. **A listing is not the same thing as a PipelineRun.** One listing can have many historical runs.
6. **Retry is not regenerate.** Retry repeats the same logical version; regenerate creates a new version.
7. **Never overwrite successful historical artifacts.**
8. **One orchestrator owns node dependency transitions.**
9. **Provider adapters own provider protocol, not workflow state.**
10. **Daily scans are idempotent.**
11. **`Pipeline/` contents are ignored by listing discovery.**
12. **Physical output version allocation must be concurrency safe.**
13. **All operations remain tenant/source authorized.**
14. **Scheduled automation is durable and resumable.**
15. **Start simple with deterministic Knowledge Pack loading; add RAG only when justified by scale.**

This is the source-of-truth implementation direction for the initial Creative Pipeline unless later approved requirements explicitly change it.
