---
project: CAM-TYPOGRAPHY-ENGINE-V2
state_schema_version: 2
updated_at: 2026-10-29
status: G3_QUEUE_ADAPTER_API_PILOT_RUNNER_MERGED_PILOT_ACCEPTANCE_PENDING_G0_1_BLOCKED
active_gate: G3
next_action: G0_1_BACKEND_ROLLBACK_CAPACITY_THEN_G3_8_REFERENCE_PILOT_QA
canonical_runbook: docs/operations/CAM_TYPOGRAPHY_ENGINE_V2_RUNBOOK.md
canonical_state: docs/operations/CAM_TYPOGRAPHY_ENGINE_V2_STATE.md
canonical_ui_blueprint: docs/operations/CAM_TYPOGRAPHY_ENGINE_V2_UI_BLUEPRINT.md
canonical_ui_prototype: docs/operations/prototypes/CAM_TYPOGRAPHY_ENGINE_V2_UI_PREVIEW.html
ui_design_status: G2_LIBRARY_IMPLEMENTED_IN_FEATURE_BRANCH_G4_STYLE_LAB_NOT_STARTED
ui_blueprint_version: 1.0.0
ui_acceptance: UX-01..UX-16_PENDING
feature_flag: OFF
scout_flag: OFF
vps_main_commit: 0f9a05df4b8b5f245123a3eb5922a4605cbfd8c2
feature_branch: docs/typography-v2-g3-pilot-qa-20261029
pr: G2_PR_42_MERGED_G3_PR_43_44_45_46_50_51_52_MERGED_PILOT_QA_PR_PENDING
ci: G3_PILOT_PR_52_CI_2474_16_OF_16_JOBS_GREEN_QA_DOC_PENDING
vps_release_commit: null
windows_active_version: VERIFIED_0.1.46
last_verified_at: 2026-10-28
blockers:
  - G0_1_BACKEND_CURRENT_PRESENT_PREVIOUS_SYMLINK_MISSING
  - G0_1_DISK_89_PERCENT_AND_SWAP_2_GIB_OF_2_GIB_REQUIRES_CAPACITY_REVIEW
rollback:
  backend: CURRENT_VERIFIED_PREVIOUS_MISSING
  frontend: HOTFIX_FRONTEND_RELEASE_VERIFIED_TYPOGRAPHY_ROLLBACK_PENDING
  windows: AVAILABLE_0_1_45_KEEP
---

# CAM Typography Engine V2 — Execution State & Checkpoint

> **Cập nhật G3 (29/10/2026):** PR #46 queue, #50 Vision adapter, #51 Analyze API, #52 one-shot pilot launcher đã merge vào `main`, CI #2460/#2470/#2472/#2474 đều 16/16. Backend production chưa nâng migrations 0188/0189, các cờ V2/pilot/Scout đều OFF. G0.1 rollback backend/capacity còn BLOCKED, chưa xử lý tám ảnh có quyền thực tế.

## 1. Next action — nhiệm vụ được chọn sẵn

**G3 — Code queue/adapter/API/CLI pilot đã merge, chưa có AI call thực hay pilot nghiệm thu. Ưu tiên G0.1: chuẩn bị backend previous/rollback thực sự và capacity, kiểm tra ảnh pilot có quyền sử dụng; sau đó vận hành 8 lượt có budget/QA. Không deploy production, không bật cờ khi rollback chưa VERIFIED.** Không deploy production/migration/worker cho đến khi các gates liên quan VERIFIED.

Công việc mở đầu:
1. Đọc RUNBOOK + STATE + UI_BLUEPRINT + HTML prototype. Nếu chưa có trong `main`, đưa **cả bốn artifact** vào `docs/operations/` (prototype nằm trong `docs/operations/prototypes/`) qua clean worktree + documentation-only PR + CI, không chạm production.
2. Xác minh `origin/main`, git/worktree và release current/previous trên VPS; **checkout root đã từng dirty, không reset/clean**.
3. Xác minh máy tool `DESKTOP-3FR0LF0`, `D:\Desify_Agentic\scan_pinterest`, Scout Manager active release/version, health/pairing mà **không lấy hoặc in token**.
4. Xác minh Stage 1 exact-six validator, reference upload, Stage 2 handoff contract và nguồn/tính sẵn sàng của các reference được chọn để pilot.
5. Cập nhật bảng evidence; nếu gate G0 đủ điều kiện, tạo worktree sạch từ `origin/main` rồi bắt đầu G1 theo thứ tự.

## 2. Trạng thái các gate

| Gate | Tên | Trạng thái | Evidence cần bổ sung |
|---|---|---|---|
| G0 | Baseline & safety preflight VPS/Windows | BLOCKED (rollback) | main 0f9a05df; backend current present nhưng previous symlink chưa có; disk 89%, swap 2/2 GiB |
| G1 | Data contract/schema/API nền tảng | MERGED, ACTIVATION OFF | PR #40 + PR #41 merged; PR #41 17/17 CI green; no production migration or API activation |
| G2 | Web Typography Ideas Library & Stage 1 subnav | MERGED, authenticated pilot visual QA pending | conditional subnav preserving V1, masonry, batch upload with bounded concurrency, filters, metadata/rights drawer; local UI/API tests; responsive browser QA still pending |
| G3 | Visual Analyzer & Style DNA | Code merged (queue/adapter/API/pilot CLI); REAL PILOT PENDING, flags OFF | eight licensed samples, review, latency/cost/ROI/override |
| G4 | Reference matcher & Style Lab Dynamic Composer | PENDING | 3 keyword transfers, SVG preview, UX-05..UX-07 |
| G5 | Exact-text/diversity/originality/embroidery QA | PENDING | QA tests, partial outputs contract |
| G6 | Stage 1 concept-short-v2 integration | PENDING | Skill tests, job/Stage 2 regression |
| G7 | Windows Typography Scout extension | PENDING | manager build, lease/offline/Chrome QA, UX-11..UX-12 |
| G8 | Load/security/Browser QA & observability | PENDING | CI, PostgreSQL, metrics, screenshots UX-01..UX-16 |
| G9 | Pilot A/B và đánh giá nghiệm thu | PENDING | 8 references/20 keywords, cost, QA |
| G10 | Merge main, release, canary, production | PENDING | main SHA, health, rollback evidence |
| G11 | Handover & vận hành | PENDING | documentation, SOP, alerts, accepted |

## 3. Quyết định đã được chốt — KHÔNG hỏi lại

- `concept-short-v2`, giữ nguyên `concept-short-v1`.
- **UI design lock V1**: Stage 1 subnav `Keyword Jobs` / `Typography Ideas` / `Style Lab` (Style Lab rendered as explicitly unavailable until G4); Windows mock là màn hình preview riêng, không phải CAM Web tab. Desktop Style Lab 3 vùng và responsive theo UI_BLUEPRINT §§2–9. Không hỏi lại thiết kế mặc định khi user gõ “tiếp tục”.
- Balanced, tham khảo **Layout + Font + Overlap**, không lấy toàn bộ hình mũ/lighting/vành mũ vào Stage 1.
- Exact keyword lock, không tự thêm text hoặc copy 1:1 thiết kế gốc.
- Cho user upload ảnh vào CAM, Scout Windows mở rộng nguồn reference.
- VPS làm data/model/queue/composer/generation; Windows RRUGC Scout Manager làm discovery/sync.
- Không tạo ứng dụng Windows thứ hai; không chuyển dữ liệu sang ổ C.
- Production chỉ từ `main`, đầy đủ CI/QA và rollback, không bypass hard-block.
- Không tự rerun job Stage 1 đã hoàn thành hoặc thay đổi Stage 2 và các Scout đang hoạt động.
- Auto Scout OFF mặc định cho đến khi source rights, Windows QA và pilot đều đạt.

## 4. Điểm cần kiểm chứng đầu tiên

- **VPS source:** `/srv/creative-asset-manager-source` (đã quan sát), production links `/opt/creative-asset-manager/current` và `/opt/creative-asset-manager/previous` (cần xác nhận trạng thái mới).
- **Stage 1:** `apps/api/app/modules/realistic_review_ugc/keyword_images.py`, `stage1_typography_renderer.py` (đã quan sát code có exact-six contract).
- **Reference upload:** `reference_library.py`, `router.py`, `model.py`, `repository.py` (đã quan sát API `/reference-assets/uploads`, 20 MB validation).
- **Windows:** `DESKTOP-3FR0LF0`, `D:\Desify_Agentic\scan_pinterest` (đã kiểm tra kết nối); active-version marker từng cho `0.1.46`, cần check lại.
- **Dirty checkout:** checkout VPS gốc từng có nhiều thay đổi/untracked. Không xóa/ghi đè. Dùng clean worktree.
- **User example references:** ảnh đã chia sẻ trong hội thoại, chưa được xác nhận là đã import vào CAM với metadata/rights. Pilot import chỉ sau G1/G2 và xác nhận nguồn.
- **UI reference:** `CAM_TYPOGRAPHY_ENGINE_V2_UI_BLUEPRINT.md` định nghĩa màn hình/CTA/responsive/states/acceptance. HTML demo đi kèm chỉ chứa type specimens tạo giả, không có ảnh private hay API call. UI chưa được code triển khai vào production.

## 5. Progress log — append sau mỗi lượt

### 2026-10-28 — Kế hoạch được chấp thuận, tạo tài liệu

- **Hoàn tất:** đặc tả, runbook triển khai VPS/Windows và state/checkpoint v1.
- **Chưa hoàn tất:** code feature, database migration, web UI, analyzer/composer, Windows extension, QA, CI, production activation.
- **Trạng thái:** `APPROVED_SPEC_NOT_IMPLEMENTED`.
- **Bước kế được ghi ở thời điểm tạo hai tài liệu ban đầu:** `G0.0` (đồng bộ tài liệu); sau khi cập nhật UI, phải đồng bộ **cả bốn artifact**, như mục Next action hiện hành ở trên.
- **Bằng chứng:** tài liệu `CAM_TYPOGRAPHY_ENGINE_V2_RUNBOOK.md` và tệp này.

### 2026-10-28 — UI/UX design handoff được bổ sung (chưa code)

- **Hoàn tất trong tài liệu:** `CAM_TYPOGRAPHY_ENGINE_V2_UI_BLUEPRINT.md` V1 có routes/subtabs, wireframes, controls, tokens, state matrix, responsive breakpoints, Windows card, API interaction map, checklist UX-01..UX-16.
- **Prototype:** `CAM_TYPOGRAPHY_ENGINE_V2_UI_PREVIEW.html` (offline-only, demo data, không API). Tham chiếu mock 4 chế độ: Keyword Jobs, Typography Ideas, Style Lab, Windows Tool preview; Windows tool là app riêng, không phải một subtab CAM.
- **Kiểm tra prototype tại thời điểm tạo:** headless Chromium render desktop 1440×900, tablet 768×1024 và mobile 390×844, 3 chế độ Web + Windows tool mock, không phát hiện JS error hoặc body horizontal overflow; screenshots mock được đóng gói. Đây là **prototype QA**, không phải kiểm thử feature CAM thật và không làm thay đổi gate UX-01..UX-16 đang PENDING.
- **Chưa thực hiện:** không có thay đổi code production, không bật Auto Scout, không chạy migrations; bản thiết kế chưa được commit `main`.
- **Trạng thái:** `APPROVED_SPEC_NOT_IMPLEMENTED`; `ui_design_status=UI_TARGET_DEFINED_NOT_IMPLEMENTED`. G0.0 cập nhật thành persist **4 files**.
- **Bước kế:** G0.0 docs/prototype PR → G0.1 baseline → G1, sau đó xây UI theo chính blueprint này.

---

### 2026-10-28 — G0.0 implementation start / safety cleanup / CI unblocking

- **Request:** Dọn các phần không cần rồi triển khai Typography Engine V2 trên VPS và Windows.
- **VPS:** kiểm kê 50G root, ban đầu ~87% dùng / 6.4G free; dọn hai Git worktree sạch, commit đã nằm trong main (`.cam-browser-qa-approved-20261028`, `.cam-slot1-v4-release-20261028`), sau đó ~86% dùng / 6.7G free. Không xóa source dirty, job data, storage, previous releases hay repo branches.
- **Windows:** RRUGC Scout Manager active `0.1.46` (process PID 11648 verified); giữ rollback `0.1.45`, xóa 15 file release 0.1.40–0.1.44 (setup/portable/SHA), release folder từ ~134MB xuống ~45MB. Không sửa profile, paired token, log hoặc uncommitted code.
- **Baseline production read-only:** `creative-asset-manager-api.service` + nginx active, `/ready` báo PostgreSQL/Elasticsearch available; `/version` backend phục vụ `d4573f7ee825c659ad44401fc9c60fec297aaefd`. Kiểm tra symlink `current/previous` bị CodeLocal workspace hard-block; **không bypass**, bằng chứng rollback cần tiếp tục ở G0.1.
- **Documentation source:** worktree sạch `.cam-typography-engine-v2-20261028` tạo từ `origin/main`, commit `2def456ed88ba0e828f22aab9ad48aa774ad433e`; PR #38 đang chờ CI xanh. Đã copy RUNBOOK, STATE, UI Blueprint, prototype HTML, reproducible Playwright QA và 5 screenshot baselines. **5/5** prototype views browser QA đạt, không có JS error/horizontal overflow; không phải production QA.
- **CI blocker found:** PR #38 docs-only bị 5 test frontend cũ lỗi từ main (không có stock catalog fixture, selector UI lỗi thời, số palette dự kiến lệch). Đã sửa riêng 3 file test không thay đổi sản phẩm trong PR #39, local targeted Vitest **17/17** pass, typecheck pass, **15/15 GitHub CI jobs green**, PR #39 merged main commit `4bd2ee7be7739bb8b11334d9ff3b0bcd90e90fdf`. Full local suite bị CodeLocal cancel trước báo cáo cuối; GitHub CI là bằng chứng toàn diện.
- **Đã hợp nhất `origin/main` sau PR #39 vào nhánh docs PR #38**, chưa merge PR #38 tại thời điểm ghi. Feature code/migration/Windows extension chưa triển khai, Auto Scout OFF.
- **Gate:** G0.0 IN_PROGRESS. Chỉ đạt khi PR #38 CI xanh và merge vào main, sau đó G0.1 hoàn tất baseline; chưa cho phép bỏ qua các gate để lên production.
- **Next action:** push docs branch cập nhật, chờ CI PR #38 full green → merge main → kiểm tra source & rollback G0.1 → triển khai G1 schema/API trên clean worktree.

---

### 2026-10-28 19:xx +07 — G0.0 merged, G0.1 baseline and G1.1 isolated foundation

- **G0.0 VERIFIED:** PR #38 merged `main` as `f32b7084d316a73c7c437c222b89f2afe16abcdc` after **17/17 GitHub checks successful** (including frontend/API/migrations/Windows/Browser QA/security). Runbook/STATE/UI Blueprint/prototype and five screenshot baselines are in main.
- **G0.1 baseline:** production public `/version` = backend commit `d4573f7ee825c659ad44401fc9c60fec297aaefd`, frontend `build-info.json` = `08c228e3da6c846d54c700909b462144d162fe24`. API and nginx `active`, `/ready` returns PostgreSQL + Elasticsearch available. Root disk **87%** used (~6.2 GiB free), RAM available ~3.6 GiB, swap **1.9/2 GiB** used, load average 2.94/3.31/3.39 at audit. Do not launch new production workers under these conditions.
- **Rollback blocker:** CodeLocal rejects explicit `/opt/creative-asset-manager/current` and `/var/www/creative-asset-manager/current` paths as outside authorized workspace. No access bypass attempted; actual current/previous release symlinks and storage rollback remain unverified. Windows active Scout Manager 0.1.46, rollback file 0.1.45 present; no token access.
- **G1.1 development only:** clean isolated worktree `.cam-typography-v2-foundation-20261028` from current `origin/main`. Added additive `0188_typography_v2_foundation` (reference + versioned Style DNA, tenant-scoped FK, unique tenant+hash); `TypographyReferenceRepository` with private/workspace visibility and dedup across UGC; RBAC keys `typography_reference.read/upload/manage`, `typography_generate.run`, `typography_scout.manage` (not automatically assigned Viewer/Operator).
- **Local verification:** G1.1 repository, migration, previous authorization tests **24/24 passed** together; migration upgrade, guarded rollback with live data, empty-table rollback verified. Migration test runs Alembic in a subprocess to avoid polluting Base.metadata of other tests. PostgreSQL 12.22/16.4 CI, production release preflight and G1.2 API tests still pending.
- **Production impact:** none; no migrated production schema, no exposed endpoint, no changed Stage 1 v1, no Scout changes or extra workers.
- **Next:** complete G1.1 PR CI; implement G1.2 batch upload/list/detail/editorial API behind OFF feature flag, tenant/auth negative tests, then G2 frontend. Retain G0.1 hard-block; do not activate production until gates pass.

---

### 2026-10-28 — G1.1 merged, G1.2 pilot API in isolated branch

- **G1.1 merged:** PR #40, `b5b74814635b70997791c7847c3cdc4bf9c9115f`, **17/17 GitHub checks green** including PostgreSQL 12/16, API, frontend, Windows, Browser QA and security. No production deploy.
- **G1.2 implementation:** `feat/typography-v2-reference-api-20261028` from merged main. Added `TYPOGRAPHY_V2_ENABLED` default false and an empty `TYPOGRAPHY_V2_CANARY_TENANT_IDS`. New tenant-scoped API: batch reference uploads (12 PNG/JPEG/WEBP, 20MB max each), list/search/pagination/detail/image/DNA, patch scope/rights/privacy/tags/preference, archive; audit writes without private payloads. Component roles layout/font_mood/overlap and balanced influence preserved.
- **Security:** Deny non-canary tenants, including platform admins; new permissions separate from RRUGC role permissions. Private references cannot be viewed by other users without manage; old RRUGC list/detail/image/upload and candidate promotion cannot return typography-only storage rows. Upload duplicates never auto-upgrade rights or copy design content. Scope campaign/keyword IDs are tenant-checked.
- **Local evidence:** 8/8 new API tests passed; 4/4 selected existing RRUGC reference regression tests passed. No worker/imagegen/scout/API route activated in production.
- **Production unchanged, G0.1 BLOCKED:** release rollback symlinks outside CodeLocal authorized workspace; root disk/swap capacity review remains required. Do not deploy or enable feature until verified.
- **Next action:** CI/merge G1.2 after all mandatory jobs green → G2 UI implementation from blueprint + UX-01..UX-16, while resolving G0.1 access/capacity separately.
- **API contract:** `docs/operations/CAM_TYPOGRAPHY_ENGINE_V2_API_CONTRACT.md`.

---

### 2026-10-28 — G1.2 merged, G2 Ideas Library frontend in isolated branch

- **G1.2 MERGED:** PR #41, `b8e558cd48c80dd6a50fc8b986eb40a6c8ae71b2`, **17/17 GitHub checks green** (16 workflow jobs plus security). No production deploy or API flag activation.
- **G2 implementation underway:** clean feature worktree `.cam-typography-v2-ideas-ui-20261028`, branch `feat/typography-v2-ideas-ui-20261028`, from merged `main`. Added conditional Stage 1 subnav, with existing Keyword Jobs V1 table and search/sort/page kept mounted; tab switch is explicit and query-param backed. Library is gated by tenant auth/rights/feature flag (existing API GET must succeed); disabled backend falls back to original V1 with no new controls.
- **Typography Ideas:** image grid (preserve original aspect ratio), server-provided counters, pagination, keyword/tag/source search, style-component/source/scope/status/sort/my-uploads filters; upload modal batch up to 20 files, PNG/JPEG/WEBP <=20MB each, three concurrent multipart requests with two images each; per-file created/duplicate/error, isolated retry, privacy and source-rights selections. Reference drawer displays the actual image, metadata/rights, Style DNA list or empty state, editable tags/scope/priority/style components, rights-gated approve, audited archive confirmation. Viewer controls are read-only.
- **API enhancements for G2:** filter by style role/source/scope/my uploads, sort by priority/age, tenant/private-filtered summary stats and capability flags. All remain behind G1.2 default-OFF tenant canary.
- **Verification to date:** dedicated G2 UI 6/6 + Stage 1 conditional navigation 2/2 + existing Stage 1 15/15; backend typography API 9/9 + foundation 6/6. Full frontend suite, CI and actual authenticated browser responsive QA still pending.
- **Deliberate exclusions:** no fake Style Lab/Generate action; G3 DNA analyzer, G4 Dynamic Composer, G5/G6 generation & QA and G7 Windows Scout are not part of G2. No source images, tokens or tenant secrets committed.
- **Production:** unchanged. G0.1 rollback symlink inspection and capacity gate still BLOCKED. Explicitly do not deploy G2 code, migrate schema, or turn on flags until rollouts are authorized and safe.

---

### Mẫu log lượt triển khai tiếp theo

```md
### YYYY-MM-DD HH:mm TZ — Gate Gx.y
- Request: người dùng gõ “tiếp tục”.
- Đã làm: ...
- Chứng cứ: commit ..., PR ..., CI ..., tests ...
- VPS: ... / Windows: ...
- Changed files: ...
- Production changed? No/Yes (SHA/version...)
- Gate status: PENDING / IN_PROGRESS / BLOCKED / VERIFIED
- Blocker & risk: ...
- Rollback readiness: ...
- Next action: Gx.z — ...
```

### 2026-10-29 — G2 merged, G3 schema contract started

- **G2 merged:** PR #42 on `main` commit `8275286cecbf330c716f99fff5f1e921ebc945de`; CI #2451 all 16 jobs successful (frontend, PostgreSQL 12/16, browser matrix, worker, Windows package). Authenticated Typography Ideas UI visual acceptance with 8 rights-verified pilot images remains separate/pending; do not call G8 or G9 complete.
- **G3 development:** branch `feat/typography-v2-g3-dna-contract-20261029` created from merged `origin/main`. Add strict `typography-dna/v1` parser/validation contract + negative tests for source hash mismatch, malformed bounding box, untrusted graph relations, over-occlusion, NaN/invalid confidence. Contract has no new DB migration, worker, provider calls, or public routes.
- **Production:** unchanged; `TYPOGRAPHY_V2_ENABLED=false`, Auto Scout OFF. G0.1 symlink rollback access still blocked; disk at ~88% on VPS, no new production worker.
- **Next:** confirm G3 contract CI, implement source-hash/version cache, bounded analyzer queue and failure/retry budgets; collect 8 anonymized rights-verified DNA samples and authenticated desktop/tablet/mobile UI screenshots. Keep G0.1 hard gate.

### 2026-10-29 — G3 durable queue storage and lease safety (feature branch)

- **PR #45 merged:** GitHub CI #2457 16/16 jobs green, main commit `c3f8fe45ca8114737f821bc2c47d63006e2c7e1c`. Pure dispatch policy is verified in CI; it did not schedule any job.
- **Current branch:** `feat/typography-v2-g3-durable-queue-20261029` from fresh `origin/main`. Implement additive Alembic `0189`, tenant-linked unique job identity (`reference_id + image_hash + analyzer_version`), bounded due-time claim, PostgreSQL tenant advisory capacity lock + `SKIP LOCKED`, lease fencing, heartbeat, bounded timeout recovery, retry budget and atomic Style DNA persistence.
- **Fail-closed invariants:** no enqueue/claim/heartbeat/complete without explicit feature+tenant-canary allow; verified rights checked at enqueue, claim, renewal and completion; stale token cannot save results; invalid/untrusted analysis goes to human review. No image bytes or raw model responses in job rows.
- **Test evidence:** Python syntax and migration test additions prepared; full SQLite/PostgreSQL CI to verify after PR creation. This is storage/lease infrastructure, **not a live worker**, AI analyzer, background scheduler, active queue, public API or completed G3 gate.
- **Production:** unchanged; feature OFF, auto Scout OFF, no migration deployed. G0.1 rollback symlink access/capacity and authenticated G2 UI pilot screenshot QA remain blocked/pending.
- **Next:** CI for durable queue, connect guarded worker/provider adapter and budgeted retry with 8 verified reference samples, do actual authenticated desktop/tablet/mobile screenshot QA, then G4.

### 2026-10-29 — PR #46 merged, G3 visual analyzer adapter under development

- **main:** `899154a9fb371940f10cbbc05d24f61209a90a8e`, PR #46 merged after CI #2460 with 16/16 jobs green; additive queue migration `0189` remains **not applied to production**.
- **hotfix baseline:** production frontend already published from `eb4f966a` (Stage 0/1 24h date display); no changes to its API, workers or existing Stage 1 design jobs for this G3 step.
- **feature branch:** `feat/typography-v2-g3-analyzer-adapter-20261029` clean from latest `origin/main`, implementing optional one-shot `TypographyVisualAnalysisRunner` and synthetic-only tests; runbook supplement at `docs/operations/CAM_TYPOGRAPHY_ENGINE_V2_G3_ANALYZER.md`.
- **invariants:** feature OFF until explicit tenant canary; only verified source rights; Managed Storage private read with SHA256 verification; one Vision AI schema call per attempt, bounded resize/read/timeout; no provider raw response/lettering persisted; lease-fenced commit. No live provider call or worker registration in this branch.
- **blocking gates:** no eight real licensed samples/QA yet, production backend `previous` symlink missing, VPS root approximately 89% occupied; no production migration or rollout authorized.
- **next:** CI and review adapter; then explicit permissioned analyze API, optional pilot worker and sanitized 8-image DNA QA with documented cost/latency. G4 not started.

### 2026-10-29 — PR #50 merged, explicit G3 Analyze API under development

- **main:** `d2b94df0cc1ac7dee415aad03b265d259b2c0c05`; G3 one-shot private-image Vision adapter PR #50 merged after CI #2470 all 16 jobs green. The adapter remains unregistered; **zero real provider calls** were made.
- **active branch:** `feat/typography-v2-g3-analysis-api-20261029` from fresh `main`, adding `POST /references/{id}/analyze` (manage-only, idempotent, server-owned analyzer version, audit only for created job) and `GET /references/{id}/analysis` (read-only, tenant/private-scope safe, no lease token/storage/provider secrets). Both are gated by the existing feature flag and tenant allowlist. No worker starts after enqueue.
- **test evidence:** new API tests for rights unknown, canary OFF, RBAC, duplicate enqueue, private/cross-tenant access, cached DNA and sanitized job response; backend CI required.
- **safety:** no production API/migration/frontend change; existing Stage 1/Stage 2 job pipeline untouched. G0.1 blocked: backend `previous` missing, disk 89%, swap 2/2 GiB. Do not activate V2, run worker or stage real user images.
- **next:** merge API only after full CI; then build optional bounded pilot runner wiring using existing provider pool and run 8 licensed reference QA/ROI review when G0.1 is cleared.

### 2026-10-29 — G3 explicit Analyze API merged, one-shot pilot CLI in progress

- **main:** `647c02069b3a54a7d40fc97d0c0db69633b9799b` after PR #51 CI #2472 full 16/16 PASS. API Analyze enqueues jobs explicitly but **does not invoke any AI**; current backend production remains at its old release/migrations.
- **development-only pilot CLI:** branch `feat/typography-v2-g3-pilot-runner-20261029` adds a single-invocation `typography_pilot_once` launcher. `--tenant-id` alone is dry run, without provider/storage connection. `--execute` needs `TYPOGRAPHY_V2_ENABLED`, exact tenant allowlist, `TYPOGRAPHY_V2_PILOT_WORKER_ENABLED` (default false) and **independent** `TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED` (default false). The executable launcher creates a provider only after all gates are satisfied and claims at most one job, without scheduling or a daemon.
- **test plan:** fake-provider/storage tests for no side effects in dry-run, feature OFF, tenant mismatch, rollback flag OFF, one tick at concurrency 1 and provider lifecycle; CI required before any merge. No real image/paid model request.
- **hard-block remains:** backend `previous` release missing, 89% disk, swap 2/2 GiB; not safe to apply migrations `0188/0189` or enable any worker. Need authenticated G2 screenshots, eight rights-verified pilot reference images and sanitized DNA/latency/cost before G3 acceptance.
- **next:** pilot CLI CI/merge; safety/rollback capacity remediation; rights-verified sample QA and G3 manual override + perceptual dedup, then G4.

### 2026-10-29 — G3 code path merged, pilot evidence and release safety still open

- **main:** PR #52 one-shot pilot launcher merged as `0f9a05df4b8b5f245123a3eb5922a4605cbfd8c2` following CI #2474 16/16 PASS; PR #51 explicit Analyze API CI #2472 also 16/16 PASS. This is **code readiness**, not G3 field acceptance.
- **G3 pilot safety:** `TYPOGRAPHY_V2_ENABLED=false`, empty tenant allowlist, `TYPOGRAPHY_V2_PILOT_WORKER_ENABLED=false` and `TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED=false` by default. Pilot CLI without `--execute` is dry run and no automation/daemon has been registered.
- **current branch:** `docs/typography-v2-g3-pilot-qa-20261029`; preparing G3 QA rubric with eight pseudonymous slots and required rights, image hash, typography-only region, schema, originality, human review, latency/token/cost evidence. No images/tokens/tenant IDs are committed.
- **hard blocker:** backend production has only `current` release; no verified `previous` rollback, 89% disk, swap fully allocated. Do not run migration `0188/0189` or enable V2, worker or Scout until these are remediated and signed off.
- **G3 exit remaining:** eight rights-verified reference analyses (plus failures/reviews), perceptual near-duplicate checks, manual override workflow, ROI crop comparison and UX pilot screenshot QA. After real evidence, advance G4 Style Lab with exact keyword transfer.

## 6. Quy tắc bảo toàn trạng thái

1. Không ghi `VERIFIED` nếu test hoặc CI chưa đạt, dù code đã commit. **HTML prototype/screenshot không phải bằng chứng feature được triển khai**.
2. Mỗi lệnh `tiếp tục` đọc bản STATE mới nhất, RUNBOOK và UI_BLUEPRINT, rồi kiểm tra bằng chứng từ tool hiện tại. Không thực thi âm thầm sau khi kết thúc lượt.
3. Khi merge PR, cập nhật `main SHA` và CI evidence; khi deploy phải kiểm tra **public version** + backend/frontend + Windows tool riêng biệt.
4. Nếu hard-block, giữ trạng thái `BLOCKED`; không bypass, không xóa hoặc reset dữ liệu để tiến tiếp.
5. Sau mỗi lượt nên commit cập nhật state trong docs theo nhánh phù hợp/PR; nếu không thể cập nhật repo vì tool/quyền, ghi rõ `checkpoint_not_persisted` và cung cấp bản `.md` đã cập nhật.
