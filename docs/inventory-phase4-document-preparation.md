# Inventory Phase 4 — Document Preparation

Phase 4 consumes only Inventory source-file records whose download state is `downloaded` or `duplicate`. It never reads Creative source assets, assets, queues, AI records or indexes.

## Runtime contract

`inventory_file_download` atomically stores the source under `inventory/{tenant}/source/...` and enqueues one idempotent `inventory_document_prepare` job. The Inventory worker registry contains exactly the delivered handlers: `inventory_file_download` and `inventory_document_prepare`.

Preparation validates the stored source key, SHA-256 and persisted byte size before decoding. JPEG, PNG, WebP and AVIF are decoded statelessly, orientation-normalized, alpha-flattened to white, resized without upscaling to the configured bounds, and encoded as deterministic JPEG output. Prepared artifacts are atomically written under `inventory/{tenant}/prepared/{source-file}/v{version}/`.

Each provider-version source file has one unclassified Inventory document and one page at this phase. This deliberately avoids inferring business document type, location or business date before Phase 5. Duplicate source content preserves its own source audit row and page/document relationship; once the canonical page exists it reuses the canonical prepared artifact through `duplicate_of_page_id`.

Failures are sanitized. Corrupt, unsupported, oversized and identity-mismatched sources become terminal preparation failures. Missing Inventory source storage is retryable. Temporary storage files are removed before an error is returned; a finalized artifact without a committed page is never exposed as a successful page and is safely reused by idempotent recovery.

## Rollback

Migration `0035_inventory_document_prep` downgrades directly to `0034_inventory_drive_ingestion`. It removes only Phase 4 Inventory columns and restores the previous document constraints. Do not downgrade while prepared pages depend on the Phase 4 metadata unless the corresponding Inventory data has been retired under the normal deployment procedure.
