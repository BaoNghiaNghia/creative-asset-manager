# Creative Pipeline — Listing-to-Video Automation Plan

> **Repository:** `BaoNghiaNghia/creative-asset-manager`  
> **Document date:** 2026-09-15  
> **Status:** Planning document for implementation. This document does **not** authorize production deployment, destructive source-folder operations, provider credential changes, or bulk generation runs.  
> **Feature name:** `Creative Pipeline`  
> **Navigation target:** place the new tab between **Visual Search** and **Inventory Daily** and reuse the visual language, spacing, controls, drawers, filters, status chips, and interaction patterns already established elsewhere in the application.

---

## 1. Why this plan exists

The goal is to turn a structured source-folder hierarchy into a durable, retryable creative-production pipeline.

Each product/listing is represented by a folder named in the form:

```text
listing - ASIN
```

Those listing folders live under a parent folder representing a shop/store/source group, for example:

```text
Etsy - Shop A
├── listing - ASIN 1
├── listing - ASIN 2
└── listing - ASIN 3

Amazon - Store B
├── listing - ASIN 4
└── listing - ASIN 5
```

The system should scan configured source roots every day, detect newly added listing folders, create a durable listing task only once, and run the listing through a node-based production pipeline.

The initial pipeline is:

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

Every node must be independently retryable and version-aware. A failed downstream step must not force successful upstream work to run again unless the operator explicitly chooses to regenerate it.

The pipeline must support two initial commerce profiles:

```text
Amazon
├── 16:9 video output
└── 9:16 video output

Etsy
└── 1:1 video output
```

The design should remain extensible enough to later support image generation, approval, scoring, publishing, additional generative models, and other output channels without redesigning the core execution model.

---

## 2. Product goals

The first production-ready version should make the following workflow possible:

```text
Configured source folders
        ↓
Scheduled daily scan
        ↓
Detect new listing - ASIN folders
        ↓
Create ListingTask once
        ↓
Capture input data
        ↓
Generate Idea Story with OpenAI + Knowledge Pack
        ↓
Generate model-specific prompts with OpenAI + Knowledge Pack
        ↓
Generate one or more videos
        ↓
Store every video version inside the listing folder
        ↓
Remove watermark / Smart Enhance
        ↓
Produce final versioned artifacts
```

The operator should be able to answer, from one UI:

- Which shops/groups contain active listing tasks?
- Which listing folders were newly discovered today?
- What step is each listing currently on?
- Which node failed and why?
- How many retries have occurred?
- Which Idea Story and Prompt version produced a given video?
- Which video versions exist for each aspect ratio/model?
- Which version has completed watermark removal and enhancement?
- Which work is queued, running, blocked, failed, or complete?

---

## 3. Non-goals for V1

The first implementation should not attempt to solve everything at once.

Out of scope for the initial release:

- automatic publishing to Amazon or Etsy;
- automatic ad launch or campaign management;
- automatic human-quality approval using a vision model;
- cross-listing prompt optimization based on conversion data;
- vector-database/RAG infrastructure unless the Knowledge Pack becomes too large for deterministic file selection;
- automatic deletion or moving of source listing folders;
- destructive overwrite of generated videos;
- shared generation state across unrelated listings;
- full workflow editor where users can arbitrarily add/remove nodes;
- provider-agnostic marketplace parsing for every marketplace.

The architecture should allow these later, but V1 should remain opinionated and operationally predictable.

---

## 4. Core domain hierarchy

The domain should have four primary levels:

```text
SourceGroup
    ↓
ListingTask
    ↓
PipelineRun
    ↓
NodeRun / GenerationRun / Artifact
```

### 4.1 SourceGroup

Represents the real parent folder and business grouping.

Examples:

```text
Etsy - Shop A
Amazon - Store B
```

Recommended fields:

```text
id
platform                amazon | etsy
name
source_path
source_provider
external_source_id
external_folder_id
active
scan_enabled
scan_schedule
timezone
created_at
updated_at
last_scan_at
last_successful_scan_at
```

The parent group should be persisted as domain state rather than derived only at render time by splitting a path string.

### 4.2 ListingTask

Represents one discovered listing folder.

Recommended fields:

```text
id
source_group_id
platform
listing_key
asin
folder_name
folder_path
external_folder_id
status
current_pipeline_run_id
first_discovered_at
last_seen_at
created_at
updated_at
```

A listing folder should map to one stable `ListingTask`.

A listing may have many pipeline runs over time.

### 4.3 PipelineRun

Represents one intentional production attempt/version branch for a listing.

Recommended fields:

```text
id
listing_task_id
run_number
status
trigger_type            discovery | manual | regenerate | retry_branch
triggered_by
knowledge_snapshot_id
started_at
completed_at
created_at
updated_at
```

One `ListingTask` can therefore retain history:

```text
listing - ASIN 1
├── Pipeline Run #1
├── Pipeline Run #2
└── Pipeline Run #3
```

### 4.4 NodeRun

Represents one execution of one pipeline node.

Recommended fields:

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
last_error_code
last_error_message
next_retry_at
created_at
updated_at
```

Suggested node types:

```text
input_data
idea_story
prompt
video_generation
video_output
watermark_smart_enhance
```

### 4.5 GenerationRun

Generation should be first-class because one prompt can produce many video outputs.

Recommended fields:

```text
id
pipeline_run_id
prompt_artifact_id
model_provider
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

### 4.6 Artifact

Artifacts connect the lineage of the system.

Examples:

```text
input snapshot
idea story JSON
Seedance prompt
Google Omni prompt
raw generated video
enhanced video
render metadata
provider response metadata
```

Recommended fields:

```text
id
listing_task_id
pipeline_run_id
node_run_id
artifact_type
version
model_provider
aspect_ratio
storage_kind
relative_path
content_hash
mime_type
size_bytes
metadata_json
created_at
```

The relationship between artifacts should allow the UI to answer:

> Which exact idea/prompt produced this video version?

---

## 5. Folder discovery and daily scanning

### 5.1 Source folder model

The scanner operates on configured source roots containing parent groups and listing folders.

Conceptually:

```text
Source root
├── Etsy - Shop A
│   ├── listing - ASIN 1
│   └── listing - ASIN 2
│
└── Amazon - Store B
    ├── listing - ASIN 3
    └── listing - ASIN 4
```

### 5.2 Discovery schedule

The default is one scheduled scan per day.

The design should also allow:

```text
manual Scan now
scheduled daily scan
future webhook/event-triggered discovery
```

The scheduler should create a durable scan job rather than relying on an in-process timer only.

### 5.3 Listing folder recognition

V1 should recognize listing folders using a strict, configurable convention.

Initial convention:

```text
listing - <ASIN>
```

Parsing should be normalized and deterministic.

Examples:

```text
listing - B0ABC123
listing - ABCD-001
```

The system should avoid accepting arbitrary nested folders as listing tasks.

### 5.4 Idempotency

Daily scans must never duplicate a listing task.

Recommended discovery identity:

```text
source_group_id + external_folder_id
```

If a stable provider folder ID is unavailable, fallback identity may be:

```text
source_group_id + normalized_folder_path
```

`ASIN` alone should not be the only identity because the same ASIN may exist in multiple shops or source groups.

### 5.5 Scan behavior

For each scan:

```text
load configured SourceGroups
        ↓
list direct/allowed listing folders
        ↓
normalize listing identity
        ↓
existing ListingTask?
    ├── yes → update last_seen_at / metadata only
    └── no  → create ListingTask + initial PipelineRun
```

A scan must not automatically create a second run for an already-known listing unless a separate regeneration policy explicitly requires it.

### 5.6 Missing folders

If a previously known folder disappears, V1 should not immediately delete the listing or its history.

Prefer a lifecycle such as:

```text
active
missing_source
archived
```

This prevents temporary provider/source failures from destroying workflow history.

---

## 6. Platform profiles and output contracts

Platform-specific behavior should be represented as configuration, not spread throughout node implementations.

### 6.1 Amazon profile

Required initial output variants:

```text
16:9
9:16
```

A single listing pipeline may therefore create multiple generation branches from the same Idea Story/Prompt stage.

Example:

```text
Prompt v3
├── Seedance 2.5 / 16:9 / generation 001
├── Seedance 2.5 / 16:9 / generation 002
├── Seedance 2.5 / 9:16 / generation 001
└── Google Omni / 9:16 / generation 001
```

### 6.2 Etsy profile

Required initial output:

```text
1:1
```

### 6.3 Future profile model

A platform profile should eventually hold:

```text
required_aspect_ratios
target_duration_rules
model_preferences
prompt_rules
output_count
naming rules
watermark/enhancement policy
```

This prevents marketplace rules from becoming hard-coded conditionals in every node.

---

## 7. Pipeline execution model

The pipeline is sequential at the logical level but can fan out at generation time.

```text
Input Data
    ↓
Idea Story
    ↓
Prompt
    ↓
Generating
    ├── model A / ratio 1
    ├── model A / ratio 2
    └── model B / ratio 1
            ↓
       Video Output
            ↓
Watermark & Smart Enhance
```

### 7.1 Recommended execution substrate

Reuse the application's durable job-queue concepts for execution rather than creating a second unrelated queue system.

Domain tables should represent creative workflow state, while durable processing jobs should execute node work.

Conceptually:

```text
Creative domain state
    ListingTask / PipelineRun / NodeRun
                ↓
Durable processing queue
                ↓
Worker claims node execution
                ↓
Artifact/result persisted
                ↓
Next node becomes runnable
```

### 7.2 Node dependencies

A node should become runnable only when all required upstream outputs are complete.

Example:

```text
Prompt cannot run until Idea Story completes.
Generating cannot run until Prompt completes.
Enhance cannot run until a raw video artifact exists.
```

The dependency logic should live in one orchestration service, not duplicated across workers and UI code.

### 7.3 Node states

Recommended canonical states:

```text
pending
ready
running
retry_wait
completed
failed
cancelled
blocked
```

The UI can simplify these into user-facing categories:

```text
Waiting
Running
Retrying
Completed
Failed
Blocked
```

### 7.4 Listing-level state

`ListingTask.status` should be derived from the current pipeline run rather than independently editable.

Example priority:

```text
failed if an unrecoverable active node failed
running if any node is running
retrying if any node is waiting for retry
queued if runnable work is waiting
completed if all required terminal outputs are complete
blocked if required operator input is missing
```

---

## 8. Retry semantics

Retry is a core requirement and should exist at every node.

### 8.1 Retry granularity

Retry the smallest failed unit.

Examples:

```text
Idea Story failed
→ retry Idea Story only

Seedance generation 16:9 failed
→ retry that GenerationRun only

Smart Enhance failed for v003
→ retry enhancement for v003 only
```

Successful upstream work should be reused.

### 8.2 Automatic retry

Each node should define:

```text
max_attempts
retryable_error_codes
retry_backoff
retry_jitter
```

Transient errors may retry automatically.

Examples:

```text
provider busy
HTTP 429
HTTP 5xx
timeout
temporary network failure
```

Permanent validation errors should fail immediately.

Examples:

```text
missing required input
invalid output contract
unsupported format
invalid provider configuration
```

### 8.3 Manual retry

UI should expose:

```text
Retry failed node
Retry failed generation
Retry failed items in group
```

Manual retry should use the same canonical retry service as automatic retry, not direct DB status edits.

### 8.4 Retry versus regenerate

These actions must remain different.

```text
Retry
→ same logical input/version
→ another attempt to obtain the same intended result

Regenerate
→ creates a new output version / branch
→ preserves previous successful output
```

This distinction is especially important for Idea Story, Prompt, and Video Generation.

---

## 9. Input Data node

The Input Data node creates a stable snapshot used by downstream AI nodes.

It should gather only the listing data required for creative production.

Possible inputs:

```text
listing folder identity
platform
ASIN/listing key
source group/shop
product images
existing videos
listing title
folder notes
selected metadata
product description if available
operator notes
required aspect ratios
```

The node should persist an immutable snapshot artifact for that pipeline run.

Example:

```text
pipeline/input/input_v001.json
```

Downstream nodes should read the snapshot instead of repeatedly reading mutable listing state.

This makes a run reproducible.

---

## 10. Idea Story node

### 10.1 Purpose

Turn raw listing context into a structured creative concept.

The OpenAI call should not directly return final model prompts.

Instead, it should return a structured intermediate representation.

Suggested contract:

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

A typed JSON contract is preferable to free-form prose because Prompt generation can then be deterministic and testable.

### 10.2 Versioning

Every intentional rerun creates a new version:

```text
idea_story_v001.json
idea_story_v002.json
idea_story_v003.json
```

Retrying a failed API call does not necessarily create a new creative version; explicit `Regenerate Idea` does.

---

## 11. Prompt node

### 11.1 Purpose

Transform one Idea Story into provider/model-specific generation prompts.

Initial targets:

```text
Seedance 2.5
Google Omni
```

Prompt generation should be provider-specific because model prompting conventions differ.

### 11.2 Prompt artifact contract

Suggested structure:

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
      "duration_seconds": 10,
      "prompt": "...",
      "negative_constraints": ["..."],
      "metadata": {}
    },
    {
      "provider": "seedance",
      "model": "seedance-2.5",
      "aspect_ratio": "9:16",
      "duration_seconds": 10,
      "prompt": "..."
    }
  ]
}
```

Google Omni prompts should have their own typed payload rather than forcing them into Seedance-specific fields.

### 11.3 Prompt history

Never overwrite previous prompt versions.

The UI should allow the operator to inspect:

```text
Prompt v1
Prompt v2
Prompt v3 (current)
```

and see which generation runs each version produced.

---

## 12. Knowledge Pack architecture

Idea Story and Prompt both need reusable creative knowledge.

V1 should begin with deterministic files rather than immediately introducing a vector database.

Recommended layout:

```text
knowledge/
├── manifest.yaml
├── global_rules.md
├── idea_story_rules.md
├── hooks_library.md
├── amazon_rules.md
├── etsy_rules.md
├── seedance_2_5_rules.md
├── google_omni_rules.md
├── safety_and_quality.md
└── examples/
    ├── amazon_examples.md
    └── etsy_examples.md
```

### 12.1 Manifest

The manifest defines which knowledge sections belong to each task.

Example concept:

```yaml
idea_story:
  common:
    - global_rules.md
    - idea_story_rules.md
    - hooks_library.md
  amazon:
    - amazon_rules.md
  etsy:
    - etsy_rules.md

prompt:
  seedance-2.5:
    - global_rules.md
    - seedance_2_5_rules.md
  google-omni:
    - global_rules.md
    - google_omni_rules.md
```

### 12.2 KnowledgeLoader

A dedicated loader should:

```text
receive task + platform + model
        ↓
load approved files from manifest
        ↓
normalize content
        ↓
build context in deterministic order
        ↓
return knowledge bundle + content hashes
```

### 12.3 Knowledge snapshots

A pipeline run should record exactly which knowledge version it used.

Recommended snapshot metadata:

```text
knowledge files
file hashes
combined hash
manifest version
created_at
```

This solves an important reproducibility problem:

> Why did yesterday's prompt differ from today's prompt even though the listing did not change?

### 12.4 Future RAG evolution

Only introduce embedding/RAG when the knowledge corpus becomes too large or retrieval quality requires it.

Future architecture may become:

```text
Knowledge Pack files
      ↓
chunk/index
      ↓
retrieval policy
      ↓
relevant knowledge snippets
      ↓
OpenAI
```

The persisted `knowledge_snapshot_id` should remain useful even after this transition.

---

## 13. OpenAI integration

OpenAI is used initially for:

```text
Idea Story
Prompt generation
```

### 13.1 Structured output

Prefer typed/structured JSON output for both stages.

Do not make downstream code parse arbitrary Markdown when a schema can express the result.

### 13.2 Input boundaries

OpenAI input should be assembled from:

```text
system/task contract
+ selected Knowledge Pack
+ platform profile
+ immutable listing input snapshot
+ upstream Idea Story when generating prompts
```

### 13.3 Audit metadata

Persist operational metadata without storing secrets:

```text
model
request timestamp
latency
response status
usage metadata if available
input artifact IDs
knowledge snapshot ID
output artifact ID
```

Never persist API keys or authorization headers in node metadata/logs.

### 13.4 Provider failures

Provider errors should be normalized to internal error classes such as:

```text
rate_limited
provider_busy
timeout
invalid_request
invalid_response
authentication_error
content_rejected
unknown_provider_error
```

This allows consistent retry policy and UI messaging.

---

## 14. Generating node

### 14.1 Provider adapters

Generation should use an adapter interface.

Conceptually:

```text
VideoGenerationProvider
├── SeedanceProvider
└── GoogleOmniProvider
```

The orchestration layer should not contain provider-specific HTTP details.

A provider adapter should support a lifecycle similar to:

```text
submit generation
poll/check generation
retrieve output metadata
cancel if supported
normalize provider error
```

### 14.2 Fan-out

The Prompt artifact determines generation branches.

For Amazon:

```text
Prompt
├── Seedance 2.5 / 16:9
├── Seedance 2.5 / 9:16
├── Google Omni / 16:9 (if enabled)
└── Google Omni / 9:16 (if enabled)
```

For Etsy:

```text
Prompt
├── Seedance 2.5 / 1:1
└── Google Omni / 1:1 (if enabled)
```

Provider/model enablement should be configurable.

### 14.3 Concurrency

The queue should enforce bounded concurrency per provider/model to avoid rate-limit storms.

Possible policy dimensions:

```text
global generation concurrency
provider concurrency
tenant concurrency
source group concurrency
```

V1 should keep these configurable rather than hardcoded.

---

## 15. Video Output and versioning

Generated files must be written inside the corresponding listing folder.

A recommended structure is:

```text
listing - ASIN/
├── input/
│   └── input_v001.json
│
├── pipeline/
│   ├── ideas/
│   │   ├── idea_v001.json
│   │   └── idea_v002.json
│   └── prompts/
│       ├── prompt_v001.json
│       └── prompt_v002.json
│
├── output/
│   ├── 16x9/
│   │   ├── seedance/
│   │   │   ├── v001.mp4
│   │   │   └── v002.mp4
│   │   └── google-omni/
│   │       └── v001.mp4
│   ├── 9x16/
│   │   └── ...
│   └── 1x1/
│       └── ...
│
└── enhanced/
    ├── 16x9/
    ├── 9x16/
    └── 1x1/
```

The exact layout may be simplified during implementation, but the following rules are mandatory:

1. generated files remain associated with the listing folder;
2. retries/regenerations must not overwrite previous successful versions;
3. file naming must be deterministic and collision-safe;
4. every file must map back to a persisted Artifact record;
5. writing must be atomic where possible;
6. partial downloads/renders must not appear as successful final artifacts.

### 15.1 Version identity

Suggested naming:

```text
v001.mp4
v002.mp4
v003.mp4
```

The database, not filename parsing, remains the canonical source of generation lineage.

---

## 16. Watermark & Smart Enhance node

This node receives one raw video artifact at a time.

Conceptual flow:

```text
Raw generated video
      ↓
Watermark removal
      ↓
Smart Enhance
      ↓
optional speed/format/render policy
      ↓
Final enhanced artifact
```

This should be implemented as an independently retryable node/run.

A failure processing `v003` must not reprocess `v001` or `v002` unnecessarily.

Recommended metadata:

```text
input_artifact_id
output_artifact_id
processing_profile
watermark_method
enhance_method
input_resolution
output_resolution
duration_in
duration_out
processing_duration_ms
```

The node should preserve the raw generated artifact even after a final enhanced version succeeds.

---

## 17. File-system/provider write safety

Because output is written back into source listing folders, write semantics need explicit safeguards.

### 17.1 Never overwrite source media by default

Generated/enhanced outputs must go into owned pipeline subfolders.

Do not modify arbitrary user/source input files.

### 17.2 Atomic output publication

Prefer:

```text
write/download to temporary file
        ↓
validate file exists + expected size/format
        ↓
rename/move to final version path
        ↓
create/finalize Artifact record
```

### 17.3 Remote provider destinations

If source folders can live on connected Drive/SharePoint/etc., the implementation should separate:

```text
logical artifact path
from
provider write adapter
```

This prevents generation logic from depending on local filesystem details.

### 17.4 Output collision

Version assignment must be serialized/idempotent so two workers cannot both claim `v005.mp4`.

Prefer DB-backed version allocation or deterministic GenerationRun identity.

---

## 18. Idempotency and duplicate prevention

Idempotency is required at several layers.

### 18.1 Discovery

One source listing folder -> one `ListingTask`.

### 18.2 Pipeline run

A discovery event should not create duplicate active runs for the same newly discovered listing.

### 18.3 Node execution

One NodeRun attempt should have a stable processing-job idempotency key.

Concept:

```text
creative-node:<node_run_id>:attempt:<attempt_number>
```

### 18.4 Generation

Generation submission must avoid accidental double-submit when the worker crashes after provider acceptance but before local persistence.

Persist provider request IDs and use provider-side idempotency where available.

### 18.5 Artifact creation

Artifact records should have a uniqueness strategy around logical generation/output identity so retries cannot register duplicate successful outputs accidentally.

---

## 19. UI placement and navigation

The new top-level tab should be inserted:

```text
Visual Search
Creative Pipeline
Inventory Daily
```

The feature should reuse existing application design conventions instead of introducing a separate design system.

Reuse where appropriate:

```text
page spacing
panel/card styling
search/filter controls
status pills
buttons
menus
loading skeletons
error states
right-side detail drawers
confirmation patterns
empty states
```

---

## 20. Main Creative Pipeline UI

### 20.1 Page header

Suggested controls:

```text
Creative Pipeline

[ Search listing... ]
[ All statuses ▾ ]
[ All platforms ▾ ]
[ All groups ▾ ]

[ Scan now ]
```

Optional summary counters:

```text
Waiting
Running
Retrying
Failed
Completed
```

### 20.2 Parent-folder grouping

Listing tasks must be grouped under their real parent `SourceGroup`.

Example:

```text
▼ Etsy - Shop A
  12 listings · 2 running · 1 failed · 8 completed · 1 waiting

    listing - ASIN 1
    listing - ASIN 2
    listing - ASIN 3

▶ Amazon - Store B
  24 listings · 5 running · 19 completed
```

Each group should support collapse/expand.

Group header may expose:

```text
Scan now
Retry failed
More
```

Bulk actions should always use canonical service APIs and never directly rewrite job state.

### 20.3 Listing row

Keep the listing view compact enough for large shops.

Example:

```text
listing - ASIN 1      Etsy · 1:1

Input     Idea     Prompt     Generate     Output     Enhance
  ✓         ✓         ✓          ◉            3          ○

Running · Generation 2/3
```

For Amazon:

```text
Amazon · 16:9 + 9:16
```

The row may also show:

```text
current run number
video version count
retry count
last update
```

### 20.4 Status visualization

Recommended node states:

```text
✓ completed
◉ running
↻ retrying
! failed
○ waiting
— not applicable
```

Use the application's existing icon/color semantics where available.

---

## 21. Listing detail drawer

Clicking a listing should open a detail drawer rather than navigating away unnecessarily.

Suggested structure:

```text
listing - ASIN 1
Etsy - Shop A

Current Run #4

1. Input Data
   Completed
   input_v001.json

2. Idea Story
   Completed
   idea_v002.json
   [ View ] [ Regenerate ]

3. Prompt
   Completed
   Seedance v003
   Google Omni v002
   [ View ] [ Regenerate ]

4. Generating
   Running
   Seedance / 1:1 / Attempt 2

5. Video Output
   v001.mp4
   v002.mp4
   v003.mp4

6. Watermark & Smart Enhance
   2 completed · 1 pending
```

Tabs/sections in the drawer can include:

```text
Current Run
Artifacts
Run History
Errors
```

### 21.1 Run history

Operators need to see previous runs without losing current context.

Example:

```text
Run #4   Running      Today 13:42
Run #3   Completed    Sep 14
Run #2   Failed       Sep 13
Run #1   Completed    Sep 12
```

---

## 22. UI actions and semantics

### Listing actions

```text
Open details
Retry failed node
Regenerate Idea Story
Regenerate Prompt
Generate another video
Restart as new pipeline run
Cancel current run
```

### Generation actions

```text
Retry
Generate another version
Open output
Run Enhance again
```

### Group actions

```text
Scan now
Retry failed listings
Pause group processing (future/optional)
```

Actions must distinguish clearly between retry and regeneration.

---

## 23. API surface — proposed

Exact paths should follow existing repository router conventions.

A possible API surface:

```text
GET  /api/v1/creative-pipeline/groups
POST /api/v1/creative-pipeline/groups/{group_id}/scan

GET  /api/v1/creative-pipeline/listings
GET  /api/v1/creative-pipeline/listings/{listing_id}

POST /api/v1/creative-pipeline/listings/{listing_id}/runs
GET  /api/v1/creative-pipeline/listings/{listing_id}/runs
GET  /api/v1/creative-pipeline/runs/{run_id}

POST /api/v1/creative-pipeline/node-runs/{node_run_id}/retry
POST /api/v1/creative-pipeline/runs/{run_id}/regenerate-idea
POST /api/v1/creative-pipeline/runs/{run_id}/regenerate-prompt

GET  /api/v1/creative-pipeline/runs/{run_id}/artifacts
POST /api/v1/creative-pipeline/generations/{generation_id}/retry
POST /api/v1/creative-pipeline/artifacts/{artifact_id}/enhance
```

The backend should return typed state, not force the client to infer workflow status from raw processing jobs.

---

## 24. Suggested service boundaries

A clean backend decomposition may look like:

```text
creative_pipeline/
├── contracts.py
├── model.py
├── repository.py
├── router.py
├── service.py
├── scanner.py
├── orchestrator.py
├── knowledge.py
├── idea_story.py
├── prompt_builder.py
├── generation/
│   ├── base.py
│   ├── seedance.py
│   └── google_omni.py
├── artifacts.py
└── retry.py
```

This is a conceptual layout; implementation should align with actual repository conventions after code inspection.

Responsibilities:

```text
scanner.py
    discover groups/listings

orchestrator.py
    transition pipeline/node state and release runnable work

knowledge.py
    load/version Knowledge Pack

idea_story.py
    OpenAI structured Idea Story generation

prompt_builder.py
    OpenAI model-specific prompt generation

generation/*
    provider API adapters

artifacts.py
    versioning, paths, lineage, output finalization

retry.py
    canonical retry policy
```

---

## 25. Database design — recommended

Implementation should inspect the current database schema and naming conventions first, but the domain likely requires persistent tables equivalent to:

```text
creative_source_groups
creative_listing_tasks
creative_pipeline_runs
creative_node_runs
creative_generation_runs
creative_artifacts
creative_knowledge_snapshots
creative_scan_runs
```

### 25.1 Why domain tables are needed

Processing jobs alone should not be the entire product model.

The durable job queue answers:

> What executable work is queued/running/failed?

Creative domain tables answer:

> What listing/run/version/artifact exists and how are they related?

Keep those responsibilities separate.

### 25.2 Constraints

Important uniqueness constraints should include equivalents of:

```text
SourceGroup source identity unique within tenant
ListingTask source folder identity unique within SourceGroup
PipelineRun run_number unique within ListingTask
Artifact logical version unique within relevant run/type/model/ratio
GenerationRun generation_number unique within prompt/model/ratio branch
```

Every row must be tenant-bound according to the repository's existing tenancy model.

---

## 26. Scheduling and queue behavior

### 26.1 Daily scanner

The scanner should be a durable scheduled job.

Suggested process:

```text
scheduler
    ↓
create creative_scan job for SourceGroup
    ↓
worker scans provider folder
    ↓
upsert discovery state
    ↓
new listings create initial pipeline runs
```

### 26.2 Pipeline scheduling

Do not enqueue every future node immediately.

Prefer:

```text
node completes successfully
        ↓
orchestrator evaluates dependencies
        ↓
next node(s) marked ready
        ↓
processing job created idempotently
```

This makes retries and branching easier to reason about.

### 26.3 Worker ownership

Long provider generation operations should not hold a short HTTP request open.

API actions should create/update durable workflow intent and return promptly.

Workers perform long-running work.

---

## 27. Error handling

Errors should be normalized by stage.

Suggested categories:

```text
source_unavailable
source_permission_denied
invalid_listing_folder
input_missing
knowledge_invalid
openai_rate_limited
openai_invalid_response
generation_rate_limited
generation_provider_busy
generation_failed
generation_timeout
artifact_download_failed
artifact_write_failed
watermark_failed
enhance_failed
render_failed
```

Each NodeRun should retain:

```text
last_error_code
safe operator-facing message
attempt_count
last_failed_at
```

Do not expose tokens, signed URLs, raw provider authorization payloads, or secret request content in UI/logging.

---

## 28. Observability and operations

Track aggregate operational metrics such as:

```text
listing folders discovered per scan
new ListingTasks created
pipeline runs started/completed/failed
node success/failure by node type
retry counts
OpenAI latency/error rate
generation provider latency/error rate
generation queue depth
videos generated by provider/aspect ratio
enhancement latency/error rate
end-to-end listing completion time
```

Useful operational views:

```text
currently running
retry waiting
failed by node
oldest pending work
provider outage symptoms
```

Avoid high-cardinality labels containing ASIN/path where the metrics platform cannot safely handle them.

---

## 29. Permissions

The first implementation should reuse existing application authorization patterns.

At minimum separate capabilities for:

```text
view Creative Pipeline
trigger scan
retry node
regenerate creative content
cancel run
manage SourceGroup configuration
manage Knowledge Pack (future/admin)
```

A normal viewer should not gain source-folder write access merely because the Creative Pipeline tab exists.

Output write actions must remain tenant/source authorized.

---

## 30. Security and data handling

Required principles:

1. Never persist provider/OpenAI API secrets in pipeline artifacts or logs.
2. Never send unrelated tenant resources to OpenAI/generation providers.
3. Build AI input from the listing's authorized immutable snapshot only.
4. Keep all DB reads/writes tenant-bound.
5. Validate output paths so a crafted listing name cannot escape the listing folder.
6. Sanitize filenames and reject `..`, absolute-path injection, and invalid provider paths.
7. Do not expose raw knowledge files to users without the appropriate permission.
8. Treat generated media as untrusted input before enhancement/render tooling.
9. Bound downloads, duration, resolution, and file sizes according to processing policy.

---

## 31. Concurrency and race conditions

The design must explicitly handle races.

### 31.1 Concurrent scans

Two scans discovering the same listing must still create only one `ListingTask`.

Use DB uniqueness + idempotent create semantics.

### 31.2 Concurrent retries

Two operators clicking Retry should not create duplicate attempts for the same active NodeRun.

### 31.3 Concurrent generation completion

Provider callbacks/polls can race with worker retries.

Generation state transitions must be monotonic/idempotent.

### 31.4 Output version allocation

Two generations finishing simultaneously must not write to the same output version path.

Use persistent version identity, not "list files and choose next number" as the only locking strategy.

---

## 32. State transition rules

Recommended high-level transition constraints:

```text
pending -> ready
ready -> running
running -> completed
running -> retry_wait
running -> failed
retry_wait -> ready
pending/ready/running/retry_wait -> cancelled
```

A completed NodeRun should not return to running.

Explicit regeneration creates a new version/new NodeRun rather than reopening a completed historical run.

This immutable-history bias makes auditability substantially easier.

---

## 33. Artifact lineage example

One complete branch may look like:

```text
ListingTask: listing - B0ABC123

PipelineRun #3
│
├── Input Artifact v1
│
├── Idea Story v2
│
├── Prompt v4
│   └── Seedance 2.5 / 9:16
│
├── GenerationRun #1
│   └── raw video v001.mp4
│
├── GenerationRun #2
│   └── raw video v002.mp4
│
└── Enhance
    ├── v001 -> enhanced v001.mp4
    └── v002 -> enhanced v002.mp4
```

The UI should be able to traverse this lineage in both directions.

---

## 34. Example end-to-end Amazon flow

```text
Amazon - Store B
└── listing - B0ABC123
```

1. Daily scan sees the folder for the first time.
2. `ListingTask` is created.
3. Initial `PipelineRun #1` is created.
4. Input Data captures product/listing assets and metadata.
5. Idea Story uses OpenAI + global + Amazon knowledge.
6. Prompt uses Idea Story + Seedance/Google Omni knowledge.
7. Generation fans out to required `16:9` and `9:16` branches.
8. Each successful generation becomes a versioned raw Artifact in the listing folder.
9. Each raw Artifact independently enters Watermark & Smart Enhance.
10. Enhanced artifacts are stored under the listing folder.
11. Listing becomes `completed` when all required output branches satisfy the platform completion policy.

If one `9:16` generation fails:

```text
16:9 successful artifacts remain untouched
9:16 GenerationRun retries independently
Idea Story and Prompt are reused
```

---

## 35. Example end-to-end Etsy flow

```text
Etsy - Shop A
└── listing - ASIN 1
```

1. Daily scan creates the listing task.
2. Input Data snapshots listing context.
3. Idea Story uses Etsy-specific knowledge.
4. Prompt creates model-specific `1:1` generation instructions.
5. Generation produces one or more `1:1` versions.
6. Raw versions are saved under `output/1x1/...`.
7. Watermark & Smart Enhance processes each version independently.
8. UI shows the listing under `Etsy - Shop A` with version counts and node status.

---

## 36. Completion policy

The system needs an explicit definition of when a listing is complete.

Initial recommended policy:

### Amazon

A pipeline run is complete when:

```text
at least one required final enhanced video exists for 16:9
AND
at least one required final enhanced video exists for 9:16
```

### Etsy

A pipeline run is complete when:

```text
at least one required final enhanced video exists for 1:1
```

If multiple models are configured as required, the policy can later become stricter.

Do not define completion as "all historical GenerationRuns completed" because optional/regenerated versions may fail without invalidating a good final output.

---

## 37. Manual controls

V1 should provide enough manual control to recover without DB intervention.

Required operations:

```text
Scan now
Retry failed node
Retry failed generation
Regenerate Idea Story
Regenerate Prompt
Generate another video version
Retry enhancement
Cancel active run
Start a new pipeline run
```

Every action should be an API/service operation with authorization, validation, idempotency, and audit metadata.

No UI action should repair workflow state by directly editing database statuses.

---

## 38. Implementation phases

The feature should be implemented in bounded phases rather than one very large Codex run.

### CP-00 — Architecture audit and contracts

Deliver:

```text
actual current navigation insertion point
existing queue/scheduler reuse decision
source-folder/provider access strategy
DB entity contract
platform profiles
artifact storage strategy
Knowledge Pack location/contract
```

No production behavior yet.

### CP-01 — Domain schema + read APIs

Implement:

```text
SourceGroup
ListingTask
PipelineRun
NodeRun
GenerationRun
Artifact
ScanRun if needed
```

Add migrations and focused model/repository tests.

### CP-02 — Folder discovery + daily scanner

Implement:

```text
SourceGroup configuration/read
listing folder parser
daily/manual scan
idempotent discovery
new ListingTask creation
initial PipelineRun creation
```

No AI generation yet.

### CP-03 — Creative Pipeline UI shell

Implement tab placement:

```text
Visual Search
Creative Pipeline
Inventory Daily
```

Implement:

```text
grouped parent-folder view
listing rows
filters/search
status visualization
detail drawer shell
manual Scan now
```

Use real API state, not mocked hard-coded production behavior.

### CP-04 — Input Data node

Implement reproducible listing snapshots and input artifact lineage.

### CP-05 — Knowledge Pack + OpenAI Idea Story

Implement:

```text
knowledge manifest
KnowledgeLoader
knowledge snapshot
OpenAI structured output
Idea Story artifacts
retry/regenerate semantics
```

### CP-06 — Prompt node

Implement Seedance 2.5 and Google Omni prompt contracts and prompt versioning.

### CP-07 — Generation provider adapters

Implement provider interfaces and one provider at a time.

Recommended order:

```text
Seedance 2.5 first
Google Omni second
```

Implement aspect-ratio fan-out according to platform profile.

### CP-08 — Versioned Video Output

Implement safe artifact download/write, collision-safe versioning, listing-folder output paths, and artifact lineage.

### CP-09 — Watermark & Smart Enhance

Integrate the existing/selected watermark removal and enhancement pipeline with per-artifact retry semantics.

### CP-10 — Retry, cancellation, bulk recovery

Harden automatic retry, manual retry, cancellation, group retry, provider failure behavior, and stale-worker recovery.

### CP-11 — Operations hardening

Add metrics, queue visibility, rate/concurrency policy, failure diagnostics, security review, and production rollout plan.

---

## 39. Testing strategy

### 39.1 Folder scanner

Test:

```text
new listing discovered
existing listing not duplicated
same ASIN in two groups remains separate
deleted/missing folder does not destroy history
invalid folder name ignored
cross-tenant/source isolation
concurrent scans do not duplicate listing
```

### 39.2 Pipeline orchestration

Test:

```text
node dependency ordering
successful upstream reuse
retry failed node only
regenerate creates new version
cancel prevents new downstream work
worker retry idempotency
crash/reclaim behavior
```

### 39.3 Knowledge/OpenAI

Test:

```text
manifest selects correct platform/model files
knowledge snapshot hash deterministic
structured output validation
invalid provider response fails safely
retryable vs terminal OpenAI errors
no secrets persisted
```

### 39.4 Generation

Test:

```text
Amazon fan-out 16:9 + 9:16
Etsy fan-out 1:1
multiple versions preserved
provider request idempotency
provider busy retry
failed generation does not destroy successful siblings
```

### 39.5 Artifacts

Test:

```text
safe listing-relative paths
version collision prevention
partial file not published as successful
artifact lineage correct
raw video preserved after enhancement
```

### 39.6 UI

Test:

```text
tab ordering
parent group collapse/expand
listing search/filter
group status summary
node state rendering
detail drawer
retry action
regenerate action
run history
multiple video versions
loading/error/empty states
```

---

## 40. Acceptance criteria for the initial product

```text
[ ] Creative Pipeline tab exists between Visual Search and Inventory Daily.
[ ] UI uses the application's established component/style language.
[ ] Listing tasks are grouped by persisted parent SourceGroup.
[ ] Daily scanner detects new `listing - ASIN` folders.
[ ] Existing listings are not duplicated on later scans.
[ ] Amazon listings create required 16:9 and 9:16 output branches.
[ ] Etsy listings create required 1:1 output branch.
[ ] Every listing can retain multiple PipelineRuns.
[ ] Every node has durable status, attempt count, and error state.
[ ] Every node can be retried independently where safe.
[ ] Retry does not unnecessarily rerun successful upstream nodes.
[ ] Explicit regeneration creates a new version rather than overwriting history.
[ ] Idea Story uses OpenAI + versioned Knowledge Pack context.
[ ] Prompt generates separate Seedance 2.5 and Google Omni contracts.
[ ] Knowledge selection is deterministic and reproducible per run.
[ ] Generating supports multiple GenerationRuns from one prompt.
[ ] Raw video output is versioned and stored inside the listing folder.
[ ] Watermark & Smart Enhance works per raw-video artifact.
[ ] Enhanced output is versioned and does not overwrite raw output.
[ ] UI displays node state, retries, video version counts, and current run.
[ ] Listing detail exposes artifact/run history.
[ ] Source scans, pipeline nodes, and generation are idempotent.
[ ] No cross-tenant/source leakage is possible.
[ ] No secrets are persisted in artifacts or operator-facing logs.
[ ] Long-running work is executed by durable workers, not synchronous browser requests.
[ ] Concurrency/rate policies protect external providers.
[ ] Operators can recover failures without direct DB edits.
```

---

## 41. Recommended first implementation milestone

Do not start with OpenAI or video generation.

The safest first milestone is:

```text
SourceGroup configuration
        ↓
Daily/manual folder scan
        ↓
ListingTask discovery
        ↓
PipelineRun + NodeRun skeleton
        ↓
Creative Pipeline tab
        ↓
Grouped listing UI
```

The UI should initially be able to show real discovered listings such as:

```text
Etsy - Shop A
├── listing - ASIN 1   Waiting
└── listing - ASIN 2   Waiting

Amazon - Store B
└── listing - ASIN 3   Waiting
```

Only after this domain/discovery layer is stable should implementation add:

```text
Input Data
→ Idea Story
→ Prompt
→ Generating
→ Video Output
→ Watermark & Smart Enhance
```

This ordering reduces the risk of building expensive AI/generation behavior before task identity, retry semantics, grouping, artifact lineage, and queue ownership are correct.

---

## 42. Open implementation decisions

The following decisions should be resolved during CP-00 by inspecting the current repository and actual provider constraints:

1. Which connected source/provider owns the listing folders in V1?
2. Are output files written back through existing source-provider APIs or first written locally then uploaded?
3. How is `ASIN` parsed for Etsy folders if the business identifier is not literally an Amazon ASIN?
4. Where should the Knowledge Pack live: repository-controlled files, database-managed content, or a dedicated controlled source folder?
5. Which OpenAI model/configuration should generate Idea Story and Prompt?
6. What is the exact Google Omni API/model contract intended for V1?
7. Does Seedance expose asynchronous job IDs, callbacks, polling, or all three?
8. What concurrency/rate limits apply to each generation provider?
9. Which existing watermark removal / Smart Enhance implementation should become the canonical processing adapter?
10. What exact output naming convention is preferred for human browsing inside listing folders?
11. Should newly discovered listings auto-start immediately or enter `Waiting for start` until explicitly approved?
12. Should all generated provider/model branches be required for listing completion, or only a configurable minimum set?
13. What permissions should non-admin operators receive for regenerate/retry/cancel actions?
14. Should zero-resource SourceGroups remain visible in the Creative Pipeline UI?
15. Should completed listings be hidden by default after a retention period or remain in the primary group view?

These decisions should be documented before implementation choices become difficult to reverse.

---

## 43. Final target architecture

```text
                         CREATIVE PIPELINE

Configured Source Roots
        │
        ▼
Daily / Manual Scanner
        │
        ├── SourceGroup: Etsy - Shop A
        │      ├── listing - ASIN 1
        │      └── listing - ASIN 2
        │
        └── SourceGroup: Amazon - Store B
               └── listing - ASIN 3

                    │ new listing
                    ▼
                ListingTask
                    │
                    ▼
                PipelineRun
                    │
                    ▼
              ┌─────────────┐
              │ Input Data  │
              └──────┬──────┘
                     ▼
              ┌─────────────┐
              │ Idea Story  │◄──── OpenAI
              └──────┬──────┘      + Knowledge Pack
                     ▼
              ┌─────────────┐
              │   Prompt    │◄──── OpenAI
              └──────┬──────┘      + Knowledge Pack
                     ▼
          ┌───────────────────────┐
          │      Generating       │
          │ Seedance / Google Omni│
          └──────────┬────────────┘
                     │
            ┌────────┴────────┐
            ▼                 ▼
       Generation v1     Generation v2 ...
            │                 │
            └────────┬────────┘
                     ▼
              Versioned Videos
          inside listing folder
                     │
                     ▼
        Watermark & Smart Enhance
                     │
                     ▼
             Final Artifacts
```

The design intentionally separates:

```text
folder discovery
creative domain state
execution queue
AI knowledge
provider generation
artifact storage
post-processing
UI operations
```

so each layer can evolve independently without breaking listing history or retry semantics.
