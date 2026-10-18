# Branding DNA — Shop Knowledge RAG (BD-RAG-01)
**Ngày rà soát:** 2026-10-18
**Vai trò:** Nguồn tri thức bổ sung độc lập theo shop; không thay thế listing/gallery evidence, Brand Pick hoặc Best Seller.

## A. Hợp đồng Input → Method → Output

### Input bổ sung, KHÔNG thành input thứ tư của Product Creative
Shop RAG lấy tài liệu từ shop owner/team: brand guideline, typography and brand voice, marketing/audience, verified product documentation, visual/creative rules, campaign briefs, FAQ and shop-specific policies. It does **not** alter the 3 team inputs (Product & Design, Marketing Context, Output Requirements).

| Input / nguồn | Trạng thái đầu vào | Trích xuất | Đầu ra |
|---|---|---|---|
| Shop product/listing gallery | authorized source, currently unverified export | product/variant/role/evidence | observed Product DNA evidence |
| Brand Pick | future human selection + reason | preference signals | Creative Playbook candidate |
| Best Seller | future independently verified sales/proxy | trend/source window | commercial signals |
| **Shop Knowledge RAG** | uploaded shop-scoped file, reviewer approves | PDF/DOCX/text extraction, chunked lexical retrieval with citations | reference context for DNA draft and eventual shot briefs |
| 3 team inputs | separate Product Creative job | Product Master/shot plan | 6 output groups (A SMS, B Scout) |

An approved RAG document **does not establish** product fact correctness, gallery role, sales volume, merchant authorization, usage rights or platform policy. Authority order: verified SKU/product evidence > verified platform requirements > mandatory team constraints > approved DNA Core > approved shop RAG references > inferred preferences. Conflicts create review actions, not silent overwrites.

### BD-RAG-01 released capabilities / precise boundaries
- Until dedicated RAG permissions are available, all document reads, upload and review require `ai_provider.configure`; ordinary viewers with `ai_operations.read` cannot view private documents.
- Tenant/shop-scoped upload to `bc_knowledge_documents` (original bytes, SHA256, category, provenance note, immutable chunks), strict 2 MiB/file, 30 active docs and 20 MiB active storage/shop, 60 lifetime docs and 40 MiB lifetime storage/shop.
- TXT/MD/CSV/JSON UTF-8, DOCX OOXML plain paragraph extraction, text PDF extraction by pypdf. No OCR; scanned PDFs are rejected if they yield no text.
- `pending_review → approved/rejected → archived`. Only explicitly approved documents enter retrieval. Reviewer identity, timestamp and decision note retained; archive revokes retrievability without destroying provenance.
- Deterministic 850-character chunks with short overlap and hashes. Query searches **only the selected tenant and shop's approved chunks** and returns `rag:<document_id>:<ordinal>` IDs, source filename/category/hashes.
- **Lexical retrieval**, not semantic embedding/vector search. Returns evidence snippets, not AI-generated answers; no external model calls, token-based charges or agent/job dispatch.
- Uploaded text is always treated as untrusted content: it cannot instruct the system, invoke tools, alter permissions, authorize crawling or mutate the approved DNA.
- v1 stores originals inside PostgreSQL under strict quotas to avoid adding a new cross-provider credential path; future object-store migration must have per-tenant encryption, retention and deletion policies.

### BD-DNA-00 — Evidence Input Readiness (Read-only)
The Brand DNA tab can inspect an individual shop's approved RAG corpus and imported listing statuses through `GET /api/v1/branding-dna/shops/{shop_id}/dna-inputs`. Output includes counts of approved/pending documents, listing snapshot and gallery manifest counts, approved RAG first-chunk citation excerpts, a stable hash of approved source identities, hard blockers and optional missing-signal notices.
- Requires `ai_provider.configure` because approved shop RAG can be private.
- No DNA inference, job dispatch, autosave or auto-approval, and **no claim that unverified listing exports are real observed product facts**.
- A deterministic fingerprint is not a DNA version. Missing optional RAG does not block DNA drafting if product evidence is independently verified.
- Brand Pick/Best Seller remain unavailable until their independently governed pipelines exist.

### Future BD-RAG-02 / BD-RAG-03 (NOT CURRENTLY DEPLOYED)
1. **Full source governance**: dedicated `branding_dna.knowledge_* ` roles, document owner rights/licensing declaration, GDPR/privacy deletion/purge, upload AV/CDR, audit append-only change log, object store encryption, provenance versioning and stronger PDF parser sandbox/quarantine.
2. **Semantic retrieval**: chunk embeddings (versioned, tenant isolated), hybrid BM25 + vector, citations, reranker quality evaluation, limits for data leakage, cost, and prompts.
3. **Brand DNA integration**: approved RAG + listing observations → evidence-backed DNA draft; separate `rag_claim` vs `observed_listing_claim`, disagreements and sample coverage visible, manual version approval. Pin doc hashes/versions in Creative Playbook/shot-plan provenance, never use unapproved RAG or alter product protected features.
4. **Per-asset multimodal RAG**: original PDFs with OCR, images, video, visual embeddings; image rights and people/PII gates, upload media on R2 with lifecycle policies.
5. **Ops**: ingestion queue, progress, retry, E2E mobile/browser QA, backfill, stale document expiry and alerts.

## B. Security and acceptance
- No raw arbitrary URL fetch from uploaded content. Formats and byte limits enforced server-side, no ZIP macro execution, zip-bomb size controls, bounded PDF pages/parsed text.
- Every select/update constrained by `tenant_id` and `shop_id`; tests must prove cross-tenant and cross-shop queries return empty/404.
- Approved sources only in retrieval; rejected/archived excluded. No automatic DNA Core or Creative Playbook writes.
- Source metadata/notes and file content are untrusted; citations are stable and reviewer can inspect each chunk.
- Uploaded marketplace policy cannot override official marketplace rules; customer audience notes are intentions, not demographic facts.
- Feature rollout must run migrations and pass isolated API tests, frontend typecheck/build and authenticated QA before production.
- Browser scanner remains disabled until separate authorization and dispatch gates.
