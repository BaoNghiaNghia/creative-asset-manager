---
project: CAM-TYPOGRAPHY-ENGINE-V2
state_schema_version: 2
updated_at: 2026-10-28
status: APPROVED_SPEC_NOT_IMPLEMENTED
active_gate: G0
next_action: G0.0
canonical_runbook: docs/operations/CAM_TYPOGRAPHY_ENGINE_V2_RUNBOOK.md
canonical_state: docs/operations/CAM_TYPOGRAPHY_ENGINE_V2_STATE.md
canonical_ui_blueprint: docs/operations/CAM_TYPOGRAPHY_ENGINE_V2_UI_BLUEPRINT.md
canonical_ui_prototype: docs/operations/prototypes/CAM_TYPOGRAPHY_ENGINE_V2_UI_PREVIEW.html
ui_design_status: UI_TARGET_DEFINED_NOT_IMPLEMENTED
ui_blueprint_version: 1.0.0
ui_acceptance: UX-01..UX-16_PENDING
feature_flag: OFF
scout_flag: OFF
vps_main_commit: 4bd2ee7be7739bb8b11334d9ff3b0bcd90e90fdf
feature_branch: docs/typography-engine-v2-20261028
pr: 38
ci: PR38_RECHECK_PENDING_AFTER_MAIN_SYNC
vps_release_commit: null
windows_active_version: VERIFIED_0.1.46
last_verified_at: 2026-10-28
blockers: []
rollback:
  backend: UNVERIFIED_AT_START
  frontend: UNVERIFIED_AT_START
  windows: UNVERIFIED_AT_START
---

# CAM Typography Engine V2 — Execution State & Checkpoint

> **Chú ý:** Đây là checkpoint ban đầu của **tính năng mới chưa triển khai**. Các đường dẫn source/phiên bản cũ mới chỉ được ghi nhận trong quá trình lập kế hoạch, không thay thế gate kiểm tra hiện tại. Mọi trạng thái chuyển sang VERIFIED phải có test/evidence.

## 1. Next action — nhiệm vụ được chọn sẵn

**G0.0 — Persist four artifacts (RUNBOOK, STATE, UI_BLUEPRINT, HTML PROTOTYPE) to repository; then G0.1 Read-only baseline audit** trên VPS và Windows Tool, theo mục G0 của `CAM_TYPOGRAPHY_ENGINE_V2_RUNBOOK.md`.

Công việc mở đầu:
1. Đọc RUNBOOK + STATE + UI_BLUEPRINT + HTML prototype. Nếu chưa có trong `main`, đưa **cả bốn artifact** vào `docs/operations/` (prototype nằm trong `docs/operations/prototypes/`) qua clean worktree + documentation-only PR + CI, không chạm production.
2. Xác minh `origin/main`, git/worktree và release current/previous trên VPS; **checkout root đã từng dirty, không reset/clean**.
3. Xác minh máy tool `DESKTOP-3FR0LF0`, `D:\Desify_Agentic\scan_pinterest`, Scout Manager active release/version, health/pairing mà **không lấy hoặc in token**.
4. Xác minh Stage 1 exact-six validator, reference upload, Stage 2 handoff contract và nguồn/tính sẵn sàng của các reference được chọn để pilot.
5. Cập nhật bảng evidence; nếu gate G0 đủ điều kiện, tạo worktree sạch từ `origin/main` rồi bắt đầu G1 theo thứ tự.

## 2. Trạng thái các gate

| Gate | Tên | Trạng thái | Evidence cần bổ sung |
|---|---|---|---|
| G0 | Baseline & safety preflight VPS/Windows | PENDING | main SHA, worktree, health, rollback, version, risk |
| G1 | Data contract/schema/API nền tảng | PENDING | migrations, API tests, tenant isolation |
| G2 | Web Typography Ideas Library & Stage 1 subnav | PENDING | batch upload/dedup, UI QA UX-01..UX-04, UX-08 |
| G3 | Visual Analyzer & Style DNA | PENDING | sample DNA, cache/analyzer metrics |
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
- **UI design lock V1**: Stage 1 subnav chỉ `Keyword Jobs` / `Typography Ideas` / `Style Lab`; Windows mock là màn hình preview riêng, không phải CAM Web tab. Desktop Style Lab 3 vùng và responsive theo UI_BLUEPRINT §§2–9. Không hỏi lại thiết kế mặc định khi user gõ “tiếp tục”.
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

## 6. Quy tắc bảo toàn trạng thái

1. Không ghi `VERIFIED` nếu test hoặc CI chưa đạt, dù code đã commit. **HTML prototype/screenshot không phải bằng chứng feature được triển khai**.
2. Mỗi lệnh `tiếp tục` đọc bản STATE mới nhất, RUNBOOK và UI_BLUEPRINT, rồi kiểm tra bằng chứng từ tool hiện tại. Không thực thi âm thầm sau khi kết thúc lượt.
3. Khi merge PR, cập nhật `main SHA` và CI evidence; khi deploy phải kiểm tra **public version** + backend/frontend + Windows tool riêng biệt.
4. Nếu hard-block, giữ trạng thái `BLOCKED`; không bypass, không xóa hoặc reset dữ liệu để tiến tiếp.
5. Sau mỗi lượt nên commit cập nhật state trong docs theo nhánh phù hợp/PR; nếu không thể cập nhật repo vì tool/quyền, ghi rõ `checkpoint_not_persisted` và cung cấp bản `.md` đã cập nhật.
