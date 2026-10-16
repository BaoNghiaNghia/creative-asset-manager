# CAM — Branding DNA & Product Creative Studio
## Đặc tả phát triển hệ thống hợp nhất (v1.0)

**Ngày chốt nghiệp vụ:** 2026-10-16
**Trạng thái:** Approved functional direction / implementation specification; **KHÔNG** có nghĩa scanner, image generation hoặc supervised training đã chạy thật.
**Phạm vi:** Branding DNA (shop intelligence) + Product Creative Studio (tạo bộ ảnh cho nhiều shop, nhiều loại sản phẩm).
**Tham chiếu:** [UI/API Blueprint](./BRANDING_DNA_UI_SPEC.md), [Pre-implementation Gates](./PRE_IMPLEMENTATION_OPTIMIZATION.md), [Dedicated Scanner Worker](./WORKER_ARCHITECTURE.md), [Readiness Audit](./READINESS_AUDIT.md), [Implementation Plan](./PRODUCT_CREATIVE_IMPLEMENTATION_PLAN.md). Tài liệu này bổ sung nghiệp vụ; các P0 security/evidence gate đã có vẫn giữ hiệu lực.

## 1. Mục tiêu và quyết định đã chốt

Hệ thống nhận dữ liệu được cấp phép từ shop, tạo một **Brand DNA Core** có bằng chứng và một **Creative Playbook** có phiên bản. Team chỉ cần cung cấp **3 nhóm Input** để tạo bộ ảnh sản phẩm (thường 10 ảnh, có thể cấu hình), hệ thống tự lập shot plan và đi qua **2 nhánh xử lý tách biệt**:

- **Nhánh A — SMS Mockup:** sử dụng **ảnh mockup sản phẩm đã có artwork/chi tiết thêu**, nhiều góc thật được cung cấp từ hệ SMS (tên hệ thống nguồn; không suy diễn SMS là gì). Chỉ tạo nhóm **Studio/Product, Detail/Macro, Information/Infographic, Variations/Collection**. **Không dùng Scout**.
- **Nhánh B — Reference Scout:** tìm, lọc, duyệt ảnh tham khảo realistic cho **Lifestyle và UGC/Human**, rồi dùng kỹ thuật image editing / inpaint / product replacement với Product Master. **Không cấp reference Scout cho 4 nhóm còn lại**.
- **Output Pick/Reject** theo từng candidate tạo tập nhãn **Supervised Learning**. Vòng này đề xuất tối ưu phương pháp, reference retrieval, shot plan và Creative Playbook, **không tự ghi đè Brand DNA Core**.

Hệ thống phải làm việc với mũ, áo, túi, ly, trang sức, đồ gia dụng, điện tử, mỹ phẩm... thông qua category schema có thể mở rộng; không hardcode logic “mũ thêu” vào toàn bộ sản phẩm. Artwork có thể là thêu / in / logo / hoa văn tùy category.

**Ngoài phạm vi của bản tài liệu-only này:** triển khai crawler hoạt động, kết nối thật SMS, huấn luyện/fine-tune model, bật cron scan, thêm dữ liệu production, deploy ứng dụng. Chỉ khi các mốc triển khai sau vượt gate mới được bật.

## 2. Node diagram — luồng end-to-end

~~~mermaid
flowchart TB
  S[Shop Registry / Authorized shop] --> D[Listing & Image Evidence]
  D --> BASE[All Products - baseline]
  D --> PICK[Brand Pick - product/image + reason]
  SALES[Verified sales / allowed marketplace signals] --> BEST[Best Seller - source + window + confidence]
  D --> BEST
  BASE --> INTEL[Brand Intelligence / evidence aggregation]
  PICK --> INTEL
  BEST --> INTEL
  INTEL --> DNA[Approved Brand DNA Core]
  INTEL --> PLAY[Approved Creative Playbook]

  I1[Input 1: Product & Design] --> VAL[Validate & Product Master]
  I2[Input 2: Marketing Context] --> PLAN[AI Creative Brief & Shot Planner]
  I3[Input 3: Output Requirements] --> PLAN
  VAL --> PLAN
  DNA --> PLAN
  PLAY --> PLAN

  PLAN --> ROUTE{Output group router}
  VAL --> SMS[SMS source: embroidered/product mockups - real angles]
  SMS --> A[A: Mockup based methods]
  ROUTE --> A
  A --> ST[Studio / Product]
  A --> DT[Detail / Macro]
  A --> IN[Information / Infographic]
  A --> VR[Variations / Collection]

  ROUTE --> Q[Scout query from approved shot brief]
  Q --> SC[Reference Scout - permitted source]
  SC --> REV[Gemini filter + team approve / reject / AI tag]
  REV --> LIB[Approved realistic reference library]
  LIB --> B[B: Image edit / inpaint / product replacement]
  VAL --> B
  B --> LS[Lifestyle]
  B --> UGC[UGC / Human]

  ST --> QA[Image QA and review]
  DT --> QA
  IN --> QA
  VR --> QA
  LS --> QA
  UGC --> QA
  QA --> GAL[Candidates + approved final gallery]
  GAL --> LABEL[Output Pick / Reject / Unreviewed]
  LABEL --> LEARN[Supervised learning dataset & offline evaluation]
  LEARN --> PROPOSE[Draft learning rules / playbook revision]
  PROPOSE --> APPROVE{Human approval and canary}
  APPROVE -->|Approved| PLAY
  APPROVE -->|Rejected| LEARN
~~~

**Định nghĩa nguồn:** Shop scanner thu thập dữ liệu của shop cho DNA; Reference Scout chỉ tìm **ảnh ngữ cảnh** cho nhánh realistic. Hai pipeline này có permission, nguồn, retention, lịch sử và worker riêng. Một hình được tìm thấy công khai không đồng nghĩa có quyền tái sử dụng hoặc tái tạo gần giống.

## 3. Branding DNA — ba lớp tín hiệu đầu vào

| Signal | Cấp chọn | Ý nghĩa đúng | Điều cấm suy diễn |
|---|---|---|---|
| All Products | Shop > category > product > variant > gallery | Style thực tế theo nguồn, tần suất, ngoại lệ; mẫu đại diện | Không đánh đồng ảnh trùng biến thể thành nhiều sản phẩm độc lập |
| Brand Pick | **Product Pick** hoặc **Image Pick** (+ reason + actor + timestamp) | Team cho biết thiết kế/ảnh nào nên được chú ý; ưu tiên sáng tạo | Chưa Pick = **unreviewed**, không phải negative; Pick không chứng minh bán chạy |
| Best Seller | Product/variant + metric/source + marketplace/category + kỳ đo + observed_at | Tín hiệu thương mại có provenance | Không coi marketplace rank là doanh số; không khẳng định ảnh gây ra doanh số |

### 3.1 Brand Pick

- Product Pick: lựa chọn concept, artwork, overall listing. Image Pick: chỉ chọn một hoặc nhiều gallery image cụ thể (Hero, Detail, Lifestyle, UGC, Info, Variation).
- Reason enum gợi ý: good_design, good_photography, brand_representative, good_layout, good_lifestyle, other; cho phép ghi chú.
- Có lịch sử bật/tắt Pick, người thao tác, thời điểm, source revision; không dùng lượt click vào card làm Pick.
- Brand Pick **khác** Output Pick (ảnh sau Generate); không trộn dataset hoặc metric.

### 3.2 Best Seller

- Mức nguồn: **verified_sales** (được shop ủy quyền và cho phép đọc); **marketplace_proxy** (chỉ số công khai như rank, không phải order volume); **team_reported** (tự gắn, cần xác minh); **unknown** (không có dữ liệu).
- Mỗi quan sát lưu: metric_kind, metric_value (nullable), unit, ranking_category, marketplace, region, observed_at, period_start/end, source_uri/source_id, access_mode, confidence_level, verification_state.
- Phân biệt bestseller theo **category** và **khoảng thời gian** (7/30/90 ngày, season); rank tốt của mũ không so thẳng với rank của ly.
- Dữ liệu doanh số riêng tư không được trộn giữa tenants; API chính thức/được cấp quyền ưu tiên; competitor có thể chỉ có proxy/unknown.
- Nếu không xác minh được: hiển thị “Team-reported” hoặc “Unknown”; không tự đưa vào tập “verified top seller”.

### 3.3 Suy luận Brand Intelligence

1. Chuẩn hóa canonical shop/listing/variant, giữ snapshot/URL/hash/order và provenance.
2. Phân nhóm mẫu theo category, thời gian, gallery role và variant; khử ảnh trùng exact/perceptual hash để không lệch mẫu.
3. Tách observed vs inferred vs unknown cho mọi feature.
4. Báo các trục **Trait Strength**, **Evidence Confidence**, **Coverage** độc lập; không coi score cao là “tốt”.
5. So sánh 4 cohort có chứng cứ: picked+bestseller, picked+not verified bestseller, not picked+bestseller, baseline. **Unknown bestseller** là nhóm riêng; không kết luận không bán chạy.
6. Công bố supporting evidence, opposing evidence, sample numerator/denominator, selection bias, thời điểm, prompt/model version. Mỗi kết luận quan trọng có đường dẫn ngược đến listing/image nguồn.
7. Tạo **Brand DNA Core** (đặc điểm nhất quán) và **Creative Playbook** (team preference, thương mại, creative patterns đáng thử) thành **draft độc lập**; human review và approve immutable version.

**Không áp dụng trọng số tĩnh kiểu “Pick + Best Seller = 2x tốt hơn”**. Tín hiệu bán chạy chỉ là tương quan, không chứng minh ảnh nào khiến bán tốt (giá, ads, season, reviews là confounders). Hiệu quả thật của image creative cần đo bằng A/B hoặc dữ liệu sau xuất bản.

### 3.4 Sáu lớp Brand DNA (giữ nguyên hợp đồng hiện có)

1. Shop DNA: vibe, tone, palette, visual consistency, định vị quan sát được.
2. Category Style: form, vật liệu, typography, stitch/print placement, chuẩn theo nhóm sản phẩm.
3. Listing Visual: vai trò gallery, thứ tự, cách trưng bày biến thể.
4. Image Style: lighting, background, framing, camera, people/props, realism.
5. Platform: hành vi quan sát được vs official rules (cần citation riêng).
6. Seasonal/Campaign: overlay theo valid_from/valid_to; không ghi đè evergreen DNA.

Mỗi insight có claim, observed_count, sample_count, confidence, evidence_ids, contradiction_ids, model/prompt version, state và reviewer; weak evidence hiển thị unknown/needs_review. Profile approved giữ nguyên; scan mới tạo diff/draft.

## 4. Ba nhóm Input từ Team — hợp đồng tối giản

| Input | Team cung cấp | AI được tự đề xuất | Gate bắt buộc |
|---|---|---|---|
| **1. Product & Design** | artwork / chi tiết thêu / logo, loại sản phẩm, placement, mẫu gốc hoặc SKU, reference có nhiều góc, màu, kích thước design nếu cần, variant | phân loại category, nhận dạng features, gợi ý góc còn thiếu | Giữ chính xác logo/chữ, form, vật liệu, màu, placement, scale; thông tin không xác minh được phải hỏi/đánh dấu missing |
| **2. Marketing Context** | bán cho ai, dịp nào, cảm xúc/thông điệp, thị trường và bối cảnh đặc biệt (nếu có) | diễn giải creative brief, model appearance khi phù hợp, props, scenes, ánh sáng | Đối tượng là creative intent; không suy luận demographic là fact |
| **3. Output Requirements** | group bật/tắt, số ảnh mỗi nhóm, Amazon/Etsy/Shopify..., tỷ lệ khung/kích thước/khung chữ/yêu cầu đặc biệt | shot count distribution, shot order, scene variants, per-role method | Tổng ảnh hợp lệ; platform rules đối chiếu; Info claims có nguồn |

**Input 1** dùng cho mọi nhánh; SMS Mockup là **nguồn derivative đã có chi tiết thiết kế**, không thay thế dữ liệu gốc của Product Master. Với sản phẩm không thêu, nguồn là mockup đã áp artwork tương đương theo loại vật liệu.

**Thiếu dữ liệu:** không tạo phỏng đoán như thật. Mark incomplete nếu thiếu góc hoặc size cho một output cụ thể; cho phép những nhóm không phụ thuộc vẫn chạy. Cho phép team override creative style per job nhưng không làm đổi DNA approved.

### Input schema đề xuất (hình dạng logic, không phải endpoint đã hoạt động)

~~~json
{
  "shop_id": "shop-uuid",
  "brand_dna_version_id": "approved-version",
  "playbook_version_id": "approved-version",
  "product": {
    "sku": "SKU-001",
    "category": "hats",
    "artwork_asset_ids": ["asset-id"],
    "base_product_ref_ids": ["asset-id"],
    "placement": {"surface": "front", "anchor": "center"},
    "variants": [{"sku": "SKU-001-NAT", "color": "Natural"}],
    "protected_features": ["artwork_text", "hat_form", "thread_color"]
  },
  "marketing_context": {
    "audience": "Dads",
    "occasion": "Father's Day",
    "tone": ["funny", "warm"],
    "market": "US"
  },
  "output_requirements": {
    "platform": "etsy",
    "groups": [
      {"group": "studio", "count": 2},
      {"group": "detail", "count": 2},
      {"group": "lifestyle", "count": 2},
      {"group": "ugc_human", "count": 2},
      {"group": "information", "count": 1},
      {"group": "variations", "count": 1}
    ],
    "total": 10
  }
}
~~~

Hệ thống chuẩn hóa dữ liệu vào **Product Master** bất biến theo từng job revision; prompt, model, source mockup và output phải reference version này để giữ một SKU nhất quán qua nhiều ảnh.

## 5. AI Method — product-first / role-aware

1. **Input Validation:** media rights, asset existence, design text/logo, SKU/color, source image angles, context/claims, platform constraints. Fail-closed tại shot thiếu reference/claim.
2. **Product Understanding / Product Master:** phân loại category, form, materials, placement, exact design, protected regions; giữ nguồn ảnh/thông số từng feature và uncertainty.
3. **Merge & Contextualize:** Approved DNA + Playbook + 3 Input, resolve conflicts theo thứ tự **verified product facts > approved platform policy > mandatory team constraints > DNA > creative preference**. Xung đột được đưa ra review, không âm thầm sửa.
4. **AI Creative Brief & Shot Planner:** tạo danh sách ảnh có role, angle, framing, method, reference requirements, priority, acceptance constraints. Team có thể duyệt/chỉnh từng shot trước generate.
5. **Route by Output Group:** hard-routing theo bảng ở mục 6. Không có fallback “lén” sang Scout đối với A.
6. **Generate per-shot:** job riêng, model/skill version riêng, retry/cancel/pause riêng; giữ ảnh cũ và candidate history.
7. **Per-shot Quality Gate:** giữ đúng sản phẩm, artwork, kích thước, location, đủ thông tin nguồn, đúng vai trò, kiểm tra realism/visual artifact và platform rules.
8. **Human Review & Export:** chọn bản final, xuất gallery 10 ảnh/nhóm/ZIP/manifest; lịch sử approval, source, costs.
9. **Supervised Learning:** xem mục 8; offline evaluate trước promote.

## 6. Output Groups, phương pháp cố định và input phụ

| Group | Default | Source/Method | Input/QA quan trọng |
|---|---:|---|---|
| **A1 Studio / Product** | 2 | SMS multi-angle embroidered mockup → background cleanup, controlled retouch, compositing | Ảnh SMS đúng SKU/design/view; nền và shadows, màu chuẩn; **Scout forbidden** |
| **A2 Detail / Macro** | 2 | SMS có chi tiết thật → validated crop/macro/texture enhance | Không bịa đường chỉ/khóa/chất liệu; thiếu detail phải bổ sung; **Scout forbidden** |
| **A3 Information / Infographic** | 1 | SMS + dữ liệu spec đã verified → deterministic text/icon/layout rendering | Chữ, thông số, kích thước thật; claims có nguồn; **Scout forbidden** |
| **A4 Variations / Collection** | 1 | SMS của các SKU/variants đã verified → arrange/compose | Không tạo màu/SKU ảo; nhãn màu khớp ảnh; **Scout forbidden** |
| **B1 Lifestyle** | 2 | Scout approved real-scene reference → inpaint/replace với Product Master & shot mask | Bối cảnh phù hợp context, composition thật, không copy quá sát ảnh người khác |
| **B2 UGC / Human** | 2 | Scout approved human-use reference → inpaint/replace + realism QA | Interaction hợp lý, tỷ lệ đúng, synthetic disclosure khi cần, không tạo review giả |

**Default balanced:** 2+2+1+1+2+2 = 10 ảnh. Shot Plan có thể điều chỉnh count (kể cả count=0), thêm future group Campaign/Packaging nếu được duyệt. Bảng chỉ mô tả ví dụ vận hành, không phải schema ấn định 10 ảnh với mọi category.

### Nhánh A — SMS Mockup dependency contract

SMS cần cung cấp manifest có tenant/shop/product/SKU/variant, artwork/design hash, applied placement, product angle (front/side/back/45/macro), source image dimensions/hash, color/material tags, object key hoặc URL được cấp phép, captured/generated time và rights/retention. Chỉ chấp nhận ảnh có design **đã gắn** lên sản phẩm; các góc được reconcile vào cùng revision. Nếu thiếu góc để thực hiện shot thì báo missing_view và xin bổ sung, không tự suy ra sản phẩm/góc khuất. Thao tác cleanup/retouch không được làm lệch chi tiết thêu/print.

### Nhánh B — Reference Scout dependency contract

Shot Planner sinh query từ **context + image role + category + DNA/Playbook** (không dựa trên SKU keyword một cách máy móc). Scout có nguồn và quyền truy cập theo chính sách, history no-repeat, separate worker/profile; mỗi reference có source URI, captured_at, consent/license status, visual score, human-present, face/privacy flags, scene tags, AI-likelihood và kiểm duyệt ✓ approve / × reject / AI. Chỉ approved & use-permitted refs vào library. Sau đó dùng mask, placement constraints, Product Master để thay sản phẩm; mọi output cần source lineage và realistic/product fidelity QA.

**Chính sách:** không vượt login/CAPTCHA/rate limit; không thu thập/lưu media hoặc thông tin cá nhân trái quyền; sử dụng inspiration không đồng nghĩa quyền tái sử dụng nguyên ảnh. Nếu thiếu reference hợp lệ, trả needs_reference (không âm thầm tạo ảnh giả từ nguồn không được duyệt).

## 7. Gallery, Output Pick và nguồn gốc dữ liệu

Ba lựa chọn trong gallery: **Positive Pick (✓)**, **Negative Reject (× + reason)**, **Unreviewed**. Unreviewed là default, tuyệt đối không trở thành Negative. Có thể Pick nhiều candidate của cùng shot; nhưng chỉ 1 final được duyệt (hoặc business rule explicit). Pick/Reject là action audited, không xóa ảnh gốc hay làm thay đổi Brand DNA.

- Nguyên nhân reject đa nhãn: wrong_product_form, artwork_changed, wrong_text, wrong_color, wrong_placement, fake_embroidery, unnatural_hands, scene_mismatch, poor_composition, low_resolution, brand_mismatch, policy_or_claim_error, other.
- Feedback record: tenant, shop, product, SKU/variant, job, shot, group, candidate, source branch, SMS asset IDs hoặc scout refs, image/prompt/model/skill version, label, reasons, notes, rater, timestamp.
- Export chỉ dựa trên các ảnh đã được final approve và manifest; còn thiếu slot sẽ báo incomplete thay vì fake success.

## 8. Supervised Learning — 3 lớp học độc lập

| Lớp | Training/analytics signals | Tác động được phép |
|---|---|---|
| **Brand DNA Learning** | Shop evidence từ crawler + Brand Pick + Best Seller có provenance | Đề xuất DNA Core draft (evidence-based) và Creative Playbook draft. Human approves, immutable versions |
| **Creative Preference & Commercial Intelligence** | Cohort: All / Picked / Best Sellers (verified/proxy/team-reported) + category/period | Đề xuất creative pattern, shot/ref preference; không tự coi là causal sales uplift |
| **Output Supervised Learning** | Output Positive/Negative + rejection reasons + method/refs/version | Tối ưu routing trong phạm vi cho phép, shot planner, ranking, prompt/config, candidate selection theo group |

**Loop chuẩn:** collect labels → deduplicate/sanitize → hold-out split theo shop/product/creative family (tránh near-duplicates leak) → rule candidate hoặc model evaluation → offline QA against baseline + safety regression → human approve → canary theo tenant/shop/group → observe → promote/rollback. V1 là **feedback-driven rule/ranking optimization**, không tự xưng là model fine-tuning. Nếu sau này có train model thật, bắt buộc có dataset license, evaluation plan, compute/budget và phê duyệt riêng.

**Học SMS vs Scout tách riêng:** nhánh A học đúng SKU/view/macro crop/retouch/layout; nhánh B học scene suitability/reference retrieval/mask/inpaint realism. Kết quả image-generated không tự làm “bằng chứng rằng shop vốn có phong cách đó”.

## 9. Kiến trúc dữ liệu đề xuất

PostgreSQL là nguồn chuẩn cho jobs, identity, snapshot, evidence, profiles, labels và approvals; R2/provider-managed storage giữ media theo permission/retention. Elasticsearch/embeddings là projection có thể xây lại. Schema dưới đây là **đề xuất**, chưa có migration.

### Branding DNA — kế thừa namespace bc_ hiện có
- Giữ: bc_shops, bc_browser_agents; và các bảng evidence/profile trong [optimized model](./PRE_IMPLEMENTATION_OPTIMIZATION.md).
- Bổ sung: **bc_brand_picks** (target_type=product/image, target_id, action, reason, actor, event history); **bc_best_seller_observations** (source quality, metric, period, verified state); **bc_cohort_analyses**; **bc_creative_playbook_versions**; **bc_profile_change_reviews**.
- Immutable approved profile & playbook, source lineage, tenant FK, distinct SKU vs variant vs image identity.

### Product Creative — namespace đề xuất pc_
- **pc_product_masters**; **pc_product_references** (verified source assets & protected features); **pc_sms_mockup_manifests**.
- **pc_creative_jobs** (3-input snapshot, DNA/Playbook pinned version); **pc_shot_plan_versions**; **pc_shot_tasks** (group, branch, state, lease, attempts).
- **pc_scout_reference_bindings** (approved/use rights), **pc_generated_candidates** (never silently overwrite), **pc_quality_assessments**.
- **pc_output_labels** (positive/negative/unreviewed by absence); **pc_final_selections** (one per active shot); **pc_export_manifests**.
- **pc_learning_rule_proposals**, **pc_learning_evaluations**, **pc_learning_deployments** (human-approved promotion/rollback).

**Constraints:** tenant-scoped FK and query predicates for every table; uniqueness on canonical shop/listing/variant and job+shot+revision; hash dedupe, no filename-based identity. Job idempotency key includes tenant+shop+SKU+variant+product_master_version+shot_plan_version+shot_id+source_revision+method_version. Store external references as scoped IDs and audit-safe URL hashes; credentials stay in dedicated secure provider/agent stores, not job payloads.

## 10. Proposed module interfaces và API contract

Năm mô-đun trách nhiệm tách biệt, giao tiếp bằng typed versioned contracts, không dùng frontend state làm nguồn chuẩn:

1. **BrandEvidenceIngestion**: shop/source adapter → listing/image evidence; no universal public crawling assumption.
2. **BrandIntelligence**: evidence + pick + bestseller → DNA draft & Playbook draft with diff/confidence; immutable approval.
3. **ProductMasterResolver**: 3 Input (artwork+SKU+context+requirements) + SMS manifests → verified product model & missing-info report.
4. **ShotOrchestrator**: approved DNA/Playbook + Product Master → shot plan, hard branch selection, isolated job queues, retriable delivery.
5. **CreativeFeedbackLearning**: output labels → evaluated rule proposals → manual approval/canary/rollback.

**API proposals only (not live):**

- Branding: GET/POST /api/v1/branding-dna/shops/{shop_id}/picks; GET/POST /shops/{shop_id}/best-seller-observations; GET /shops/{shop_id}/cohorts; GET /shops/{shop_id}/dna-versions; POST /dna-versions/{version_id}/approve; analogous playbook-versions routes.
- Creative: POST /api/v1/product-creative/product-masters; POST /jobs; GET /jobs/{job_id}; POST /jobs/{job_id}/shot-plan; POST /shot-tasks/{id}/retry; GET /jobs/{job_id}/candidates; POST /candidates/{id}/labels; POST /shot-tasks/{id}/select-final; POST /jobs/{job_id}/export.
- Learning: GET /learning/evaluations; POST /learning/proposals/{id}/approve; POST /learning/deployments/{id}/rollback.

Tất cả action cần RBAC riêng: branding_dna.read, manage, scan, pick, bestseller.manage, approve; product_creative.read, manage, generate, review, export, learning.approve. Không cấp scope chỉ vì người dùng đã đăng nhập; enforce server-side + audit.

## 11. UI/UX và responsive

- **Branding DNA:** Overview, Shop Library, Scanner, Product Explorer (All / Picked / Best Sellers / Picked + Best Sellers / Unknown; thời kỳ/category/market/source filters), DNA Core, Creative Playbook (evidence, comparison, approval).
- **Product Creative Studio:** Stage 0 Input → Stage 1 Product Master verification → Stage 2 Shot Planner (group counts) → Stage 3 Generation (SMS A / Scout B, queue/job status) → Stage 4 Gallery Pick/Reject & Final selection → Stage 5 Learning dashboard/rules review. Stage numbers đề xuất riêng, không đổi Stage hiện hữu của GatorHats.
- Each product card: Pick state/reason, Best Seller source/period/confidence, listing/gallery roles, selected variants, source URL; every insight opens supporting/contradicting images.
- Group cards: input source, template/skill+model version, status, output thumbs, elapsed/cost, retry reason, approval; multi-select and zoom; never collapse unavailable source with job success.
- Diagram UX: node drag/drop, connectors follow positions, pan/zoom/minimap, semantic labels, Save Layout, Reset, export JSON; **diagram is planning UI**, not evidence of operational scanner.
- Support desktop ≥1440, tablet 768–1023, phone 320–767; touch drag vs canvas pan, accessible keyboard move, no page overflow; loading/empty/error/permission/blocked states. Use [CAM Multi Device Standard](../../operations/CAM_MULTI_DEVICE_STANDARD.md) and [QA](../../operations/CAM_MULTI_DEVICE_QA.md).

## 12. Quality, safeguards và acceptance criteria

### Data & Brand DNA
- No claim without source evidence; no marketplace sales proxy labeled verified revenue; no Pick=Best Seller equivalence.
- Handle missing/unknown denominators, failed shop enumerations, season/category stratification, coverage/confidence transparency.
- Canonical identity fixture target inherited from existing pilot gate: ≥95% eligible listing identity, ≥95% gallery order in verified fixtures; **100% approved claims traceable to authorized evidence**; 0 cross-tenant leaks, 0 auto-approvals.

### Creative routing & content
- **Hard gate:** every Studio/Detail/Info/Variations shot has SMS mockup manifest; **zero Scout ref IDs or Scout fetch invocations** in A.
- **Hard gate:** every Lifestyle/UGC shot binds approved/use-permitted Scout ref; **no A-style SMS-only substitute** when missing reference (unless separately reviewed future feature); Product Master governs target product.
- For all outputs: exact SKU, logo, artwork text, placement, material/color and dimensions; no invented measurements, features, reviews or testimonials.
- Every candidate traces to Product Master, shot plan, DNA/Playbook versions, source refs, prompt, model/skill version, QA result. Partial failure retains previous outputs; retry only failed shot/group.
- Unreviewed never counted Negative. Output labels must not mutate Brand DNA Core without evidence review. Approved versions remain immutable.

### Ops/security
- Separate Windows Branding DNA scanner worker from Pinterest Scout; isolate browser profiles/cookies; heartbeat != successful scan.
- Policy-aware API-first shop adapters, bounded retries/rate budgets, no CAPTCHA/login/robots bypass, media rights/privacy/retention, signed R2 access, SSRF protections and secret redaction.
- UI readiness gate: Shop registry/worker heartbeat being live does **not** imply scanner/learning/image-generation is deployed. Fail closed until all P0 integrations and verified canary complete.

## 13. Definition of Done

Feature is only **operational** when an authorized shop scan generates audited listing+gallery evidence; Brand Pick and Best Seller are source-verifiable; approved DNA/Playbook version can be pinned to a job; 3 Inputs produce a validated Product Master; Shot Planner routes six groups correctly (A SMS-only, B Scout-only); generation yields traceable QA'd candidates; team can Pick/Reject/select finals, export 10 images; learning proposals are evaluated, approved, canaried and reversible; tests pass across desktop/tablet/mobile and tenant isolation.

## 14. Explicit non-goals / open decisions

- No promise of “all public products scanned” without a known accessible denominator.
- No automatic model training or change to approved shop DNA from team output picks.
- No made-up revenue, conversion uplift or hidden competitor data.
- Needs validation before live integration: **SMS** manifest interface, source rights, authorized per-platform APIs, cost budget, image model per group, per-category QA tolerances and owner of learning approvals.
- Versioned design must accommodate a future 7th Campaign group and video pipeline without breaking 6-group core or old manifests.

**Implementation milestones, quality checks, dependencies, security and rollback:** see [Product Creative Implementation Plan](./PRODUCT_CREATIVE_IMPLEMENTATION_PLAN.md).
