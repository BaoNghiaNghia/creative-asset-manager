---
project: CAM-TYPOGRAPHY-ENGINE-V2
state_schema_version: 2
updated_at: 2026-10-29
status: G4_PREVIEW_MVP_MERGED_G0_1_FULL_RESTORE_BLOCKED
active_gate: G0.1
next_action: PARALLEL_G5_G6_OFFLINE_QA_WITH_G0_1_OFF_HOST_BACKUP_RESTORE_BEFORE_PRODUCTION_ACTIVATION
canonical_runbook: docs/operations/CAM_TYPOGRAPHY_ENGINE_V2_RUNBOOK.md
canonical_state: docs/operations/CAM_TYPOGRAPHY_ENGINE_V2_STATE.md
canonical_ui_blueprint: docs/operations/CAM_TYPOGRAPHY_ENGINE_V2_UI_BLUEPRINT.md
canonical_ui_prototype: docs/operations/prototypes/CAM_TYPOGRAPHY_ENGINE_V2_UI_PREVIEW.html
ui_design_status: G2_LIBRARY_MERGED_G4_STYLE_LAB_PREVIEW_MVP_MERGED_AUTHENTICATED_QA_PENDING
ui_blueprint_version: 1.0.0
ui_acceptance: UX-01..UX-16_PENDING
feature_flag: OFF
scout_flag: OFF
vps_main_commit: 8a8ad2c124f6cdd625efbcac480ca7592f3d5591
feature_branch: main
pr: G2_PR_42_G3_PR_43_44_45_46_50_51_52_53_56_57_G0_PR_54_55_60_61_62_G4_PR_63_MERGED
ci: G4_PR_63_17_OF_17_CI_PASS_G4_LOCAL_PYTEST_54_PASS
vps_release_commit: null
frontend_release_commit: 2e528643497c159251ac77f3d87ffb941a6e8987
windows_active_version: VERIFIED_0.1.46
last_verified_at: 2026-10-29
blockers:
  - G0_1_BACKUP_LAST_RUN_FAILED_CAM_APP_LACKS_SELECT_ON_TWO_INCIDENT_TABLES
  - G0_1_BACKUP_SERVICE_ON_OLD_BACKEND_AND_NEW_READONLY_ROLE_NOT_USED_BY_SCHEDULER_YET
  - G0_1_FULL_DATA_RESTORE_AND_0188_0189_COMPAT_NOT_VERIFIED
  - G0_1_DISK_89_PERCENT_SWAP_NEAR_FULL_NEEDS_CAPACITY_PLAN
  - G2_AUTHENTICATED_BROWSER_QA_STOPPED_DUE_VPS_RESOURCE_PRESSURE
rollback:
  backend: CURRENT_D4573F7E_PREVIOUS_INDEPENDENT_D4573F7E_SNAPSHOT_RUNTIME_CHECKED_DB_RESTORE_UNVERIFIED
  frontend: MAIN_2E5286434_DEPLOYED_PREVIOUS_RELEASE_PRESERVED
  windows: AVAILABLE_0_1_45_KEEP
---

# CAM Typography Engine V2 — Execution State & Checkpoint

> **Checkpoint 29/10/2026:** PR #63 **G4 Style Lab preview MVP** đã merge vào `main` (`8a8ad2c12`, 17/17 CI PASS), nhưng chưa phát hành backend/frontend production; V2 vẫn OFF. PR #61/#62 đã hoàn thành tài khoản backup riêng `cam_backup`, quyền SELECT 197/197 bảng + 1/1 sequence và không quyền ghi. **Backup dữ liệu ~4 GB + restore off-host còn thiếu**; runtime backend `d4573f7e`, DB revision `0187`, frontend production `2e5286434`. `0188/0189` chưa chạy; Scout/pilot OFF, disk ~89%, swap gần đầy. Tiếp tục G5/G6 offline đồng thời gỡ G0.1.

## 1. Next action — nhiệm vụ được chọn sẵn

**G0.1 — Role backup chỉ đọc và credential riêng đã được cấp, xác minh xong. Không tạo lại role hoặc rotate secret.** Việc tiếp theo là chụp bản backup production đầy đủ với identity mới, xác minh bản sao remote và restore toàn bộ off-host, đo dung lượng/rollback và thử tương thích migration `0188/0189`; chỉ sau đó mới xem xét deploy backend. Có thể tiếp tục G2 responsive QA/G3 tám ảnh reference có quyền khi đủ tài nguyên, nhưng chưa kích hoạt V2/pilot/Scout. Không chạy full dump trên VPS đang swap full hoặc dùng backend cũ của timer.

Công việc mở đầu:
1. Đọc RUNBOOK + STATE + UI_BLUEPRINT + HTML prototype. Nếu chưa có trong `main`, đưa **cả bốn artifact** vào `docs/operations/` (prototype nằm trong `docs/operations/prototypes/`) qua clean worktree + documentation-only PR + CI, không chạm production.
2. Xác minh `origin/main`, git/worktree và release current/previous trên VPS; **checkout root đã từng dirty, không reset/clean**.
3. Xác minh máy tool `DESKTOP-3FR0LF0`, `D:\Desify_Agentic\scan_pinterest`, Scout Manager active release/version, health/pairing mà **không lấy hoặc in token**.
4. Xác minh Stage 1 exact-six validator, reference upload, Stage 2 handoff contract và nguồn/tính sẵn sàng của các reference được chọn để pilot.
5. Cập nhật bảng evidence; nếu gate G0 đủ điều kiện, tạo worktree sạch từ `origin/main` rồi bắt đầu G1 theo thứ tự.

## 2. Trạng thái các gate

| Gate | Tên | Trạng thái | Evidence cần bổ sung |
|---|---|---|---|
| G0 | Baseline & safety preflight VPS/Windows | PARTIAL, full restore/capacity BLOCKED | main 491e037f9; backup role credential+SQL ACL verified 197/197 tables, 1/1 sequence, no write; backend d4573f7e; DB 0187; last scheduled backup failed; remote full restore pending; disk 89%, swap 2/2 GiB |
| G1 | Data contract/schema/API nền tảng | MERGED, ACTIVATION OFF | PR #40 + PR #41 merged; PR #41 17/17 CI green; no production migration or API activation |
| G2 | Web Typography Ideas Library & Stage 1 subnav | MERGED, authenticated pilot visual QA pending | conditional subnav preserving V1, masonry, batch upload with bounded concurrency, filters, metadata/rights drawer; local UI/API tests; responsive browser QA still pending |
| G3 | Visual Analyzer & Style DNA | Human Review + ROI near-duplicate advisory PR #57 merged; real pilot OFF | 8 licensed references, human region/dedup review, provider cost/latency and authenticated QA; no production migration |
| G4 | Reference matcher & Style Lab Dynamic Composer | MVP PREVIEW MERGED, FINAL QA PENDING | PR #63 17/17 CI pass; guarded 3 SVG previews and Stage1 Apply style available in source; matcher scoring, UX-05..07 and production enablement remain |
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

### 2026-10-29 — G0.1 rollback retention repaired, independent snapshot seeded (not full gate)

- **Source:** `main=c0ab6d8c8e123edd46607d13ae143ec91b0d822c`, PR #53 QA guide merged with CI #2476 (16/16); PR #54 backend retention fix merged with CI #2478 (16/16). Source deploy script now defaults/minimum **2** releases, refuses one-release mode and cannot delete the previous symlink after a successful deploy. The running backend still uses the older release; this new script policy has **not** been exercised by a production backend deploy.
- **Production backend `current`:** `/opt/creative-asset-manager/releases/d4573f7ee825c659ad44401fc9c60fec297aaefd`, validated release marker, API venv imports and Alembic code head `0187_navbar_member_permissions`. This **does not establish the production DB revision**, which remains to be checked.
- **Rollback seed `previous`:** independent copy at `/opt/creative-asset-manager/releases/d4573f7ee825c659ad44401fc9c60fec297aaefd-g0rollback-20261029`, `previous` symlink created atomically under backend deployment lock; SHA marker, ready marker, critical requirements, Python runtime and single Alembic head validated. Release marker has a **different inode** from current. It is a physical snapshot of the same pre-upgrade commit; the future deploy script will replace `previous` with the then-old current release.
- **Production stability after seed:** current symlink unchanged, API/image/video services active; `/live` and `/ready` returned HTTP 200 with the production Host header. No database migration, service restart, provider call, Windows Scout change or pilot execution. Config check found no override for `CAM_BACKEND_RELEASE_KEEP`, V2 feature flag not true.
- **Capacity:** VPS disk originally 89%; removed three clean worktrees whose commits were already merged (Typography foundation/API and Stage 1 date hotfix), dropping to 88%; the ~725 MiB independent safety snapshot raised use to **90% (about 5.0 GiB free)**. Memory ~7.8 GiB total, ~3.1 GiB available; swap **2/2 GiB allocated**. A brief `vmstat` sample showed no active swap-in/out in its later seconds, but several PostgreSQL processes were CPU-intensive. No swapoff, restart, delete of dirty worktrees or database tuning was attempted.
- **Backup/DB gate:** `/opt/creative-asset-manager/backups` contained historical env/config evidence, not a verified current PostgreSQL restore artifact. A read-only production-env/alembic DB query was blocked at the CodeLocal workspace boundary; **no workaround was attempted**, no DB credential read. A verified PostgreSQL backup/restore, actual DB revision, migration `0188/0189` compatibility with the old runtime and a non-disruptive rollback drill remain required.
- **Next safe sequence:** (1) obtain authorized, scoped read-only DB/backup validation and restore to an isolated test DB, (2) confirm 0188/0189 additive backward-compatible rollback and release target, (3) reduce/plan production disk and swap pressure, (4) run G2 authenticated desktop/tablet/mobile reference UI QA, (5) verify rights of exactly eight references and costs, (6) only then authorize the one-shot G3 pilot. **Keep all rollout/pilot/Scout flags OFF until gates pass.**

### 2026-10-29 — Isolated production-schema restore PASS and G3 human review implementation

- **main:** `d53da73bebac1c18dc37163fe0335ccca8c98909`, after PR #55 G0.1 documentation CI #2480 PASS 16/16; working feature branch `feat/typography-v2-g3-human-review-20261029`.
- **Read-only production DB check:** PostgreSQL database `creative_asset_manager`, Alembic revision `0187_navbar_member_permissions`, about 3996 MB, 197 public tables, 3 active and 21 idle connections at observation time. No app credentials were read or printed.
- **G0 isolated restore drill:** Schema-only `pg_dump` (about 451 KB, no user rows) restored to a uniquely named temporary PostgreSQL database on the same cluster in **4 seconds**. It contained **197/197 tables, 944 constraints and 708 indexes**, with a valid seeded revision 0187. Test database occupied about 18 MB and was removed automatically after successful checks. Production database/schema were never altered. **This proves only schema restore, NOT 4 GB data recovery or 0188/0189 migration on a production clone.**
- **Additional rollback QA:** `.venv/bin/uvicorn` and `.venv/bin/alembic` console scripts in current and previous physical snapshot have old absolute shebangs pointing to deleted build staging paths. API's actual systemd ExecStart uses the safe `python -m uvicorn` form and current API/worker health remained PASS; do not assume copied console entrypoints are independently executable. A clean deploy/rollback entrypoint audit remains part of G0 acceptance.
- **G3 human review code:** new pure `typography_dna_review.py` and manage-only `PATCH /references/{id}/dna/{dna_id}/review`, with decisions, fixed reason codes, bounded normalized structural overrides, tenant/hash/version checks, rights gating, atomic audit and immutable AI result preservation. The existing `manual_override_json` column from migration 0188 is used; **no new migration**. Accepted effective DNA invalidates when rights/editorial approval changes. API tests use synthetic references; full CI required before merge.
- **Still BLOCKED:** no verified complete production data backup/restore and 0188/0189 backward-compatibility drill; disk about 90%, swap 2/2 GiB allocated; G3 eight licensed references, human QA, ROI cost and near-duplicate screening outstanding. Production V2/pilot/Scout flags remain OFF; no AI calls, migration, backend deploy or service restart in this work.

### 2026-10-29 — G3 human review merged, typography-only dHash advisory in development

- **main:** `faf6b78f4c5d97a65342dbf70b3dab2c8d728ef8` after PR #56 Human Review CI #2482 full 16/16 PASS. Its bounded, audited manual override/readiness API uses existing 0188 columns and remains feature OFF.
- **feature branch:** `feat/typography-v2-g3-duplicate-screen-20261029` computes `tdh1` 64-bit perceptual hash from the *already prepared and SHA-verified image*, cropping the AI's normalized **typography-only** region. ROI hashing occurs locally only after one validated analyzer call and is stored in existing `rrugc_typography_style_dna.perceptual_hash`, with lease-fenced persistence. No extra provider call, no new migration.
- **read-only similarity endpoint:** `GET /references/{id}/dna/{dna_id}/similar` with tenant/canary/read permission, current hash/version, SQL-enforced private/workspace visibility, and distance 0–8. Returns visible reference IDs and distances only, max 25 results scanning up to 500 candidates; `scan_truncated` acknowledges possible omissions. It is an **advisory for human review**, not an automatic rejection/plagiarism determination.
- **verification:** source/tests Python syntax and diff checks; synthetic tests for ROI vs product background, alpha images, false-positive risk, versioning, malformed inputs, provider one-pass persistence, invalid stored hash and private/cross-tenant API results; CI pending. Current G0.1 remains blocked on full backup/restore, separate clean target for schema upgrade + rollback, disk around 90%, fully allocated 2 GiB swap.
- **next:** CI/merge warning API; perform G2 authenticated responsive QA; secure eight rights-verified images and create sanitized per-image human-review/latency/cost evidence after G0.1 approval. Production V2/pilot/Scout unchanged and OFF.

### 2026-10-29 — Main frontend production release; G0.1 database backup failure isolated

- **Source:** `main` fast-forwarded from `eb4f966a0` to `2e528643497c159251ac77f3d87ffb941a6e8987`. PR #57 ROI near-duplicate advisory is merged. An unrelated untracked `CAM_THREADTRANSFER_V1_IMPLEMENTATION.md` in the main worktree was preserved, not reset/staged.
- **Frontend production DEPLOYED:** official `scripts/deploy-cam-frontend.sh --commit 2e528643497c159251ac77f3d87ffb941a6e8987` exited 0 from a temporary clean checkout of the exact main commit. Dist provenance check verified frontend source unchanged from its committed `build-info.json` ancestor; Nginx syntax/reload and deployment script's public HTTP health/icon/version checks succeeded. Backend, database and workers were not restarted by the frontend deploy.
- **Backend production NOT DEPLOYED:** existing release `d4573f7e` remains active. Production database last independently established at Alembic `0187`. No `0188/0189` migrations, V2 activation, pilot AI calls or Windows Scout changes in this step.
- **Backup blocker (read-only inspection):** scheduled database backup timer active/enabled; latest observed service execution 2026-10-27 22:00 +07 exited 2 with `DatabaseBackupDumpError`. PostgreSQL metadata shows application role `cam_app` lacks `SELECT` on two postgres-owned incident tables, `cam_incident_20261024_job_hold` and `cam_incident_20261024_source_reuse`; the other 195 public regular tables had `SELECT`. This is a **likely cause, not proven stderr-level diagnosis**. The incident tables are protected recovery evidence. No `GRANT`, application role escalation, data changes, credential access or backup retries were performed. Use a separately scoped, audited backup identity and independently verified remote full-data restore before migrations.
- **Capacity/cleanup:** root free space increased approximately 5.1 to 5.5 GiB after removing a clean already-merged Typography worktree, removing 463 rebuildable pip-cache files, and retiring the temporary release checkout. Root remains ~89% full and 2 GiB swap was again nearly full under browser QA. Preserved current/previous backend releases, product outputs, databases and source/worktree changes.
- **UI QA:** standalone read-only production Firefox QA was attempted but cancelled after >3 minutes due CPU/RAM/swap pressure; **NOT PASS**. Six partial screenshots (including a tablet Reviews failure capture) were retained privately at `apps/client/.ui-qa/production-smoke/20261029T063724Z` in the main worktree. No authenticated 1440/768/390 Typography Ideas acceptance was established. Locked QA dependencies surfaced 39 npm audit advisories (32 moderate, 5 high, 2 critical); these require classification and are not automatically patched.
- **Next:** repair the complete PostgreSQL backup path without broadening `cam_app` access; verify a fresh off-host data restore, target schema `0188/0189` compatibility, safe rollback and VPS headroom; run authenticated UI QA on a sufficiently resourced machine; verify rights for eight G3 pilot references. Only then consider backend canary and G4. Frontend release remains the only production change from this turn.

### 2026-10-29 — G0.1 dedicated backup identity code and safety tests

- **Cause localized, no production mutation:** latest scheduled backup failure remained `DatabaseBackupDumpError` (27/10). Read-only PostgreSQL catalog confirms `cam_app` is missing `SELECT` on exactly 2 of 197 public regular tables (`cam_incident_20261024_job_hold`, `cam_incident_20261024_source_reuse`). A separate `cam_backup` login was **not present** at inspection time. No role/privilege/secret was created or changed.
- **Remediation code prepared:** opt-in `DATABASE_BACKUP_DATABASE_URL` for a separate PostgreSQL login, validated same application database/host/port, different role, no URL query override. Only the backup systemd unit loads optional `/etc/creative-asset-manager/database-backup.env` (root-owned 0600), not the shared production environment. Legacy mode stays available but is not an acceptable remediation for the unreadable incident tables. Libpq inherited target/identity overrides are stripped.
- **Local verification:** dedicated backup module + CLI tests and VPS deploy tests **64/64 PASS** using workspace-local Python, `python3 -m py_compile` PASS, `git diff --check` PASS. Systemd unit syntax accepted; analyzer warned about an unrelated existing CodeLocal service setting. No real `pg_dump`, Google Drive write, production credential read or migration occurred.
- **Safety still blocked:** source code/tests do not create a read-only backup role or demonstrate restored full dataset. Operator must provision dedicated non-superuser backup identity with `SELECT` covering both protected incident tables, deliver password exclusively in root-owned backup-only config, then verify full dump/remote checksum/off-host restore and backwards-compatible `0188/0189` migration rehearsal. Root disk ~89%, swap near 2 GiB; no backend deploy, V2/pilot/Scout remain OFF.
- **Next:** reviewed PR and required CI to `main`; only after G0.1 gates pass, deploy backend from `main` and run health/rollback checks. Frontend production remains `2e5286434`.

### 2026-10-29 — PR #60 fully verified and merged into main

- **GitHub:** clean PR #60 merged at `bd27289b579ced7f003292fedfe09366b9c1b490` after **17/17 checks successful**, including GitGuardian Security Checks, API/worker sharded & full, PostgreSQL 12/16, pipeline, frontend, Windows and Browser QA. Prior PR #58 and PR #59 were closed due security warnings about *synthetic static* test passwords; replacement generates credentials dynamically and has GitGuardian SUCCESS.
- **Local VPS source:** canonical `main` worktree fast-forwarded to `bd27289b5`. The unrelated untracked `docs/plans/CAM_THREADTRANSFER_V1_IMPLEMENTATION.md` was preserved. Temporary clean PR #60 and older PR #59 worktrees removed after verification.
- **Production rollout:** code is merged into `main` only. Backend remains at `d4573f7e`; running PostgreSQL remains at known revision `0187`; `0188/0189` have NOT been applied. `TYPOGRAPHY_V2_ENABLED`, pilot/Scout remain OFF. The earlier frontend production release `2e5286434` is unchanged. This is **not** a completed backend release or backup recovery.
- **Remaining hard gates:** production dedicated backup PostgreSQL login and root-only secret **not provisioned/activated**; last observed scheduled dump failed, two incident tables require read coverage by backup-only role; full off-host database restore, data/compatibility/rollback drills and server disk/swap headroom remain unverified. Do not invoke an unsafe full dump or backend migration/deploy. Next step: scoped operator provisioning, read-only privileges audit, checked `pg_dump -Fc` with remote checksum verification, independently restored full dataset outside production, then 0188/0189 compatibility rehearsal.
- **Actual tests:** targeted 64/64 local PASS; GitHub 17/17 PASS. No live backup restoration or paid G3 pilot occurred.

### 2026-10-29 — G0.1 live backup identity provisioned; full restore still blocked

- **Merged source:** PR #61 `ed1e76f8a` and PR #62 `491e037f9`, each **17/17 CI PASS** including GitGuardian. Local focused backup/operator suite **54 PASS**.
- **Production metadata-only DDL (intentional):** an operator-only script from reviewed `main` generated a fresh secret locally and committed a dedicated `cam_backup` role with LOGIN, NOSUPERUSER, NOINHERIT and limited CONNECT, public schema USAGE, table SELECT and sequence SELECT. Default read grants scoped to `cam_app`/postgres-owned future objects. No `INSERT/UPDATE/DELETE/TRUNCATE`, no sequence `USAGE/UPDATE`, no grants to `cam_app`, no data modifications, no exposed credentials.
- **Credential handling:** backup-only root-owned `0600` environment file created. First post-creation audit failed due PostgreSQL plan evaluating a sequence ACL function on a non-sequence `pg_toast` OID, **after** role creation. No second provisioning attempt was made. PR #62 fixed it using MATERIALIZED table/sequence OID sets and added read-only `--verify`.
- **Live verification:** `--verify` on production PASS: role exists, credential file `ROOT_0600`, endpoint DNS valid, dedicated PostgreSQL login successful, 197/197 public tables and 1/1 sequence readable; `TABLES_WITH_WRITE=0`, `SEQUENCES_WITH_WRITE=0`. Two incident recovery tables are included; no 4GB full dump executed.
- **Remaining hard-block:** existing systemd backup service still runs old backend code, last backup failed, no verified remote full-data copy/independent off-host restore or `0188/0189` migration rehearsal; root disk about 89% full, 2/2 GiB swap full. Do not deploy new backend/migrate/activate Typography V2 until those prerequisites and rollback pass. Frontend prod remains previous verified main release.

### 2026-10-29 — Fast-track G4 Style Lab preview-only MVP merged

- **Implementation:** PR #63 merged into `main` as `8a8ad2c124f6cdd625efbcac480ca7592f3d5591`; **17/17 GitHub CI PASS** including frontend deterministic dist, Browser QA fixtures, API shards, PostgreSQL and GitGuardian. Local bounded Python tests 54 PASS. Worktree built and committed frontend `dist`, kept provenance metadata as in CI. No production runtime was deployed.
- **G4 vertical slice:** tenant-canary guarded `POST /typography/previews` requires a **picked Stage0 keyword**, `typography_reference.read` and keyword read grant, a visible approved rights-verified reference and human-accepted current Style DNA. Returns three deterministic SVG vector drafts with original Vietnamese/unicode keyword exactly preserved; no AI provider call, Stage1 job, storage or database write. Stage1 guarded navigation now has `Style Lab` plus row `Apply style`, 3-pane UI Reference / Previews / Balanced controls. Revoking rights invalidates prior approval. `concept-short-v1` and Stage2 untouched. UI and QA are MVP, NOT embroidery-ready final outputs.
- **Fast MVP path to runnable image:** build G5 exact-text, glyph/legibility, originality/diversity & partial-slot QA and G6 `concept-short-v2` pilot job integration OFFLINE with synthetic/rights-verified fixtures **in parallel** with G0.1 full remote database backup, off-host 4GB restore, 0188/0189 backward-compatibility/rollback proof and capacity plan. Pilot reference acquisition can be manual; Windows Typography Scout G7 must not block a manual-reference pilot but is required for full project acceptance.
- **Still blocked:** production backend release remains `d4573f7e`, actual DB migration `0187`, frontend production `2e5286434`. V2/canary/Scout/pilot OFF, no real 8-reference G3 or 20-keyword G9 pilot. Root 89% and swap nearly full. Production Stage1 Style Lab not visible until safe backend + migrations + canary/verified frontend release.

## 6. Quy tắc bảo toàn trạng thái

1. Không ghi `VERIFIED` nếu test hoặc CI chưa đạt, dù code đã commit. **HTML prototype/screenshot không phải bằng chứng feature được triển khai**.
2. Mỗi lệnh `tiếp tục` đọc bản STATE mới nhất, RUNBOOK và UI_BLUEPRINT, rồi kiểm tra bằng chứng từ tool hiện tại. Không thực thi âm thầm sau khi kết thúc lượt.
3. Khi merge PR, cập nhật `main SHA` và CI evidence; khi deploy phải kiểm tra **public version** + backend/frontend + Windows tool riêng biệt.
4. Nếu hard-block, giữ trạng thái `BLOCKED`; không bypass, không xóa hoặc reset dữ liệu để tiến tiếp.
5. Sau mỗi lượt nên commit cập nhật state trong docs theo nhánh phù hợp/PR; nếu không thể cập nhật repo vì tool/quyền, ghi rõ `checkpoint_not_persisted` và cung cấp bản `.md` đã cập nhật.
