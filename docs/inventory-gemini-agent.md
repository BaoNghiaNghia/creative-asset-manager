# Inventory Gemini Sheet Agent V4.1

Inventory Daily Sheet V4.1 is the production architecture for Daily Sheet automation. Gemini interprets the workbook; Python enforces mechanical safety.

## Responsibilities

Gemini owns workbook and quantity semantics: workbook structure, headers, row meaning, material classification, evidence requirements, and whether a change requires review. Python does not assume Inventory-specific columns, row numbers, or quantity formats.

Python owns tenant and workbook authorization, allowed-sheet enforcement, A1 and grid validation, read/write limits, formula/merged/protected-range safety, evidence hashing, stale-state detection, material tenant/active validation, duplicate target protection, and read-back verification.

## Tool loop

The native Gemini function-call loop is:

1. get_workbook_metadata
2. adaptive read_range or read_cells
3. optional get_material_catalog
4. submit_workbook_assessment
5. more reads if the assessment asks for them
6. stage_edits
7. V4 mechanical validation and, in auto mode only, execution

Every assessment and staged operation cites exact cell evidence returned by a read tool. A completed assessment is required even for an empty ready plan.

## Apply modes

- shadow: plans only; no Google write.
- review: plans only; operations that need review remain non-executable.
- auto: executes only a ready, non-review V4 plan after evidence is re-read and verified.

Auto is configured server-side. The manual endpoint cannot elevate a shadow/review configuration merely through its request body.

## Write lifecycle

The executor re-reads all assessment and operation evidence immediately before mutation. It batches safe set_cell operations, reads targets back and verifies them, then clears any safe clear_cell targets and verifies that they are blank.

The order is always **SET -> VERIFY -> CLEAR**. If a SET verification fails, source cells are never cleared.

## Fail-closed behavior

No write is performed for stale evidence, an unapproved workbook/sheet, unknown operation, invalid A1/grid target, formula/merged/protected target, duplicate target, limit breach, missing/incomplete assessment, inactive or cross-tenant material, review-required operation, or read-back mismatch.

Compound raw values are opaque source evidence. Blank is never converted to zero. New materials, possible renames, and ambiguous material matches require review and are not auto-created.

## Audit metadata

Each run emits safe structured metadata including a deterministic run ID, model, tool rounds, tools called, assessment presence, catalog usage, read counts, staged operation count, write count, verification state, plan hash, and status. Logs must never include OAuth tokens, Gemini keys, database credentials, or full workbook contents.
