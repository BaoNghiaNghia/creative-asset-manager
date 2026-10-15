# CAM · Branding DNA — UI/UX & Technical Blueprint

**Trạng thái:** Tài liệu thiết kế v1.0 — chưa triển khai vào CAM production.  
**Prototype:** `./prototype/index.html` (bản gốc) và `apps/client/public/branding-dna/index.html` (CSP-compatible preview trong CAM); dữ liệu mô phỏng, không quét website thật.
**Điểm tham chiếu:** `creative-ai-hub` (Shop/Profile/Listing/Image/Platform Profile), `WorkspaceNavigation.tsx` và giao diện light-blue / Be Vietnam Pro của CAM.
**Trước triển khai:** xem [Worker Architecture](./WORKER_ARCHITECTURE.md) và [Pre-implementation Optimization](./PRE_IMPLEMENTATION_OPTIMIZATION.md). Giao diện chỉ là bản prototype, scanner chưa chạy thật.

## 1. Mục tiêu và phạm vi

Một workspace đa shop dùng để: (1) nhập/xác thực storefront Amazon, Etsy, Shopify; (2) điều khiển Browser Agent quét listing công khai hoặc đã được cấp quyền; (3) thu thập bằng chứng ảnh, cấu trúc gallery, metadata và trạng thái; (4) tổng hợp Brand DNA thành hồ sơ có phiên bản; (5) duyệt và áp Profile đã phê duyệt vào workflow tạo ảnh/video/UGC.

**Ranh giới:** không vận hành crawler thật trong prototype. Không mặc định khẳng định mọi listing của một shop có thể truy cập. Không tự vượt CAPTCHA, login, rate limit, robots hoặc quy định nền tảng. Những nội dung không tiếp cận được phải đánh dấu `inaccessible`/`needs_attention`.

## 2. Information architecture

**Global navbar:** Branding DNA (icon fingerprint), ngay dưới AI Operations hoặc trước Realistic Review UGC. Hiện preview dùng `ai_operations.read`; trước khi kích hoạt thật phải tách `branding_dna.read/run/configure/approve` ở frontend **và backend**. Đường dẫn cũ `/branding-character` được chuyển sang `/branding-dna`.

**Top tabs / nested routes:**

| Tab | Route được đề xuất | Nhiệm vụ | CTA chính |
|---|---|---|---|
| Overview | `/branding-dna` | Thống kê và tiến độ đa shop | Add Shop |
| Shop Library | `/branding-dna/shops` | Quản lý nguồn và phiên quét | Add Shop, View Shop |
| Browser Scanner | `/branding-dna/scanner?shop=:id` | Kiểm soát phiên quét/checkpoint | Start/Pause/Resume/Retry/Stop |
| Product Explorer | `/branding-dna/products?shop=:id` | Listing, gallery, role, exceptions | View Evidence, Re-analyze |
| Brand DNA | `/branding-dna/dna?shop=:id` | 6 Profile, bằng chứng, review/version | Approve, Export, Apply |

Dữ liệu chọn shop/filter được giữ trong URL; back/forward trình duyệt phải hoạt động. Header có breadcrumb, tên, mô tả 1 dòng và tối đa 2 CTA chính. Tránh modal fullscreen trên desktop.

## 3. UI screens

### 3.1 Overview

- KPI cards: Shops, discovered listings, AI analyzed, profile approved; phân biệt số thực và mô phỏng.
- Shop Library cards: logo/initials, platform, category, discovered, analyzed, coverage, trạng thái, Quick Open.
- Activity Summary: queue status, sự kiện Scan gần nhất, brand health/coverage.
- Empty: minh họa “Add your first shop”; không dựng số liệu giả khi backend trống.

### 3.2 Add Shop wizard (modal desktop, sheet mobile)

**Step 1 — Connect:** `display_name`, `platform`, `storefront_url`, `region`, `ownership_mode`. Chuẩn hóa URL, xác minh domain/platform, tránh nhầm seller cùng tên. Hiển thị tên thực tế lấy từ trang và yêu cầu xác nhận trước Scan.

**Step 2 — Scope:** `categories[]`, scan mode (Pilot 20, Full accessible), incremental frequency (manual/daily/weekly), media capture toggle, concurrency (được cap bởi server). Thống kê dự kiến số trang/sản phẩm là **estimate**, không cam kết.

**Step 3 — Review:** nền tảng, URL, category, policy, ngân sách, cảnh báo source access. `Create Shop` tạo bản ghi shop **chưa bắt đầu scan** nếu không bật `Start pilot after create` một cách rõ ràng.

Validation: tên 2–90 ký tự; URL `https` thuộc allowlist nền tảng; chống SSRF và URL redirect độc hại; chuỗi nhập được encode; `shop_id` duy nhất theo tenant+platform+canonical-store-identity, ngăn trùng.

### 3.3 Shop Library

- Desktop table + grid switch; tìm theo tên, nền tảng, trạng thái, danh mục; sort Name/Updated/Coverage/Products; pagination server-side.
- Mỗi shop có action menu: Open, Scan, Pause, Re-scan changed, View DNA, Archive (confirm). Không `delete` dữ liệu evidence mặc định.
- Trạng thái shop: `draft`, `discovered`, `scanning`, `paused`, `needs_attention`, `analysis_queued`, `profile_draft`, `approved`, `error`.
- Branding cá nhân cần để tách scope theo tenant, không chia sẻ cookie hoặc browser state giữa shop vô ý.

### 3.4 Browser Scanner

- Left (65–70%): active session, trạng thái, pipeline 4 giai đoạn, task progress, log timeline, danh sách listing jobs theo trạng thái.
- Right (30–35%): cấu hình, nguồn, browser worker, last heartbeat, checkpoint, category coverage, retry budget, limitations.
- `Start/Pause/Resume/Stop` idempotent. Stop dừng ở safe checkpoint và không phá các listing đã hoàn tất. Retry theo error code; cap exponential backoff, không vòng lặp vô hạn.
- Heartbeat còn hiệu lực không đồng nghĩa agent đang thu thập thành công; hiển thị **last successful product**, **last attempt**, **next retry** và **reason code**.
- Error taxonomy: `source_403`, `captcha_needs_human`, `session_expired`, `rate_limited`, `listing_not_found`, `parser_changed`, `image_unavailable`, `gemini_quota`, `model_unavailable`, `unknown`; không tự override bảo vệ website.
- Scan scope: shop → category → paginated listing URLs → listing gallery → image vision → aggregated Profile.

### 3.5 Product Explorer

- Source/category tree ở bên trái, list/grid ở giữa, product inspector ở bên phải; resizable divider tương tự Stage 3–5 Reviews.
- Card: thumbnail có quyền lưu và nguồn rõ ràng, listing ID/ASIN, title, platform, category, role count, AI confidence, last scanned.
- Product inspector: gallery carousel Hero/Detail/Lifestyle/UGC/Variation/Info, source URL, image evidence, classification JSON, exceptions, analysis version; phân biệt `observed` vs `inferred`.
- Từng hình phải có `image_index`, `image_role`, `image_hash`, `source_uri`, `evidence_id`. URL ảnh cần signed/cached theo chính sách CAM, tránh hotlink phụ thuộc third party lâu dài.
- Có `Re-analyze` chỉ job lỗi/chưa chắc chắn, không ghi đè evidence cũ.

### 3.6 Brand DNA (6 profile)

1. **Shop DNA:** vibe, positioning, tone, personality dimensions, core color/lighting/context, visual consistency.
2. **Category Style:** khác biệt sản phẩm mũ/áo/tote/crochet, form, chất liệu, quy chuẩn ảnh.
3. **Listing Visual:** số ảnh, thứ tự Hero/Detail/Lifestyle/UGC/Variation/Info, variation strategy.
4. **Image Style:** light, composition, background, human presence, props, smartphone/studio realism, stitch visibility.
5. **Platform:** thông lệ quan sát + yêu cầu chính thức có citation riêng (không suy diễn chính sách từ listing cạnh tranh).
6. **Seasonal/Campaign:** lớp overlay với `valid_from/to`, không làm hỏng evergreen Brand DNA.

Mỗi insight gồm `claim`, `observed_count`, `sample_count`, `confidence`, `evidence_ids`, `exceptions`, `updated_at`, `model_version`; các giá trị confidence trong prototype chỉ là ví dụ minh họa.

**Versioning:** immutable `v0.1`, `v0.2`, ...; trạng thái `draft` → `needs_review` → `approved` / `rejected` → `archived`. Chỉ profile `approved` được Apply to Workflow. Workflow run lưu `brand_profile_version_id`, category, selected reference, image role, constraints, user approval.

## 4. Responsive & dimensions

| Viewport | Navigation | Layout | Actions |
|---|---|---|---|
| ≥ 1440 px | Sidebar 225 px | 3 cột: source ~195px / center flex / inspector ~248px | Header CTA + action toolbars |
| 1024–1439 px | Sidebar 190px, tối giản khi cần | 2 cột hoặc Inspector chuyển xuống | Không tràn body, table nội bộ scroll |
| 768–1023 px | Sidebar icon 62px | 2 cột / stacked inspector | Toolbar wrap; tree horizontal filter |
| 480–767 px | Bottom navigation 60px | 1 cột | Tabs horizontal scroll; 2 KPI/cột |
| 320–479 px | Bottom navigation 60px | 1 cột, form stepper sheet fullscreen | Touch target ≥ 40×40px, input font ≥16px |

Use CSS `minmax(0,1fr)` + internal `overflow-x:auto`, not fixed width > viewport. `position: sticky` where appropriate; keyboard focus visible, `aria-` for dialogs/states, reduced motion. Visual style: `#f4f7fb` canvas; `#476ce3` primary; `#dfe7f2` dividers; border radius 9–13 px; 10–13 px text in dense tables, larger headings 21–25px; Be Vietnam Pro fallback. Preserve understandable status semantics via icon and text, not color alone.

## 5. Backend / database design (proposed)

- `bc_shops`: `id`, `tenant_id`, `platform`, `storefront_url`, `canonical_shop_id`, `display_name`, `region`, `category_tags`, `status`, `created_at`.
- `bc_browser_profiles`: `id`, `shop_id`, `worker_id`, `auth_mode`, `connection_status`, `last_heartbeat`, `last_product_success` (never store raw cookies in general DB fields).
- `bc_scan_runs`: `id`, `shop_id`, `run_type`, `status`, `cursor_json`, `progress`, `fail_count`, `started/finished_at`, `error_code`.
- `bc_listing_snapshots`: `id`, `shop_id`, `platform_listing_id`, `title`, `category`, `url`, `fingerprint`, `scanned_at`, `evidence_status`, `revision`.
- `bc_listing_images`: `id`, `listing_snapshot_id`, `order_index`, `image_role`, `object_key`, `hash`, `model_version`, `ai_confidence`.
- `bc_evidence_claims`: `id`, `shop_id`, `profile_layer`, `claim`, `observed_count`, `sample_count`, `confidence`, `evidence_image_ids`, `exception_ids`.
- `bc_profile_versions`: `id`, `shop_id`, `version`, `state`, `profile_json`, `approved_by/at`, `created_at`, `prompt_version`.
- `bc_profile_bindings`: `id`, `workflow_id`, `shop_id`, `approved_profile_version_id`, `category`, `role`, `constraints`.

API endpoints (suggested):

```http
GET    /api/v1/branding-dna/shops
POST   /api/v1/branding-dna/shops
GET    /api/v1/branding-dna/shops/{id}
GET    /api/v1/branding-dna/shops/{id}/listings
GET    /api/v1/branding-dna/shops/{id}/scan-runs
POST   /api/v1/branding-dna/shops/{id}/scan-runs
POST   /api/v1/branding-dna/scan-runs/{run_id}/pause
POST   /api/v1/branding-dna/scan-runs/{run_id}/resume
POST   /api/v1/branding-dna/scan-runs/{run_id}/stop
GET    /api/v1/branding-dna/scan-runs/{run_id}/events
GET    /api/v1/branding-dna/shops/{id}/profile-versions
POST   /api/v1/branding-dna/profile-versions/{id}/approve
POST   /api/v1/branding-dna/profile-bindings
```

Server-side stable pagination, ETag/cursor, proper tenant filters, idempotency keys, `RBAC read/manage/scan/approve/apply`. Use existing CAM queue + shared Gemini capacity and log retention; browser worker runs separately on authorized machine. API scan state should be backed by live heartbeat and per-listing progress, not inferred from UI button clicks.

## 6. Domain operations & quality gates

- Allowlist storefront domains and image hosts, enforce DNS/IP safety against SSRF and redirects, bounded bytes and timeouts.
- Prefer official API/integration for owned/authorized shops; public content browser only where permitted; operator handles CAPTCHA/login.
- Persistent per-shop cursor, state machine, checkpoint-on-pause, duplicate listing detection, content hash differential refresh.
- Gemini throttling: low-priority shared pool, explicit quota cap per tenant/shop, exponential backoff, `needs_review` for uncertain images.
- Image analysis never silently changes approved Profile; new observations generate a diff to a draft version.
- Evidence retention and third-party image copyright/privacy/compliance must be reviewed before storing/exporting.
- Browser control actions need audit: actor, timestamp, worker, browser, URL/ID, retry code, evidence ID; scrub cookies and secrets.

## 7. QA acceptance criteria

**Functional:** Add Shop validates URL; tenant A never sees B; duplicate detection; pause/resume durable; no repeat image analysis for unchanged hash; all gallery roles inspectable; approved Profile version immutable; workflow bindings honor approved version only.

**Responsive:** 1440×900, 1280×800, 1024×768, iPad 768×1024, 430×932, 390×844, 360×800, 320×568. No horizontal body overflow, no inaccessible actions, modal stepper scrolls, search remains usable with keyboard.

**Browser QA:** routes + visual regression; read-only QA default on production; destructive or scan-start actions only in staging; no false Under Construction from one timeout; verify fallback states (403/CAPTCHA, model quota, empty data, expired profile, unknown category).

## 8. Phased delivery

- **Phase 1 — Foundation:** navbar, guarded routes, CRUD Shops, responsive library, form validation, permissions, fixture-driven QA.
- **Phase 2 — Browser Pilot:** separate Playwright worker, safe storefront discovery, 1 Amazon + 1 Etsy pilot, scan controls, listing evidence and audit.
- **Phase 3 — Gemini/Profiles:** Listing + Image analysis, 5+1-layer Brand DNA, exception/risk review, profile diff/version and approval.
- **Phase 4 — Production link:** approved profile bindings for GatorHats Products/Reviews, image/video workflow QA and incremental refresh.

**Definition of done:** Not enough to show a “scan completed” counter; require evidence of listing discoveries, image analysis successes, source/error classification, stable human approval and actual workflow output consistent with approved Profile.

**Implementation contract precedence:** The P0 gates and canonical `bc_*` identity/claim model in [PRE_IMPLEMENTATION_OPTIMIZATION.md](./PRE_IMPLEMENTATION_OPTIMIZATION.md) override preliminary schema suggestions above; the dedicated worker protocol and marketplace access matrix in [WORKER_ARCHITECTURE.md](./WORKER_ARCHITECTURE.md) override aspirational “scan entire shop” UI copy. A shop may have partial coverage with an unknown denominator; always communicate uncertainty.