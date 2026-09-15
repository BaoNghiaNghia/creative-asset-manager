# Creative Pipeline CP-00 — Normative Architecture Contracts

> **Status:** COMPLETE — architecture and documentation only
> **Normative for:** CP-01 through CP-16
> **Base architecture:** the existing CAM tenant, source, asset, processing-job, worker lease, and permission conventions.

## 1. Authority and scope

This document locks V1 Creative Pipeline semantics. It does not authorize a migration, worker, scanner, provider call, API, UI, scheduler, credential change, push, or deployment. Database records are authoritative for workflow state; provider storage is authoritative only for the bytes addressed by an Artifact record.

The existing durable `ProcessingJob` / outbox substrate remains the execution queue. Creative domain records express intent and lineage; workers claim leased jobs, renew or recover expired leases, and report outcomes only through one orchestration service. CP-03 must reuse tenant-scoped idempotency keys, `next_attempt_at`, lease expiry, bounded retry, and canonical failure handling rather than create an unrelated queue.

## 2. Source-folder ownership contract

The real source hierarchy is canonical:

```text
Etsy - EmbrolyShop/
└── listing - 4527798886/
    ├── Source/                 user-owned
    ├── UGC - Macro Vid/        user-owned
    ├── other existing content/ user-owned
    └── Pipeline/               system-owned
```

Creative Pipeline must never rename, move, delete, or overwrite `Source/`, `UGC - Macro Vid/`, or any other pre-existing listing content. It may read selected inputs from them through normal source authorization. It may create and manage only `<listing-folder>/Pipeline/` and its descendants.

If `Pipeline/` already exists, the system resolves its authorized provider folder identity, validates it is a direct child of the listing, and reuses it. Existing files are reconciled as untrusted physical candidates; their presence never completes a node. A missing or inaccessible known `Pipeline/` produces a recoverable storage error, never a new ListingTask or destructive replacement.

### Canonical Pipeline layout

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

`Input` contains immutable input snapshots; `Idea Story` typed story artifacts; `Prompt` provider-specific prompt artifacts; `Generating` safe request/status metadata; `Video Output` raw videos; `Watermark & Smart Enhance` derived videos; and `Logs` sanitized operational diagnostics. Folder names are a storage/presentation contract. They are not a state machine.

## 3. SourceGroup contract

A direct parent folder is a SourceGroup. V1 recognizes only case-insensitive, trimmed parent names matching exactly one supported prefix:

```text
Etsy - <shop name>   -> platform = etsy
Amazon - <store name> -> platform = amazon
```

The suffix must be non-empty after normalization. The display name and path are metadata. Durable identity prefers:

```text
tenant_id + external_source_id + external_folder_id
```

When a provider cannot supply a stable folder ID, the documented fallback is:

```text
tenant_id + external_source_id + normalized_folder_path
```

An unknown parent name is not inferred as Amazon or Etsy: record a safe discovery diagnostic and ignore it until configured or renamed by a user. Disabled/ignored groups are not scanned or enqueued and retain history. A configured group absent in a scan becomes `missing_source`, not deleted. Every lookup includes active tenant and external source; a folder ID from another source or tenant is never trusted.

## 4. ListingTask contract

A direct child named `listing - <listing_key>` is a listing. Parsing is case-insensitive for the `listing - ` prefix, trims outer whitespace, preserves the normalized non-empty suffix as `listing_key`, and rejects an empty suffix. Nested folders such as `Source`, `UGC - Macro Vid`, and `Pipeline` are never listing candidates.

`listing_key` is the canonical marketplace-neutral field:

| Platform | Meaning |
| --- | --- |
| Amazon | ASIN |
| Etsy | Etsy Listing ID |

`listing_key` is not globally unique. Preferred ListingTask identity is:

```text
tenant_id + source_group_id + external_listing_folder_id
```

Fallback only without stable provider identity:

```text
tenant_id + source_group_id + normalized_listing_folder_path
```

A folder rename or move updates display/path metadata when its stable folder identity remains unchanged; it does not create another task or run. Listing lifecycle is `active`, `missing_source`, or `archived`. `ListingTask` is not a `PipelineRun`: one listing retains historical runs and has at most one current run pointer.

## 5. PlatformProfile contract

Nodes consume a single PlatformProfile instead of scattering platform conditions. Its conceptual fields are:

```text
platform
required_aspect_ratios
generation_policy
prompt_policy
output_path_policy
enhance_policy
```

Locked V1 profiles are:

```text
etsy:   required_aspect_ratios = ["1:1"]
amazon: required_aspect_ratios = ["16:9", "9:16"]
```

Etsy raw/final output paths use `1x1`. Amazon raw/final paths use `16x9` and `9x16`. Duration, output count, exact provider model identifiers, and enhancement settings are configuration decisions, not implicit business defaults.

## 6. Domain entity and lineage contract

| Entity | Identity / ownership | Authoritative responsibilities and important fields |
| --- | --- | --- |
| SourceGroup | tenant + source + stable parent folder | platform, display name/path, enabled/ignored state, scan configuration, last-seen/scan metadata |
| ListingTask | tenant + SourceGroup + stable listing folder | listing_key, folder identity/path, Pipeline folder identity, source lifecycle, current PipelineRun pointer |
| PipelineRun | tenant-owned child of ListingTask; monotonic `run_number` per listing | trigger (`discovery`, `manual`, `regenerate`), status, immutable input/knowledge references, timestamps |
| NodeRun | tenant-owned child of PipelineRun; unique active logical node branch | canonical node_type, state, attempts, retry metadata, input/output artifact version references |
| GenerationRun | tenant-owned child of PipelineRun and prompt artifact | provider/model/aspect ratio, generation number, provider request identity, state, attempts and timestamps |
| Artifact | tenant-owned, immutable lineage record | listing/run/node/generation links, artifact type/version, relative path, hash, size, MIME, profile/provider/prompt/idea metadata |

Required lineage for every video is:

```text
ListingTask -> PipelineRun -> input Artifact -> idea Artifact -> prompt Artifact
-> GenerationRun -> raw video Artifact -> enhanced Artifact
```

All entity uniqueness and idempotency checks include tenant ownership. Derived fields (group summaries, listing status, UI counts, current enhanced derivative) are recomputed from authoritative rows; they are never the only source of truth.

## 7. Node graph and contracts

The V1 graph has no arbitrary workflow editor:

```text
input_data -> idea_story -> prompt -> video_generation -> video_output -> watermark_smart_enhance
```

| node_type | Required input | Output / success | Retryable vs permanent failure | Downstream / regenerate |
| --- | --- | --- | --- | --- |
| `input_data` | authorized listing, selected Source/ and optional UGC data, profile | immutable `input_vNNN.json` snapshot | temporary provider/storage = retryable; missing required listing/source authorization = permanent/blocked | unlocks idea; explicit regenerate creates a new PipelineRun |
| `idea_story` | input snapshot + knowledge snapshot | schema-valid `idea_vNNN.json` | timeout/429/5xx and bounded repair = retryable; invalid input or exhausted schema repair = failed | unlocks prompt; regenerate creates new idea/downstream lineage |
| `prompt` | idea artifact + profile + knowledge snapshot | provider/model/ratio-specific prompt JSON | temporary model failure = retryable; unsupported profile/config or exhausted validation = permanent | creates GenerationRuns; explicit regenerate creates new prompt lineage |
| `video_generation` | prompt output branch | durable GenerationRun with provider request ID and completed remote result | busy/429/5xx/network/poll timeout = retryable; invalid request/config/profile = permanent | unlocks raw materialization; explicit generate/regenerate allocates a new logical version |
| `video_output` | completed GenerationRun result | reserved, verified raw `vNNN.mp4` Artifact | temporary download/storage = retryable; missing remote result after terminal provider result = failed | unlocks enhance; no regenerate through retry |
| `watermark_smart_enhance` | one raw Artifact + enhance policy | verified `vNNN_enhanced.mp4` Artifact | transient engine/storage = retryable; unsupported media/policy = permanent | terminal; retry never regenerates raw video |

The orchestrator alone changes dependency state. Workers, providers, scanners, and UI do not advance downstream nodes directly.

## 8. State, retry, and idempotency contracts

Canonical NodeRun states are:

```text
pending -> ready -> running -> completed
                   |          -> retry_wait -> ready
                   |          -> failed
pending/ready/running/retry_wait -> blocked
pending/ready/running/retry_wait -> cancelled
```

Illegal transitions are rejected. `completed`, `failed`, and `cancelled` are terminal for that logical NodeRun. `blocked` may return to `ready` only after a separately audited dependency/configuration repair. `PipelineRun` is derived: failed if a required active node terminally fails; running when any node runs; retrying when any waits; queued when ready work awaits capacity; blocked when dependency is blocked; completed only when required final enhanced outputs are complete. Listing status is derived from its current run without erasing history.

Every executable logical unit stores `attempt_count`, `max_attempts`, `next_retry_at`, `last_error_code`, and sanitized `last_error_message`. Transient errors include provider busy, 429, 5xx, timeout, and temporary network failures. Permanent errors include missing required input, unsupported profile, invalid provider configuration, and invalid structured output after bounded repair. Retry uses current CAM bounded backoff/jitter and lease recovery conventions.

Retry means another execution attempt for the same logical artifact/version and idempotency key. Regenerate means a new creative/version branch with a new downstream lineage; no successful historical artifact is overwritten.

Idempotency is required for: SourceGroup discovery; ListingTask discovery; initial PipelineRun creation; node execution; OpenAI idea request; prompt generation; GenerationRun submission; raw materialization; and enhance materialization. CP-01/03 must define unique constraints/keys before enqueueing. A repeated unchanged scan cannot create duplicate ListingTasks or initial runs. A provider submit timeout must reconcile durable local/provider request identity before any re-submit.

## 9. Versioning, artifacts, and reconciliation

Canonical names are:

```text
Input/input_v001.json
Idea Story/idea_v001.json
Prompt/<provider>/prompt_v001.json
Generating/generation_001.json
Video Output/<ratio>/v001.mp4
Watermark & Smart Enhance/<ratio>/v001_enhanced.mp4
```

Versions are zero-padded three digits and monotonic in their applicable lineage scope. Retry reuses a reserved logical version; regenerate reserves a new one. Allocation is atomic: transactionally reserve the Artifact/version uniqueness before physical write, write to a temporary provider object/path, verify expected size/hash/MIME, atomically finalize where the provider supports it, then mark the Artifact available. Workers may never choose a version merely by listing files.

Raw paths are:

```text
Etsy:    Pipeline/Video Output/1x1/vNNN.mp4
Amazon:  Pipeline/Video Output/16x9/vNNN.mp4
         Pipeline/Video Output/9x16/vNNN.mp4
```

Enhanced paths replace `Video Output` with `Watermark & Smart Enhance` and append `_enhanced`. An Artifact records content hash, relative path, MIME, byte size, aspect ratio, provider/model, prompt and idea version, GenerationRun, timestamps, and all parent lineage.

If a DB Artifact exists but file is missing, mark a repairable storage inconsistency; do not silently claim completion. If a file exists without DB record, quarantine/reconcile as untrusted and never infer success. Partial writes remain incomplete and are cleaned/recovered only within system-owned `Pipeline/` under explicit reconciliation rules.

## 10. Input, Knowledge, Idea, and Prompt contracts

`input_vNNN.json` is an immutable run snapshot containing platform, listing_key, SourceGroup identity, listing folder identity, selected Source assets, selected UGC assets, safe folder/listing metadata, optional notes, required ratios, and `captured_at`. Missing optional fields are explicit `null`/empty arrays; missing required inputs are explicit validation failures, never invented values. Downstream nodes use this snapshot rather than live mutable folders as their sole input.

V1 Knowledge Pack is deterministic files, not RAG:

```text
knowledge/
├── global_rules.md
├── amazon_rules.md
├── etsy_rules.md
├── idea_story_rules.md
├── seedance_2_5_rules.md
├── google_omni_rules.md
├── hooks_library.md
└── examples/
```

KnowledgeLoader selection is fixed: Idea = global + platform + idea rules + applicable hooks/examples; Seedance prompt = global + platform + Seedance rules + selected idea; Omni prompt = global + platform + Omni rules + selected idea. Each run stores a manifest/content-hash `knowledge_snapshot_id` so historical output is reproducible.

Idea Story is typed JSON with `idea_story_schema_version`, `concept`, `audience`, `hook`, `story_beats`, `product_focus`, `must_show`, `must_avoid`, `tone`, and `platform_notes`. Every beat has `order`, `duration_hint`, `action`, `dialogue`, and `visual_note`.

Prompt JSON has `prompt_schema_version`, `prompt_version`, `idea_story_version`, `platform`, and `outputs[]`. Each output has `provider`, `model`, `aspect_ratio`, `prompt`, `generation_parameters`, and `knowledge_snapshot_id`. Initial targets are Seedance 2.5 and Google Omni. No provider client is implied by this contract.

## 11. Provider and scanner contracts

A provider adapter conceptually exposes `submit()`, `poll()`, `cancel()`, `fetch_result()`, and `normalize_error()`. It owns authentication/protocol translation only. GenerationRun persists provider, model, ratio, provider request ID, state, attempts, submission/completion timestamps, and sanitized failure code so a restart can resume polling. Credentials, authorization headers, refresh tokens, and secret-bearing raw responses never enter JSON artifacts, logs, error messages, or APIs.

Daily scanning is:

```text
configured active SourceGroups -> direct listing folders -> parse listing_key
-> idempotent ListingTask upsert -> new only: ensure Pipeline + initial PipelineRun
-> later orchestrator makes Input ready
```

Existing listings update last-seen metadata only and do not automatically make another PipelineRun. Missing folders become `missing_source`; history is preserved. Pipeline folder creation is allowed only for a genuinely new listing or explicit storage repair, never because a scan merely sees a file.

## 12. Security, UI, and recovery invariants

All rows, jobs, folder resolutions, APIs, and artifacts are tenant-bound. The backend independently verifies active tenant, SourceGroup/source access, listing ownership, and parent run/node relationships. Client folder/path/source values never authorize access. UI is read-only for state derivation and calls canonical server actions; it never writes execution status directly.

The future Creative Pipeline tab is placed between Visual Search and Inventory Daily. It groups by SourceGroup, shows listing node states `Input | Idea | Prompt | Generate | Output | Enhance`, and provides a detail drawer with identity, current/historical runs, versions, GenerationRuns, artifacts, safe errors, and retry/regenerate actions.

Recovery must converge without duplicate logical output after API/worker restart, expired lease, in-flight provider request, partial physical write, or DB/file disagreement. A worker first reconciles a persisted GenerationRun/provider request ID before submitting again. Successful upstream nodes are not rerun by a downstream failure. Logs/errors remain sanitized.

### Non-negotiable invariants

1. DB is authoritative for workflow execution state.
2. `Pipeline/` is system-owned; Source/ and UGC - Macro Vid/ are user-owned.
3. Scan is idempotent and tenant/source scoped.
4. Retry never creates a new creative version; regenerate does.
5. Successful artifacts are never destructively overwritten.
6. ListingTask is not PipelineRun.
7. File presence alone never means node success.
8. Every artifact has tenant/listing/run lineage.
9. Platform differences come from PlatformProfile.
10. Provider calls are resumable/idempotent where provider capabilities permit.
11. One failed downstream node does not rerun successful upstream work by default.
12. UI never directly edits database execution state.

## 13. Locked decisions and open configuration

**Locked:** V1 platforms, folder ownership, stable identity precedence, `listing_key`, node identifiers, output ratio paths, version format, state machine, retry/regenerate distinction, durable queue reuse, deterministic knowledge selection, typed idea/prompt output, provider interface shape, DB authority, tenant isolation, and recovery invariants.

**Open configuration (not architecture):** default output count per prompt; Seedance duration presets; exact Google Omni model identifier; per-provider max attempts/backoff; scanner time; generation concurrency; Knowledge Pack storage location; provider model matrix; and enhance policy defaults. These require configuration decisions but do not block CP-01 schema identity.

## 14. Bounded implementation roadmap

- **CP-01:** Database/domain models.
- **CP-02:** SourceGroup and Listing scanner.
- **CP-03:** Pipeline orchestrator and retry.
- **CP-04:** Pipeline filesystem plus Input/Artifact framework.
- **CP-05:** Knowledge Pack.
- **CP-06:** OpenAI Idea Story.
- **CP-07:** Prompt generation.
- **CP-08:** Seedance and Google Omni adapters.
- **CP-09:** Video Output and versioning.
- **CP-10:** Watermark and Smart Enhance.
- **CP-11:** Backend APIs.
- **CP-12:** Creative Pipeline UI.
- **CP-13:** Daily scheduler.
- **CP-14:** Concurrency and restart recovery.
- **CP-15:** Observability and integration tests.
- **CP-16:** Canary and rollout.

CP-01 may begin only with these contracts preserved; it must not re-decide CP-00 semantics.