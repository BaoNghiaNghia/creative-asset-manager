# Branding DNA — evidence-backed draft preview

The **Brand DNA > Input Readiness > DNA Draft** panel provides a **read-only,
deterministic evidence worksheet**, not an AI-generated or approved Brand DNA.

## Source classification

- **Verified listing metadata**: titles, product categories and immutable
  `listing:<snapshot_id>` citations can appear as observed product facts.
- **Human-approved shop RAG**: only document metadata, category and a
  `rag:<document_id>:0` citation are presented separately as context; a
  human approval does not independently verify product, sales or legal claims.
- **Unverified listing imports** are omitted entirely from product facts.
- **Image usage rights, visual style, customer demographics, Best Seller and
  Brand Pick** are not inferred from this preview and remain unresolved.

The endpoint is
`GET /api/v1/branding-dna/shops/{shop_id}/dna-draft/preview`. It requires
`ai_provider.configure`, scopes queries by tenant/shop, uses bounded source
samples, returns `Cache-Control: no-store` and never calls a model, accesses
image bytes, creates tasks or writes a DNA record.

The UI explicitly says **Chưa đủ dữ kiện** when there is no verified listing.
If the limited sample is truncated, the status remains blocked rather than
mistakenly presenting a comprehensive view.

## Gates before implementing a true generated DNA

1. A separately authorized validation flow for product evidence and media rights
   must establish proof and log provenance without changing marketplace access
   by declaration alone.
2. Human approval of source coverage and rules for visual/UGC use.
3. A versioned DNA draft model and approval flow with per-claim citations.
4. Model inference with structured output, full traceability and controlled
   retry once the above gates are met.
5. Isolated database restore rehearsal and full CI before production deploy.

This feature must **not** be described as working production image analysis.
