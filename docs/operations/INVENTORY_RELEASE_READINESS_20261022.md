# Inventory — Hồ sơ sẵn sàng phát hành ngày 22/10/2026

## Trạng thái

**Đã chuẩn bị và kiểm chứng bản hợp nhất; chưa deploy production.**

GitHub đã kết nối đúng tài khoản `BaoNghiaNghia`, có quyền admin/push trên repository công khai `BaoNghiaNghia/creative-asset-manager`. Việc tạo cây Git chứa hồ sơ QA bị cơ chế duyệt tự động từ chối vì hồ sơ có thông tin vận hành thật và câu xác nhận nghiệm thu của người dùng. CodeLocal cũng chặn chẩn đoán production vì đường dẫn cấu hình nằm ngoài workspace được cấp quyền.

Chưa tạo commit, nhánh hoặc PR mới trên GitHub trong lượt này. `main` vẫn là `ef1e85b996426d9f068cbed0a4bc7cd2a2a8bb14`. Chưa chạy migration, kích hoạt release, restart production hay đổi cấu hình quyền truy cập.

## Bản đã chuẩn bị

| Thành phần | Định danh |
| --- | --- |
| Workspace phát hành | `/srv/creative-asset-manager-source/.inventory-approved-release-20261022` |
| Nhánh local | `release/inventory-approved-20261022` |
| Main được hợp nhất | `ef1e85b996426d9f068cbed0a4bc7cd2a2a8bb14` |
| Audit/Browser QA đã duyệt trước đó | `5bafa406daacdba900ad95df5961641a961b8570` |
| Commit nguồn hợp nhất M | `564d1e3d59fc7fcacb94c6a631fd0fd7028a6705` |
| Cây nguồn M | `6b5243c2a16b6ae4c77b2684e9ba9d10b6f9d4f6` |
| Commit gói build D | `8a4a222453d88b1a2b1ef6400167ca07a0153680` |
| Cây gói build D | `02744a81872ab09da45bae6a3200e9564fb0b670` |
| Nguồn ghi trong build-info | `564d1e3d59fc7fcacb94c6a631fd0fd7028a6705` |
| Thời điểm build UTC | `2026-10-21T22:58:13.068Z` |

M giữ cả 31 commit phía main và 7 commit phía audit kể từ tổ tiên chung. Các xung đột được giải quyết để giữ chức năng Browser Agent hiện có cùng các bảo vệ Inventory đã duyệt. Ba test bulk-approval phía main được giữ lại cùng bộ test audit. Không thay migration, workflow CI, dependency lockfile hoặc script deployment so với main EF.

Phần nghiệp vụ gồm kiểm tra quyền và bằng chứng khi duyệt vật tư, xử lý bằng chứng cũ/retry, bảo vệ carry-forward Q47, giữ metadata kiểm toán và sửa lifecycle request trên giao diện. Các tệp frontend Inventory cùng bộ baseline đã duyệt được giữ nguyên từ audit.

## Kết quả kiểm chứng trên bản hợp nhất

| Kiểm tra | Kết quả |
| --- | --- |
| Toàn bộ Inventory API | **825 passed**, 1 cảnh báo deprecation, 161,57 giây |
| Toàn bộ frontend | **814 passed / 146 tệp**, 79,13 giây |
| TypeScript | `tsc --noEmit` đạt |
| Bộ phân tích/planner QA | **86/86 passed** |
| Kiểm tra tĩnh production QA plan | **6/6 passed** |
| Build frontend | Đạt, 108 tệp artifact |
| Build tái lập theo CI | Đạt; dùng lại build commit/timestamp đã ghi, không có diff hoặc tệp mới trong committed dist |
| Browser QA nhân viên | **18/18 trạng thái**, strict/full |
| Browser QA quản lý | **12/12 trạng thái**, strict/full |
| Browser QA baseline đã duyệt | **15/15 match** |
| Browser QA profile inventory-daily của CI | **12/12 trạng thái**, strict/full; bỏ so sánh ảnh theo cấu hình CI |
| Tổng Browser QA | **57 trạng thái** trên desktop, tablet portrait, mobile |
| Kiểm tra cấu trúc Git | `git diff --check` đạt |

Cả 57 dòng quality đều có overflow, unnamed controls, missing alt và small targets bằng 0. Các mảng console errors, page errors, request failures, ignored request failures, HTTP bad responses và step failures đều rỗng.

15 so sánh baseline giữ nguyên `pixelThreshold=0.1`, `maxDiffRatio=0.001`, `updateBaselines=false`. “Match” là kết quả của comparator theo ngưỡng đó. Không thay hoặc duyệt lại baseline trong lượt chuẩn bị release này.

Browser dùng Chrome 155.0.8059.39 với viewport 1440×900, 768×1024 và 390×844. Các preview QA do lượt này khởi tạo đã được dừng; cổng 4183 được kiểm tra là đã trống.

Các lần chạy ở đây là kiểm chứng local. Chưa có CI của commit GitHub mới. Chưa thay thế bốn API shard toàn repository hoặc các job PostgreSQL 12/16. Kiểm thử SQLite không chứng minh lịch khóa đồng thời thật trên PostgreSQL.

Audit coverage của repository trả mã 0, profile Inventory khớp đủ các trạng thái yêu cầu. Công cụ vẫn báo một số baseline cũ ở profile khác, gồm inventory-daily, chưa khớp plan hiện tại. CI đang đặt `CAM_UI_VISUAL_SKIP=1` cho ma trận fixture; 12 trạng thái daily ở trên kiểm tra hành vi/layout, không xác nhận lại các baseline cũ đó.

## Bằng chứng được giữ nguyên

Các report của gói M được lưu tại `docs/operations/reviews/inventory-release-20261022/`:

| Tệp | SHA-256 |
| --- | --- |
| `staff.json` | `a3dbda482162ff76d6f5a68b24504b4eef4681c10b68ec4628f297ca6bf445d5` |
| `manager.json` | `253e5427c3899570ea6bf79bd7067e2db7f1021d0e9470705b6263d7c44d2373` |
| `baseline.json` | `12a36255771553b2697275dc43c4871c82e8f652c17f2d62e3b353723f4696ec` |
| `daily.json` | `269efecea2549dd878afe140b2193e906e3a0c26c15efd20710316ec7fe1d409` |
| `build-manifest.json` | `a76fcc31d79ce41005861b14a669209afb84c464ce82b22ba446b89afedf2c7c` |

`browser-summary.json` tổng hợp 57 trạng thái. `production-observation.json` ghi các phép GET chỉ đọc tới production. Log kiểm thử đầy đủ còn trong `apps/client/.ui-qa/`.

HTML đã duyệt trước đó: `docs/operations/reviews/inventory-review-20261021.html`, 5.197.334 byte, SHA-256 `fdc00bba5fd1fc6af9b1e2b77ccbd0669157b9b57aa827866d98ffc185917af4`. Cả 15 PNG baseline đã được đối chiếu byte/hash với file được chấp nhận; 12 PNG cũ được giữ nguyên.

Ảnh dùng fixture QA, tài khoản `qa-*`, địa chỉ `example.test`, dữ liệu vật tư và hash mẫu. Đã kiểm tra cả 15 ảnh baseline và 6 ảnh compiled trong HTML; không phát hiện người/tài khoản thật, credential, ID Sheets hay URL production trong ảnh, và PNG không có metadata văn bản/EXIF. HTML còn chứa thông tin vận hành thật: đường dẫn report nội bộ, commit/hash, thời điểm, kết quả QA và câu xác nhận Browser QA của người dùng.

## Chặn 1 — công khai hồ sơ QA trên GitHub

Một lần tải PNG bị từ chối ban đầu đã được duyệt lại sau khi xác minh nguồn fixture và repository. GitHub hiện đã nhận đủ 9 blob PNG riêng biệt và 1 blob HTML, khớp các Git SHA gốc. Các blob này chưa được gắn vào commit/nhánh phát hành mới. Cây trung gian chứa 36 tệp văn bản có SHA `e9e3fb3bff0c8c138602699f3e222bc5984f157a`.

**Lần tạo cây Git hoàn chỉnh bị automatic approval review từ chối.** Lý do: repository công khai, và hồ sơ HTML chứa metadata vận hành thật/câu nghiệm thu; yêu cầu deploy hiện có chưa được hệ thống xem là chấp thuận công khai riêng hồ sơ này. Không tạo cây/commit/PR qua đường khác sau từ chối.

Phạm vi cần xác nhận: cho phép công khai hồ sơ QA Inventory trong repository trên, gồm ảnh baseline, HTML/JSON/report phát hành, commit/hash/thời điểm, đường dẫn báo cáo nội bộ và xác nhận nghiệm thu có trong hồ sơ.

## Chặn 2 — truy cập production trong CodeLocal

Preflight của chẩn đoán production trả `BLOCKED` với lý do:

> explicit path escapes authorized workspace: /etc/creative-asset-manager/production.env

Không có approval token cho thao tác này. Workspace hiện được cấp quyền cho source; chế độ full không bỏ qua giới hạn đường dẫn này.

Cần một workspace hoặc capability production được chủ hệ thống cấp quyền rõ ràng cho chẩn đoán che dữ liệu nhạy cảm, đọc marker release hiện tại/previous, và chạy các script deployment/rollback của dự án. Chẩn đoán dùng `production_env.py run-redacted` cùng `auth_cli check-admin-setup --environment production`; không cần gửi nội dung production.env vào hội thoại.

Graph migration nguồn không đổi so với EF; sole head là `0176_catinat_waste_witness`. Chưa xác minh trực tiếp Alembic head và immutable release marker đang hoạt động. Các endpoint health công khai không chứng minh hai điều này.

Lúc **06:19:38 ngày 22/10/2026, giờ Việt Nam**, các GET production trả:

| Endpoint | Quan sát |
| --- | --- |
| `/version` | HTTP 200; version 0.4.0; commit EF `ef1e85b9…` |
| `/build-info.json` | HTTP 200; nguồn frontend `50ee0b35…`, build UTC 2026-10-21T15:25:32.000Z |
| `/live` | HTTP 200, ok |
| `/ready` | HTTP 200; PostgreSQL và Elasticsearch available |

Đây là phiên bản cũ đang phục vụ, không phải bản Inventory M/D vừa chuẩn bị.

## Các bước tiếp tục sau khi gỡ hai chặn

1. Hoàn tất cây Git đúng nội dung đã kiểm chứng, tạo commit/nhánh/PR trong repository được xác minh. GitHub connector tạo SHA commit mới, nên giữ các SHA local dưới dạng provenance và kiểm tra equality của tree; không mô tả chúng là tổ tiên GitHub nếu chưa tồn tại ở đó.
2. Fetch SHA nguồn GitHub thực tế, build tại SHA đó để build-info có provenance hợp lệ. So sánh toàn bộ artifact ứng dụng với build M, ngoại trừ build-info. Chỉ tái chạy QA nếu có khác biệt có ý nghĩa.
3. Commit dist, chạy CI trên đúng head PR, rồi merge với lịch sử giữ được commit nguồn build. Kiểm tra source build là tổ tiên của release và không có thay đổi frontend sau source ngoài dist.
4. Khi có đường production được cấp quyền, xác minh live schema, marker/rollback và preflight các lệnh release. Với schema tương thích, backend dùng script dự án cùng `--no-migrate --keep-releases 2`; frontend dùng script deploy frontend ở cùng commit release. Không xem việc chuẩn bị script là đã deploy.
5. Kiểm tra version/build-info, readiness và production UI smoke sau kích hoạt. Dùng rollback có sẵn nếu xác minh sau deploy thất bại; không tự downgrade database.

User đã yêu cầu deploy production và đã duyệt Browser QA. Không cần hỏi lại hai ý định đó. Các yêu cầu còn lại xuất phát từ automatic approval review đối với công khai hồ sơ và giới hạn truy cập production của CodeLocal.
