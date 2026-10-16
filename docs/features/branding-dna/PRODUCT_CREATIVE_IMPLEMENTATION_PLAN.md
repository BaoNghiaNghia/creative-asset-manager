# CAM — Branding DNA & Product Creative Studio
## Kế hoạch triển khai theo giai đoạn (v1.0)

**Ngày lập:** 2026-10-16
**Gắn với:** [Product Creative System Spec](./PRODUCT_CREATIVE_SYSTEM_SPEC.md) (source of truth cho nghiệp vụ mới) và [Branding DNA Readiness Audit](./READINESS_AUDIT.md).
**Tình trạng tại thời điểm lập:** CAM đã có Shop Registry theo tenant, worker registry/heartbeat và lease trả no-job; Scanner thực tế, listing evidence, Brand Pick/Best Seller, image generation theo 2 nhánh và supervised learning **chưa được chứng minh đang chạy production**. Tài liệu này là kế hoạch phát triển, không phải xác nhận release.

## Nguyên tắc delivery

- Mỗi phase phải có regression tests **trước hoặc cùng với code**, migrations non-destructive, RBAC server-side, idempotency, feature flag mặc định off và rollback.
- Pilot dùng shop có quyền, SKU và quyền sử dụng hình ảnh rõ ràng; dữ liệu không đủ phải hiển thị unknown, không fake record.
- Dùng PostgreSQL authoritative; R2 chỉ lưu media được phép; search/index là derived; giữ nguồn, revision và lineage.
- Không dùng một model/một prompt cho tất cả loại ảnh: shot adapter theo group, có QA contract và version.
- Chỉ promote khi tất cả gate của phase được xác minh, không nhầm UI/demo/heartbeat với capability đã sẵn sàng.

## Work packages / thứ tự bắt buộc

| ID | Phase | Deliverables | Acceptance gate | Rủi ro / Rollback |
|---|---|---|---|---|
| BD-00 | Contracts & fixtures | schemas Brand Pick, Best Seller, Product Master, Shot Plan, source manifest, candidate, label; mock data + contract tests | Source kind, version, tenant IDs rõ; fixtures đúng A/B; no frontend placeholder called live | Flagless, import-only code; revert commit |
| BD-01 | Shop evidence + access | approved platform source adapters, canonical identity, listing discovery, gallery order and variant snapshots; isolated scanner agent lease/checkpoints | Authorized 1 Etsy/Shopify pilot, 1 Amazon pilot khi đủ quyền; coverage thực và unknown rõ; no policy bypass | Flag off, drain agent, preserve evidence snapshots |
| BD-02 | Pick & Best Seller | Product/Image Pick actions+reasons+audit; sales/API/import or team-reported observation; source-quality taxonomy; cohort UI | Independent product/image picks; non-picked != rejected; sales rank != volume; tenants isolated | Hide feature; retain audit and observations |
| BD-03 | DNA & Playbook aggregation | 6 DNA layers, evidence/contradiction, confidence/coverage, cohort comparisons, draft/approved diff + immutable versions | 100% approved claims trace to permitted sources; no auto-approval; unknown preserved | Unbind draft/revert to last approved |
| PC-00 | Three Input & Product Master | Input wizard, artwork/SKU/placement/variants/context/requirements, validation, category packs | Blocks shots depending on missing factual source; Product Master immutable; correct platform and SKU | Disable creative flag, leave source assets untouched |
| PC-01 | SMS integration (A) | SMS mockup manifest adapter, multi-angle SKU mapping, hard router Studio/Detail/Information/Variations; text/layout rendering | A job sends **zero Scout requests**, missing angle => needs_input, no altered embroidery/false specs | Roll back adapter version; retain existing candidates |
| PC-02 | Reference Scout integration (B) | query planning per context, legal/right checks, Gemini classification, human ✓/×/AI, approved ref library; inpaint/replace | Only Lifestyle/UGC consume Scout refs; no approved/rights ref => needs_reference; protected product features intact | Disable B branch; refs/history retained by policy |
| PC-03 | Shot orchestration & gallery | configurable shot count (10 default), per-task lease, retry, pause, QA, immutable candidate history, final slot approval, ZIP/manifest | At least one full 10-shot pilot by category, no lost prior candidate on retry; partial failure visible | Stop queue, revert router, keep product masters |
| PC-04 | Output supervision | ✓ Pick, × Reject + reason, Unreviewed, compare candidates, filter by group, full audit, export feedback dataset | Unreviewed != Negative, separate Brand Pick vs Output Pick, no auto-DNA update | Disable labeling; preserve audit/labels |
| PC-05 | Learning governance | label quality, offline evaluation splits, rule proposals, review, canary, rollback | Candidate improvement beats baseline on holdout without lowering product fidelity; promotion approved; rollback tested | Withdraw rule release; pin old Playbook |
| PC-06 | Scale & governance | queue fairness, Gemini quotas, worker telemetry, billing/latency, platform/right retention, responsive/device QA | Verified canary, failure recovery, zero cross-tenant leaks, no unbounded storage or retries | Kill switch, drain queues, rollback release |

## Checkpoint triển khai BD-00 — 2026-10-16

- Đã thêm các hợp đồng dữ liệu **chưa được nối API/DB** tại `apps/api/app/modules/branding_dna/contracts.py`: tenant-scoped Brand Pick, Best Seller (unknown/proxy/verified phân biệt), Product Master, versioned Source Manifest, 6-group Shot Plan, routing SMS/Scout, Candidate lineage, Output Label và Learning Rule Proposal có manual-approval gate.
- Fixture 10 shot mặc định và unit/contract tests: `apps/api/tests/modules/branding_dna/fixtures/balanced_10_shots.json`, `test_contracts.py`.
- Contract-only: **không có** endpoint mới, migrations, shop scanning, SMS/Scout remote calls, generation queue, hay learning auto-promotion. BD-01 đến PC-06 và kiểm thử production theo từng phase vẫn pending; trạng thái tài liệu không xác nhận production live.

## Checkpoint triển khai BD-01 (partial) — 2026-10-16

- Đã có metadata-only authorized-export ingest, revision-idempotent listing snapshots, gallery image order/hash/rights placeholder, Alembic additive schema, tenant-scoped GET list/gallery API và Product Explorer đọc dữ liệu thật.
- API mới: `POST /api/v1/branding-dna/shops/{shop_id}/listings/import` (quyền cấu hình, chỉ shop owned/authorized), `GET /shops/{shop_id}/listings` và `GET /shops/{shop_id}/listings/{snapshot_id}/gallery` dưới prefix `/api/v1/branding-dna`. Snapshot bắt buộc `listing_key`, `source_revision`, `export_reference`, `title`, `category`, `captured_at` có timezone và `images` chỉ metadata SHA256, index từ 0 liên tục. Không gửi URL thật, raw bytes hoặc tenant ID; không network-fetch.
- Mọi record nhập có `source_kind=authorized_export`, `evidence_state=imported_unverified`, `coverage_state=unknown`, `media_available=false`. Quyền ảnh mặc định **unknown**, không được tự khẳng định permitted từ import. Các dữ liệu này **không** tự tạo Brand DNA claim, Bestseller, thumbnail hay cấp reference cho image generation.
- Chưa xong BD-01: source adapter chính thức Etsy/Shopify/Amazon, explicit platform authorization + legal gate, scanner dispatch, lease fencing, safe redirect/DNS policy, data/media verification, signed media storage, coverage audit, worker local browser. Worker heartbeat/lease vẫn giữ scan disabled.
- Production migration phải qua schema-head preflight + bảo toàn dữ liệu và safety gate; không chạy migration/downgrade destructive một cách tự động. Nếu chỉ push mã nguồn thì **không** xem như production đã được bật.

## Checkpoint BD-01 queue staging & lease fencing (partial) — 2026-10-16

- Thêm `bc_scan_tasks` (migration `0160_branding_dna_scan_tasks`) dạng non-destructive/additive; mỗi task tham chiếu bằng FK theo tenant/shop đến snapshot thật, idempotency theo snapshot + kind, mặc định **staged**. Không sửa dữ liệu Inventory/RRUGC.
- API `POST /api/v1/branding-dna/shops/{shop_id}/scan-tasks` chỉ chuẩn bị job cho snapshot đã nhập tại shop owned/authorized; `GET /shops/{shop_id}/scan-tasks` xem queue; `POST /shops/{shop_id}/scan-tasks/{task_id}/cancel` chỉ hủy staged. Product Explorer có nút **Chuẩn bị kiểm chứng · Không quét**. RBAC và tenant predicate áp dụng server-side.
- `task_service.py` có *internal-only* lease generation/fencing, checkpoint monotonic, revoke guard, bounded retry/backoff và thu hồi lease hết hạn. Không có public activation từ staged sang pending; các chức năng claim/checkpoint/retry được kiểm thử bằng task fixture nhưng chưa nối HTTP worker.
- Worker `POST /workers/{agent_id}/lease` vẫn trả `job=null, scan_enabled=false`. Không cài Playwright, không điều hướng URL, không fetch ảnh, không khai thác nguồn bên ngoài, không đổi quyền truy cập shop. Các task staged **không chạy** và không được tính là tiến độ scan.
- Còn thiếu để BD-01 hoàn tất: platform policy/access grant có kiểm chứng độc lập; chính thức Etsy/Shopify/Amazon adapter; source/redirect/DNS allowlist, signed media, durable worker outbox, adapter-to-lease gating, scanner QA và xác minh coverage thực. Do not promote to production scanner until gates pass.
- Release migration 0160 chỉ sau khi production hiện tại đã ở revision 0159, có backup/recovery drill, non-destructive preflight và post-deploy health gates. **Push main không đồng nghĩa migration production đã chạy.**

## Checkpoint BD-01 — Worker Lifecycle Outbox (partial), 2026-10-16

- Added `bc_worker_events` and migration `0161_branding_dna_worker_events`, additive after `0160_branding_dna_scan_tasks`; this stores only tenant/agent-scoped lifecycle event receipts, NOT listing contents or worker command payloads. API `POST /api/v1/branding-dna/workers/{agent_id}/events` accepts fixed enums and timestamps; supports replay ACK/sha256 conflict rejection, revoked-token rejection, 50 events/request, and a per-agent cap of 1,000 new events/day.
- Dedicated worker (Node 22.13+) has local SQLite WAL outbox (`synchronous=FULL`, 2,000-row maximum, 50 per batch), persists events before sending and deletes only after a complete/verified server ACK. Network failures, incomplete ACK and restarts do not erase pending rows. Outbox contains no token, URL, image, product metadata or arbitrary text.
- Source ingestion still requires separate authorization/policy review. No Playwright installed, no platform adapters, no scanner job dispatch, no pending activation, no real claim/checkpoint HTTP API, and no productive evidence uploads. `/workers/{agent_id}/lease` continues to return `job=null`, `scan_enabled=false`.
- Windows rollout needs pinned Node.js runtime QA, service permission/storage verification, server-side telemetry retention approval, per-task result outbox, authenticated adapter/policy gating, prod backup/schema check and a rollback drill. This is a code checkpoint, **not production scanner readiness or a production migration claim**.

## Checkpoint BD-01 — Shopify offline CSV Source Preview (partial), 2026-10-16

- Added **read-only Shopify product CSV adapter** `offline_shopify_csv_v1` at `apps/api/app/modules/branding_dna/shopify_csv_adapter.py` and scoped endpoint `POST /api/v1/branding-dna/shops/{shop_id}/source-preview`. No marketplace browser/API access or media download; CSV is parsed in memory, not stored as a snapshot.
- Source previews accept merchant-supplied Shopify product-export columns `Handle`, `Title`, `Variant SKU`, `Image Src`, `Image Position`, optionally `Type`. Max **2 MB, 1,000 CSV rows, 250 products**; detect malformed headers, SKU/gallery discrepancies and invalid media URLs. Image links are only hashed after exact HTTPS `cdn.shopify.com/s/files/` allowlist; hashes are URL locators, **not media asset hashes**. Preserve actual export gallery positions without inventing missing photos.
- Product Explorer includes a Shopify CSV file picker and shows preview product/variant/image-reference counts and validation issues. This is **not import into verified listings**. All previews always return `source_identity_verified=false`, `rights_verified=false`, `evidence_committed=false`, `scan_enabled=false`.
- Etsy/Amazon source adapter and their OAuth/seller-access gates are **not implemented**. A Shopify CSV export does not prove origin or authorization, even for a shop registered as `owned`/`authorized`. No API token is required or accepted, no new task is activated, and `/workers/{agent_id}/lease` remains `job:null`.
- This step makes no database migration and does not deploy services. Real source verification, signed media evidence with byte hashes, approved platform adapters, eligible pilot shop + browser/security QA, and production safety preflight remain required before enabling scanner functionality.

## Checkpoint BD-01 — Amazon & Etsy export-first pilot, 2026-10-16

- User-owned operational scope is **Amazon and Etsy only**. New-shop UI offers those two platforms, and Product Explorer previews Amazon inventory reports (CSV/TSV) or Etsy Shop Manager listing CSV. Prior Shopify records/legacy preview API remain for backward compatibility, but Shopify is no longer a new implementation priority; no destructive deletion.
- New read-only adapter `marketplace_export_adapter.py`: Etsy listing titles, optional SKU, IMAGE1..IMAGE99 ordering, optional listing ID; Amazon Seller Central inventory SKU, optional ASIN, product title, grouping same-ASIN seller offers. Missing Etsy listing IDs are marked **provisional_not_canonical**, missing Amazon ASIN is **seller_sku_only_unverified**. No generated sales/Best Seller ranking, no full-shop coverage claims.
- Platform + tenant scoped API `POST /api/v1/branding-dna/shops/{shop_id}/source-preview` accepts `etsy_listings_csv` or `amazon_inventory_report` (legacy Shopify remains). User-provided CSV/TSV is parsed in memory under 2 MB / 1,000 rows / 250 products, with strict header/row validation and capped report issues. Image URLs are not fetched and only approved Etsy CDN HTTPS locators are hashed; URL hashes are **not image byte hashes**.
- All responses explicitly set `source_identity_verified=false`, `rights_verified=false`, `sales_verified=false`, `evidence_committed=false`, `scan_enabled=false`, `coverage_state=unknown`. No task activation, external HTTP/OAuth calls or database migration.
- Follow-up for **Etsy**: obtain authorized Etsy Open API v3 key + seller OAuth scope (per platform policy), then build paginated listing/image adapter with rate-limit handling, no CAPTCHA bypass. For **Amazon**: seller authorization via SP-API, explicit marketplace and seller identity, then permitted Listings Items/Reports integration. Preview uploads do **not** grant those API permissions. Do not start the scanner until live authorization, lease/result outbox and security/restore gates pass.

## Interface contracts trước khi thực thi

### Source / data contracts

**SMS:** coordinate owner API/export format, image licensing, store/SKU/variant canonical ID, design hash, angle taxonomy (front/back/side/45/macro), color/material, placement, source timestamps and SHA256, TTL/storage. If SMS lacks a required view, stop specific shot rather than synthesize unknown product geometry.

**Scout:** explicit distinction between existing Pinterest Scout product-ref learning and Branding DNA's realistic-reference search. Source policy, image rights, non-AI confidence, face/privacy handling, no-repeat, negative training tags, approved evidence IDs. Scout may only serve Lifestyle and UGC/Human. Shop Scanner remains separate Windows worker.

**Sales:** authorization per provider, storefront/market/period, SKU mapping, category-specific ranking, verified vs proxy vs team-reported; default unknown, no fabricating order counts. No use of private metrics without shop consent.

**Platform:** marketplace role/format and content constraints versioned separately from aesthetic DNA; Etsy/Amazon/Shopify access permissions independently approved.

### Data & API security contracts

- Proposed bc_ tables: brand_picks, best_seller_observations, cohort_analyses, creative_playbook_versions; proposed pc_ entities in [spec](./PRODUCT_CREATIVE_SYSTEM_SPEC.md). Schema/migrations require validation against production heads before coding.
- Add proper permissions and tenant predicates on every read/write. Test tenant A cannot infer shop B/SKU/media/labels, including signed object URLs.
- Store secrets in scoped backend/agent credential stores, never in prompt/manifest/response logs; external URL ingestion retains SSRF guards, type/size limits, source URL redaction.
- Immutable approved profile versions and creative jobs pin all versions; no migration must destroy earlier approved profiles or generated images.
- Replay-safe job task identity and lease fencing; duplicated callbacks cannot issue double output labels/overwrites.
- Media retention/licensing by tenant and source; explicit deletion and export pathways, avoid retaining unrelated private faces.

## Required test matrix (must be implemented with each phase)

| Test suite | Cases |
|---|---|
| Brand Pick | product vs image Pick, unlike; toggle history; no-pick neutral; cross-tenant denied |
| Best Seller | verified sales / proxy rank / reported / unknown; historical period; null counts, same-category comparisons |
| DNA aggregation | source lineage, sample bias, duplicate variants, contradiction/unknown; immutable approvals |
| Input validation | missing artwork, SKU, placement, angle, claim source; valid category-specific variant |
| Branch routing | A four groups SMS-only; B two groups Scout-only; invalid group/permission denied |
| SMS & Scout adapters | denied image rights, missing asset, variant hash mismatch; 403/429/CAPTCHA needs operator |
| Generation | model failure, retries, duplicate submit, crash/restart, partial 10-shot job, cancel, no output loss |
| Quality | exact artwork/text, color, geometry, hands/occlusion and platform compliance; golden fixtures per category |
| Learning | output labels separate from brand Pick; unreviewed neutral; holdout leak checks; rollback rule |
| UI | 1440 desktop, 1024/tablet, 768 iPad, 430/390/360/320 phones; touch/keyboard node drag, pan vs drag, focus, no horizontal body overflow |
| Security | source URL spoofing/redirect SSRF, prompt injection in scraped copy, signed media scope, RBAC/tenant leak, secret redaction |

**Pilot metrics (targets, not measured):**
- Branding evidence: carry forward ≥95% eligible listing identity and ≥95% gallery order fixture accuracy; 100% approved claim provenance.
- Generation: 100% required shot source-branch routing; 0 invalid unverified measurements in Info; 0 lost candidates on retry; all exported images have manifest lineage.
- Learning: positive/negative labels complete with provenance; 0 implicit negatives; 0 auto-promotions; offline evaluation score established against baseline, not guessed.
- Operational: 0 cross-tenant leaks; bounded queue, quota, retry; correct stop/drain and alerts.

## UI navigation và work ownership

**Branding DNA** owns evidence, Pick, Best Seller, DNA/Playbook draft+approval. **Product Creative Studio** owns 3 Team Inputs, Product Master, shot planner, SMS/Scout branch execution, output candidates, team QA/picks and learning proposals. Shared objects are **versioned IDs**, never mutable global style state.

Roles (proposal):
- Shop Admin: authorize shop, managed integration, Best Seller import/source policy.
- Brand Curator: Brand Pick, review interpretations, propose/approve DNA/Playbook.
- Creative Lead: 3 Input, shot plan review, final output selection.
- Output Group Reviewer: Pick/Reject own image group, rationale.
- ML/QA Reviewer: evaluate proposed learning rules and canary outcome.
- Viewer: read-only, no scan/generate/pick/approve by default.

Place UI layouts in responsive workspace shell, with pinned breadcrumb and horizontal overflow only inside dense tables. No mobile actions hidden without alternative.

## Rollout sequence

1. Review contracts with Product Owner, SMS owner, Scout operator, Brand Curator and Security reviewer; freeze v1 schema + example manifests.
2. Build RBAC + tenant models + migrations and fixtures in staging, with no live scans.
3. Enable one **owned/authorized shop** as source of real DNA evidence; Add Pick/Best Seller with provenance.
4. Validate approved DNA + Playbook on held-out gallery images.
5. Enable Product Master and SMS-only outputs for 1 SKU/variant; inspect and manually approve.
6. Enable Scout branch only for approved eligible source references and 1 Lifestyle + 1 UGC task; manual rights/QA gate.
7. Pilot complete 10-shot gallery and supervised Pick/Reject; generate rule proposals only.
8. Canary new approved rules on small authorized shop subset; compare quality and rollback before expansion to more product categories and shops.

## Release / rollback checklist

- Verify schema head, negative RBAC tests, worker version, SSRF/download tests, gallery lineage, SMS/Scout routing and tenant object permissions.
- Ship feature flags **off**; activate only after evidence + platform consent + source manifest and QA gate.
- Record deployed commit, flagged tenants, migration version and last known working model/prompt/skill/Playbook versions.
- Roll back by disabling flags and agent leases, draining in-flight jobs at checkpoints, pinning last approved DNA/Playbook and old method rules; **do not delete source media, approved profiles or prior candidate history**.
- Downgrade additive tables only by a separately reviewed retention-safe migration; for production incidents prefer disabling/ignoring new writes until repair.
- Production QA must show last committed product/task, not heartbeat-only; do not claim complete scan for unknown denominator.

## Open questions for implementation phase (not blockers for documentation)

1. Stable SMS SDK/API path and machine/service credentials, licensing, multi-angle completeness.
2. Marketplace accounts/shops for authorized testing and whether Best Seller verified order data is available by provider.
3. Role owners for Brand Pick, DNA approval and learning rule promotion.
4. Default preset choices and platform-specific role requirements for different categories.
5. Model/provider budget and quality acceptance thresholds for product fidelity/realism.
6. Dataset rights and whether an eventual actual model fine-tuning phase is approved (v1 is rule/ranking learning only).
