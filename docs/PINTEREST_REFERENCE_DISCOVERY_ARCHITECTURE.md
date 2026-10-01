# Pinterest Reference Discovery & Skill-Driven Image Generation Architecture

> **Status:** Canonical architecture for future Pinterest/reference/generation optimization
> **Applies to:** Creative Asset Manager, Realistic Review UGC, Pinterest Auto Scout, future reference-driven image workflows
> **Last updated:** 2026-09-30
> **Architecture priority:** If older RRUGC/Pinterest documentation conflicts with this document on reference discovery, Product Context Scout, Reference Library, or Codex/ImageGen execution, this document takes precedence.
> **Primary rule:** Pinterest discovers reusable references. Skills consume role-based references. These layers must remain decoupled.

---

## 1. Purpose

Creative Asset Manager must evolve from a Pinterest workflow tightly coupled to one Realistic Review UGC/hat use case into a reusable reference discovery and generation platform.

The system must support:

- Pinterest-discovered images;
- images uploaded by the operator;
- future reference sources;
- product-aware Pinterest discovery;
- visual seed learning;
- context-aware ranking;
- arbitrary local Codex skills;
- image generation through Codex + $imagegen;
- the existing server-side processing queue, storage, Supervisor, review, export and delivery infrastructure.

Target system:

    REFERENCE SOURCES
           |
           +-- Pinterest Scout
           +-- User Upload
                    |
                    v
            Reference Library
                    |
           +--------+---------+
           |                  |
       Seed learning      Ready references
           |                  |
           +--------+---------+
                    v
              Reference Set
                    |
                    v
                Skill Job
                    |
                    v
             Existing Queue
                    |
                    v
           Codex CLI on VPS
                    |
                    v
            $selected-skill
                    |
                    v
               $imagegen
                    |
                    v
                 Output
                    |
                    v
         Managed Storage / QA

---

## 2. Architecture principles

### 2.1 Pinterest is a reference acquisition system

Pinterest must not know which skill will eventually consume an image.

Pinterest is responsible for:

- discovering candidates;
- resolving the best available Pin image;
- deduplicating Pin identity;
- submitting candidates;
- collecting enough metadata for server-side qualification.

Pinterest is not responsible for:

- choosing a generation skill;
- understanding a Codex execution plan;
- selecting a generation provider;
- running image generation;
- managing final outputs.

### 2.2 Skills are execution logic, not discovery logic

A skill should receive role-based inputs such as:

    person
    scene
    product_front
    product_side
    product_back
    artwork
    detail

The skill should not need to know whether those inputs came from:

- Pinterest;
- user upload;
- Product Registry;
- Managed Drive;
- another future source.

### 2.3 PostgreSQL and Processing Jobs remain authoritative

Do not introduce a second queue/state system such as:

    queue.json
    jobs.json
    usage_state.json

The existing processing_jobs infrastructure already provides:

- idempotency;
- claiming;
- leases;
- retries;
- next_attempt_at;
- attempt counts;
- failure information;
- cancellation;
- crash recovery.

### 2.4 Keep the Scout simple

The local Pinterest Scout should remain intentionally narrow:

    claim campaign
    |
    v
    search Pinterest
    |
    v
    collect candidate
    |
    v
    resolve high-resolution Pin image
    |
    v
    submit
    |
    v
    complete run

All expensive or domain-specific intelligence belongs on the server.

### 2.5 Prefer incremental migration over rewriting RRUGC

The current RRUGC pipeline remains operational.

New generic abstractions are added around it and existing candidates are promoted into the new Reference Library when ready.

---

# PART I — REFERENCE ACQUISITION

## 3. Unified reference model

Pinterest and operator uploads ultimately become the same logical object:

    ReferenceAsset

Example Pinterest asset:

    {
      "id": "ref_18492",
      "source_type": "pinterest",
      "source_url": "https://www.pinterest.com/pin/...",
      "content_hash": "...",
      "width": 1536,
      "height": 2048,
      "status": "ready"
    }

Example upload:

    {
      "id": "ref_18493",
      "source_type": "upload",
      "original_filename": "IMG_5829.jpg",
      "content_hash": "...",
      "width": 3024,
      "height": 4032,
      "status": "ready"
    }

After ingestion, downstream generation should operate on ReferenceAsset IDs rather than source-specific objects.

---

## 4. Reference lifecycle

Conceptual lifecycle:

    Found
      |
      v
    Candidate
      |
      v
    Keep / Reject
      |
      v
    Ready
      |
      v
    Reference Library

Suggested operator semantics:

- **Found** — discovered but not qualified;
- **Keep** — useful signal/reference candidate and positive learning example;
- **Rejected** — unsuitable for the current campaign/profile;
- **Ready** — durable high-quality reference available in managed storage.

Existing RRUGC database statuses do not need to be renamed immediately. UI semantics and a generic promotion layer should be introduced first.

---

## 5. Reference Library

The Reference Library becomes the shared source for every skill-driven workflow.

Primary filters:

    Sources
    [ All ] [ Pinterest ] [ Upload ]

    Campaign
    [ ... ]

    Type
    [ Person ] [ Product ] [ Scene ] [ Detail ] [ Artwork ] [ Other ]

    Status
    [ Ready ]

Each reference should expose at least:

- source;
- resolution;
- content hash;
- campaign/profile;
- tags/themes;
- quality signal;
- visual/context scores when available;
- usage count;
- managed-storage location.

Useful actions:

- Use in generation;
- Add to reference set;
- Mark good;
- Mark bad;
- Download;
- Archive.

---

## 6. Upload ingestion

Uploaded images should pass through the same durable preparation pipeline as promoted Pinterest references:

    upload
    |
    v
    content hash
    |
    v
    dedupe
    |
    v
    dimensions / format validation
    |
    v
    thumbnail
    |
    v
    embedding
    |
    v
    managed storage
    |
    v
    ReferenceAsset

Generation must not require separate Pinterest-vs-upload code paths.

---

# PART II — PINTEREST SCOUT

## 7. Scout remains browser-local

Pinterest browser automation remains on a user-controlled browser machine because it depends on:

- the user's authenticated Pinterest session;
- persistent browser cookies;
- normal website access;
- manual resolution when Pinterest requests verification.

Do not move the Pinterest browser session into the Codex generation runtime.

Recommended topology:

    Windows / Browser Machine
    +-- Pinterest Scout

    VPS
    +-- Creative Asset Manager
    +-- Reference Library
    +-- context/ranking intelligence
    +-- Codex image generation

---

## 8. Scout v10 low-footprint browser policy

The production Scout should preserve the current low-footprint approach:

    1 persistent Search tab
    +
    1 persistent reusable Pin Detail tab

Rules:

- Pin detail concurrency is forced to 1;
- the same detail tab is reused sequentially;
- do not create/close multiple detail tabs in bursts;
- pause between detail navigations;
- keep bounded candidates/keywords per run;
- preserve the persistent Chrome profile;
- do not rotate fingerprint/proxy merely to evade controls;
- do not automate CAPTCHA solving;
- do not spoof human mouse activity to bypass anti-bot systems.

Current safe defaults may evolve based on observed production behavior, but concurrency should remain 1 unless deliberate testing proves otherwise.

Access-gate behavior:

    login / challenge / 401 / 403
    |
    v
    stop current run
    |
    v
    release work
    |
    v
    cooldown
    |
    v
    manual resolution

HTTP 429 behavior:

    stop current run
    |
    v
    cooldown
    |
    v
    do not immediately claim another campaign

The objective is low footprint and predictable recovery, not anti-detection circumvention.

---

## 9. Discovery modes

A single Scout engine supports multiple server-defined discovery modes.

Recommended modes:

    Keyword
    Visual Seed
    Product Context

The Scout itself receives resolved keywords/bounds from the server and does not contain product-specific intelligence.

---

# PART III — SEED-BASED DISCOVERY

## 10. Seed references

Campaign seeds can come from:

- operator-uploaded images;
- Pinterest references marked good;
- existing Reference Library assets.

Seeds answer:

> What should the desired references look like?

They primarily influence:

- composition;
- camera distance;
- people/scene appearance;
- realistic-vs-editorial style;
- lighting;
- candid/phone-photo characteristics.

---

## 11. Seed visual learning

Use existing visual-embedding infrastructure rather than creating a new one solely for Pinterest.

Candidate ranking can consider:

    similarity(candidate, positive seeds)
    -
    similarity(candidate, negative examples)

Positive and negative learning should be scoped to the appropriate:

    profile
    +
    campaign

Do not globally learn that an image style is bad merely because it is wrong for one campaign.

---

## 12. Context versus style

These signals are different and must remain separate:

    Product Context
    = WHAT scene/person is semantically appropriate?

    Seed References
    = HOW the desired image should look?

Example:

    Product context:
    pet owner

    Seed style:
    young woman
    outdoor
    warm afternoon
    medium shot
    phone candid

Both should influence ranking without being collapsed into one opaque signal.

---

# PART IV — PRODUCT CONTEXT SCOUT

## 13. Objective

Product Context Scout finds reference images whose scene, people and activity are semantically compatible with the meaning of the product or embroidery/artwork.

Example:

    Product:
    custom dog portrait embroidered cap

    Wrong interpretation:
    find Pinterest images visually resembling the stitched dog

    Correct interpretation:
    find realistic pet-owner lifestyle references:
    - person with dog
    - walking dog
    - dog park
    - home with pet
    - outdoor weekend lifestyle

The embroidery/product signal guides contextual discovery, not pixel-level matching.

---

## 14. Product Context Profile

For a product-context campaign, the server derives a Product Context Profile from:

    product title
    +
    product description
    +
    product images
    +
    variant/color
    +
    embroidery/artwork crop when available
    +
    operator notes when supplied

Example:

    {
      "product_type": "cap",
      "embroidery_subject": "dog portrait",
      "themes": [
        "pet",
        "dog owner",
        "personalized gift"
      ],
      "emotional_context": [
        "bonding",
        "affection",
        "casual lifestyle"
      ],
      "likely_people": [
        "adult",
        "pet owner"
      ],
      "likely_scenes": [
        "walking dog",
        "dog park",
        "home with pet",
        "outdoor casual"
      ],
      "avoid": [
        "studio fashion",
        "formal editorial portrait"
      ]
    }

The system should prefer available textual product metadata before spending vision/model work unnecessarily.

---

## 15. Embroidery/artwork region

When product images contain a small embroidered/artwork region, the system may produce:

    product_full.jpg
    embroidery_region.jpg

The embroidery crop is useful for:

- understanding subject/theme;
- extracting text or icon semantics;
- preserving design fidelity later during generation.

It should not be used as the primary visual-similarity target against Pinterest lifestyle photos.

---

## 16. Context levels

Product Context should avoid becoming too literal.

### Level 1 — Direct

Very close semantic relationship.

    dog embroidery
    -> person with dog

### Level 2 — Adjacent

Lifestyle context strongly associated with the product theme.

    dog embroidery
    -> park / outdoor / casual weekend

### Level 3 — Generic compatible

Still usable for generation even without the direct subject.

    dog embroidery
    -> candid casual portrait / cafe / weekend

A typical discovery mix may start around:

    50% Direct
    30% Adjacent
    20% Generic

These percentages are strategy defaults, not hard-coded guarantees.

---

## 17. Product-context examples

### 17.1 Pet portrait embroidery

Context themes:

    pet
    dog owner
    bonding
    outdoor casual

Possible search clusters:

    woman walking dog candid
    man with dog casual outfit
    dog owner outdoor lifestyle
    couple with dog candid
    pet owner phone photo

### 17.2 Dad/family embroidery

Themes:

    fatherhood
    family
    weekend
    children

Search clusters:

    dad with kids candid
    father son outdoor photo
    dad backyard casual
    family weekend phone photo

### 17.3 Teacher embroidery

Themes:

    teacher
    school
    classroom
    campus
    casual work

Search clusters:

    teacher classroom candid
    teacher school hallway
    teacher desk candid photo

### 17.4 Wedding/pet memorial product

The product purpose can override a simplistic embroidery interpretation.

A pet portrait associated with a wedding gift may prioritize:

    wedding
    groom
    family
    sentimental

rather than only generic pet-owner imagery.

---

## 18. Reusable context themes

Context learning should not be locked to one SKU.

Products may share reusable themes such as:

    pet_owner
    dad_family
    teacher_school
    wedding
    sports
    outdoor
    couple
    memorial_pet

A product may have multiple themes:

    fatherhood
    cycling
    outdoor
    family

This allows one product to produce multiple Pinterest search clusters without forcing it into a single category.

---

## 19. Product Context UI

Recommended compact configuration:

    PRODUCT CONTEXT

    Custom Dog Portrait Cap

    Detected themes
    [ Pet ] [ Dog owner ] [ Outdoor ] [ Personalized ]

    Preferred scenes
    [x] With dog
    [x] Walking outdoors
    [x] Home candid
    [ ] Studio portrait

    Reference style
    [ Realistic phone photo ]

    [ Start Scout ]

Default:

    Auto Context = ON

Operator editing is optional and mainly used when results drift from the intended theme.

---

# PART V — RANKING & LEARNING

## 20. Independent ranking signals

Do not collapse all discovery intelligence into one unexplained embedding.

Keep major signals logically separate:

1. Context compatibility
2. Seed visual similarity
3. Realistic/mobile UGC quality
4. Product editability
5. Resolution/technical quality
6. Learned campaign/profile feedback
7. Editorial/AI/duplicate risk

Conceptually:

    Final Reference Score
    =
    Context Compatibility
    +
    Seed Similarity
    +
    Realistic UGC
    +
    Editability
    +
    Image Quality
    +
    Learned Signal
    -
    Editorial Risk
    -
    AI Risk
    -
    Duplicate Risk

Exact weights are implementation details and should be tuned from real feedback.

---

## 21. Editability for product-on-person generation

A contextually perfect image can still be a bad generation reference.

For hats/caps, editability includes:

- head visible;
- sufficient head size;
- acceptable head angle;
- low occlusion;
- usable hair/product contact region;
- no unsuitable existing headwear;
- sufficient resolution;
- plausible perspective.

Existing RRUGC head/UGC metrics remain valuable, but they belong to a reference profile, not the global Pinterest core.

---

## 22. Reference Profiles

Product-specific qualification rules are represented as profiles.

Example:

    Profile: realistic-person-ugc

Signals:

    people count
    head ratio
    head visibility
    phone realism
    editorial risk
    AI risk
    editability

Future profile:

    Profile: embroidery-detail

Signals might include:

    macro/detail framing
    stitch sharpness
    fabric visibility
    low stylization risk
    technical resolution

The core Scout remains unchanged.

---

## 23. Human feedback

Right-click/reference feedback should distinguish image quality from contextual suitability.

Recommended actions:

    Use as reference
    Good context
    Wrong context
    Too artistic
    Hard to edit product
    Low quality
    Not relevant

Wrong context must not teach the system that the image itself is globally poor.

Learning hierarchy:

    global source-quality signal
    +
    profile learning
    +
    campaign learning

---

## 24. Keyword learning

Keywords are discovery probes, not the final intelligence layer.

Track downstream usefulness per keyword:

    keyword A
    80 evaluated
    14 useful

    keyword B
    60 evaluated
    32 useful

    keyword C
    100 evaluated
    2 useful

Prefer higher-yield queries while retaining exploration.

A reasonable conceptual strategy:

    80% exploit learned queries
    20% explore less-used/new queries

Do not permanently eliminate a query solely because of a short bad streak.

---

# PART VI — GENERIC REFERENCE SETS

## 25. Reference Set

Generation consumes role-based reference sets.

Example:

    PERSON
    ref_001  <- Pinterest

    PRODUCT_FRONT
    ref_002  <- Upload

    PRODUCT_SIDE
    ref_003  <- Upload

    ARTWORK
    ref_004  <- Upload

The skill receives role-labeled files, not Pinterest-specific URLs.

---

## 26. Generic skill inputs

Avoid adding database fields such as:

    hat_reference_id
    shirt_reference_id
    embroidery_reference_id
    scene_reference_id

Prefer generic input bindings:

    {
      "skill": "future-skill",
      "inputs": [
        {
          "role": "scene",
          "reference_id": "ref_01"
        },
        {
          "role": "product",
          "reference_id": "ref_02"
        },
        {
          "role": "artwork",
          "reference_id": "ref_03"
        }
      ]
    }

This allows dozens or hundreds of future skills without schema rewrites.

---

## 27. Optional future skill input schema

A later phase may support a small companion manifest:

    inputs:
      - role: person
        min: 1
        max: 1

      - role: product
        min: 1
        max: 4

      - role: artwork
        min: 0
        max: 1

Do not require this for V1.

Do not use another LLM call merely to infer a skill's required inputs from SKILL.md.

---

# PART VII — CODEX / SKILLS / IMAGE GENERATION

## 28. Skills are generic and replaceable

Example skill ZIPs used during design are examples only. Pinterest architecture must not hard-code them.

A local skill may be any future workflow:

    product-on-person
    embroidery-detail
    product-studio
    multi-color-scaling
    holiday-family-edit
    pet-portrait-edit
    interior-mockup
    ...

Skill selection belongs to generation orchestration, not Pinterest discovery.

---

## 29. Skills do not need to be uploaded to ChatGPT web

For Codex CLI execution, skills live in the Codex environment on the VPS.

Example:

    /var/lib/creative-image-runtime/.codex/
    +-- skills/
        +-- skill-a/
        |   +-- SKILL.md
        +-- skill-b/
            +-- SKILL.md

Set:

    CODEX_HOME=/var/lib/creative-image-runtime/.codex

The server invokes the selected skill explicitly:

    Use $selected-skill and $imagegen.

ChatGPT web is not part of the production dependency chain.

---

## 30. Run Codex on the VPS

A separate Windows image-generation machine is not required.

Target topology:

    VPS
    +-- Creative Asset Manager
    +-- processing worker
    +-- Codex CLI
    +-- local skills
    +-- temp generation workspace

Pinterest browser discovery remains local/browser-assisted, while Codex image generation runs on the VPS.

---

## 31. Do not build a separate Creative Image Agent in V1

The existing rrugc_generate processing path already has:

- GenerationAttempt;
- processing jobs;
- idempotency;
- retry/defer behavior;
- input provenance;
- managed storage;
- output validation;
- Supervisor enqueue;
- review/export lifecycle.

Therefore V1 should add only a thin Codex execution adapter.

Target:

    existing rrugc_generate
            |
            v
    CodexImageGenRunner
            |
            v
        codex exec
            |
            v
    $selected-skill + $imagegen
            |
            v
      final image bytes
            |
            v
    existing PIL/hash/storage/Supervisor path

Do not add:

- another agent registration API;
- another heartbeat protocol;
- another queue;
- another persistent state store;
- another upload-complete endpoint;
- another systemd service solely to duplicate the worker.

---

## 32. Minimal Codex runner

The new runner should only:

    materialize references
    |
    v
    create isolated temp workspace
    |
    v
    invoke codex exec
    |
    v
    verify final output exists
    |
    v
    return image bytes
    |
    v
    cleanup temp workspace

Example temp workspace:

    /tmp/creative-asset-manager-image-generation/codex/<attempt_id>/
    +-- person.jpg
    +-- product-front.jpg
    +-- product-side.jpg
    +-- output/
        +-- final.png

A separate job.json is optional and not required in V1 if the handler can provide the required instructions directly.

---

## 33. Keep Codex prompts short

Business rules should live in the selected skill.

A V1 invocation should be close to:

    Use $selected-skill and $imagegen.

    Edit target:
    person.jpg

    Product references:
    product-front.jpg
    product-side.jpg

    Follow the generation instructions.
    Generate exactly one final image.
    Save it to output/final.png.

Do not duplicate thousands of tokens of stable rules in every job if they already exist in SKILL.md.

---

## 34. Deterministic skill selection

Do not introduce an AI router merely to choose a skill.

Use deterministic mapping based on workflow/job configuration.

Example:

    RRUGC product-on-person
    -> configured product-edit skill

    Product Studio
    -> configured product-studio skill

    7-color scaling workflow
    -> configured scale skill

The existing worker_skill_version/generation provenance should be reused where practical.

---

## 35. Image generation concurrency

Initial Codex image generation concurrency:

    1

Reasons:

- predictable included usage;
- easier crash recovery;
- easier debugging;
- avoids simultaneous quota failures;
- prevents temporary-workspace contention;
- simplifies output attribution.

Only raise concurrency after measured production testing.

---

## 36. Usage-limit pause/resume

Do not build a new paused_limit state machine.

Reuse the existing processing-job defer mechanism:

    Codex usage limit
    |
    v
    return DeferredJobOutcome
    |
    v
    next_attempt_at = future
    |
    v
    worker continues other eligible work
    |
    v
    job retries later

If still limited:

    defer again

Do not attempt to resume in the middle of one image-generation call.

---

## 37. No automatic paid API fallback

Primary objective:

    ChatGPT/Codex-side included usage

Therefore:

    paid API fallback = OFF

If built-in $imagegen is unavailable or the Codex session is not entitled:

    defer / mark provider unavailable

Do not silently invoke an API-key-backed image path.

Paid API fallback, if ever added, must be an explicit opt-in configuration.

---

## 38. Capability probe

Before enabling Codex generation in production, verify:

    Codex installed?
    |
    v
    ChatGPT authentication valid?
    |
    v
    selected local skill discoverable?
    |
    v
    $imagegen discoverable?
    |
    v
    built-in image generation actually callable?
    |
    v
    workspace writable?
    |
    v
    test PNG created successfully?

Generation jobs should not be switched from the existing provider until the smoke test creates a real output image without requiring unintended API billing.

---

## 39. Existing output lifecycle remains unchanged

After Codex produces an image, keep the current server pipeline:

    validate raster image
    |
    v
    width / height
    |
    v
    SHA-256
    |
    v
    store_asset()
    |
    v
    generation attempt = completed
    |
    v
    Supervisor enqueue
    |
    v
    human review
    |
    v
    export / delivery

Do not move authoritative QA into Codex.

Codex may perform a minimal self-check, but Supervisor remains the system QA boundary.

---

# PART VIII — UI

## 40. Pinterest campaign UI

Recommended campaign structure:

    Discovery
    References
    Seeds
    Keywords
    Settings

Campaign card:

    Hat Lifestyle
    --------------------------------

    Ready         184
    Found         1,248
    Rejected      786

    Scout         ● Online

    Seed refs     12
    Keywords      18

    [ Open ]

Avoid surfacing low-value implementation metrics on the primary campaign card.

---

## 41. Discovery mode UI

    Discovery mode

    ( ) Keyword
    ( ) Visual seed
    (*) Product context

All modes use the same Scout engine.

---

## 42. Reference Library UI

    REFERENCE LIBRARY

    Source
    [ All | Pinterest | Upload ]

    Profile
    [ ... ]

    Type
    [ Person | Product | Scene | Detail | Artwork | Other ]

    Status
    [ Ready ]

The library should be independent of one RRUGC campaign so references can be reused by future skills.

---

## 43. Skill generation UI

The eventual generic creation surface may look like:

    CREATE WITH SKILL

    Skill
    [ Selected skill ]

    References
    --------------------------------

    Person / Scene
    [Pinterest reference]

    Product Front
    [Uploaded reference]

    Product Side
    [Uploaded reference]

    Artwork
    [Uploaded reference]

    [ Generate ]

The reference source is irrelevant after selection.

---

# PART IX — DATA MODEL / MIGRATION STRATEGY

## 44. Incremental generic layer

Do not rewrite current RRUGC tables in one migration.

Add a generic layer over the current workflow:

    reference_assets
    reference_sets
    reference_set_items
    reference_profiles

Current RRUGC candidates remain valid.

Promotion path:

    rrugc_candidate
    |
    v
    qualified + durable
    |
    v
    reference_asset

Uploads:

    upload
    |
    v
    reference_asset

Generation then moves progressively toward:

    reference_asset(s)
    |
    v
    reference_set
    |
    v
    skill execution

---

## 45. Preserve existing proven infrastructure

The following components are not to be duplicated:

- PostgreSQL processing queue;
- Processing Job leases;
- idempotency;
- GenerationAttempt;
- Managed Storage;
- product/variant snapshots;
- Supervisor;
- Review Board;
- export/delivery lifecycle;
- visual encoder/search infrastructure.

Future optimization should prefer adapting these components over adding parallel systems.

---

# PART X — WHAT TO REMOVE / AVOID

## 46. Explicit simplifications

Do not build the following unless a later requirement proves they are necessary:

    separate local image queue
    separate jobs.json
    usage_state.json
    Creative Image Agent registration/heartbeat API
    second generation claim/complete API
    skill-selection LLM
    per-skill database columns
    browser automation of ChatGPT web
    automatic API billing fallback
    permanent local job workspace
    duplicate QA pipeline inside Codex

The desired system has one authoritative queue and one durable asset layer.

---

# PART XI — IMPLEMENTATION ORDER

Implementation status as of 2026-10-01:

- Phase 0 — complete in production.
- Phase 1 — complete in production.
- Phase 2 — complete in production.
- Phase 3 — complete in production.
- Phase 4 — complete in production at backend release `b3316d4086713cc439a8f51f3611c2b41ad87a1b`.
  - Codex CLI is authenticated with ChatGPT for the production service user.
  - Built-in `$imagegen` was capability-probed with real PNG generation (1254×1254) before activation.
  - The Codex runner strips API-key environment variables and does not use an automatic paid API fallback.
  - `ReferenceSet` items are snapshotted immutably into `GenerationAttempt` and materialized into the Codex workspace using their generic roles.
  - Existing Managed Storage, retry/defer behavior, Supervisor and Review lifecycle remain authoritative.
- Phase 5 — optional; defer until multiple production skills require metadata/manifests.

The core end-to-end architecture is therefore complete through Phase 4. Phase 5 is not required for the current production workflow.

## 47. Phase 0 — preserve current Scout

Keep current production Scout behavior stable:

- persistent authenticated browser profile;
- low-footprint v10 tab policy;
- high-resolution Pin resolution;
- campaign leases;
- multi-keyword discovery;
- randomized/learned keyword selection;
- existing qualification pipeline.

No major crawler rewrite is required for the architecture in this document.

---

## 48. Phase 1 — Product Context intelligence

Add server-side:

- Product Context Profile;
- theme extraction from product metadata/images;
- context search clusters;
- Product Context campaign mode;
- context score;
- context feedback: Good context / Wrong context.

Scout receives generated keywords but remains product-agnostic.

---

## 49. Phase 2 — Seed and Reference Library

Add:

- generic ReferenceAsset;
- upload ingestion;
- promotion of ready Pinterest candidates;
- seed selection;
- campaign/profile-scoped visual feedback;
- generic reference browsing.

Reuse existing embedding/index infrastructure.

---

## 50. Phase 3 — Generic Reference Sets

Add:

- ReferenceSet;
- role-labeled items;
- generic generation inputs.

Do not require a complex skill schema yet.

---

## 51. Phase 4 — Codex generation on VPS

Before coding provider integration:

1. install/authenticate Codex on VPS;
2. install one test local skill;
3. invoke $imagegen;
4. generate an actual PNG;
5. verify no unintended API-key dependency.

After probe success:

- add CodexImageGenRunner;
- integrate it into existing rrugc_generate;
- reuse DeferredJobOutcome for quota limits;
- keep concurrency 1;
- keep paid API fallback disabled;
- reuse existing output storage and Supervisor path.

---

## 52. Phase 5 — Optional skill metadata

Only after multiple real skills require it:

- add simple skill input manifests;
- automatic role UI;
- deterministic mapping from workflow/product to skill;
- richer Reference Set templates.

Avoid premature skill-registry complexity.

---

# PART XII — ACCEPTANCE CRITERIA

## 53. Pinterest discovery

The architecture is working when:

- the same Scout can serve multiple reference profiles;
- Product Context campaigns produce contextually relevant scenes;
- visual seeds influence style/composition independently of semantic context;
- uploaded and Pinterest assets coexist in the same Reference Library;
- a skill never needs Pinterest-specific code.

---

## 54. Product Context

For a product such as a dog-portrait embroidered cap:

- the system identifies pet/dog-owner themes;
- generates direct, adjacent and generic-compatible searches;
- ranks person-with-dog/outdoor-casual candidates above unrelated fashion/editorial candidates;
- still rejects contextually good images that are technically hard to edit;
- learns from Good context and Wrong context separately from generic image-quality feedback.

---

## 55. Generation

The Codex path is production-ready when:

- Codex runs on VPS without a separate local generation PC;
- selected local skills are discovered from CODEX_HOME;
- $imagegen produces a real image output in an isolated job workspace;
- rrugc_generate uses the existing Processing Job lifecycle;
- usage exhaustion defers rather than corrupts/fails the queue;
- completed images enter the existing Managed Storage -> Supervisor -> Review path;
- no unrequested paid API fallback occurs.

---

# PART XIII — CANONICAL SYSTEM VIEW

## 56. Final architecture

                               PRODUCT
                                  |
                        metadata / artwork
                                  |
                                  v
                        Product Context Profile
                                  |
                  +---------------+----------------+
                  |                                |
          semantic context                    visual seeds
          "what fits?"                       "what style?"
                  |                                |
                  +---------------+----------------+
                                  v
                           Pinterest Search
                                  |
                                  v
                              Candidates
                                  |
             Context × Seed × Realism × Editability × Quality
                                  |
                                  v
                          Ready Pinterest refs
                                  |
                        +---------+---------+
                        |                   |
                  Pinterest refs        User uploads
                        |                   |
                        +---------+---------+
                                  v
                           Reference Library
                                  |
                                  v
                            Reference Set
                                  |
                                  v
                             Selected Skill
                                  |
                                  v
                      Existing Processing Queue
                                  |
                                  v
                            Codex CLI on VPS
                                  |
                                  v
                        $selected-skill
                                  |
                                  v
                             $imagegen
                                  |
                                  v
                               Output
                                  |
                                  v
                           Managed Storage
                                  |
                                  v
                              Supervisor
                                  |
                                  v
                            Review / Export

---

## 57. Final decision rules

Future optimization work must follow these rules:

1. **Do not hard-code Pinterest to one skill or one product category.**
2. **Do not hard-code a skill to Pinterest as its source.**
3. **Pinterest + Upload converge into a shared Reference Library.**
4. **Product context and visual seed style remain separate signals.**
5. **Context relevance and editability are both required for generation references.**
6. **Scout stays a simple browser collector; intelligence lives on the server.**
7. **Keep Scout browser concurrency low and never add anti-bot circumvention.**
8. **Reuse existing Processing Jobs instead of creating a second queue.**
9. **Run Codex generation on the VPS; no dedicated generation PC is required.**
10. **Use local skills through Codex; do not depend on ChatGPT web uploads.**
11. **Use deterministic skill mapping; do not add an LLM router without a proven need.**
12. **Keep image generation concurrency at one initially.**
13. **Use deferred retries for Codex usage limits rather than a new pause/resume subsystem.**
14. **Never silently fall back to paid API image generation.**
15. **Reuse Managed Storage, Supervisor, Review and Export instead of duplicating them.**
16. **Add generic abstractions incrementally; do not rewrite the working RRUGC pipeline in one step.**

This document is the baseline for all subsequent Pinterest/reference/generation optimization in Creative Asset Manager.
