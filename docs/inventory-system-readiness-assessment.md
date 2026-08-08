# Đánh giá mức độ sẵn sàng của hệ thống để tích hợp kiểm kho tự động từ Google Drive

> Repository: `BaoNghiaNghia/creative-asset-manager`  
> Branch: `feature/google-drive-explorer-mvp`  
> Commit được đánh giá: `ac772dfaf8b4a91f329489cca4bc9496ab3a2951`  
> Commit message: `update build fe`  
> Ngày đánh giá: 2026-08-08  
> Mục tiêu: bổ sung quy trình **nhân viên upload ảnh kiểm kho lên Google Drive → hệ thống tự phát hiện → đọc AI → kiểm tra → nhập/xuất/tồn → chốt ngày → xuất Excel và báo cáo**.

---

# 1. Kết luận nhanh

Hệ thống hiện tại **đã có phần lớn hạ tầng nền tảng cần thiết** để xây tính năng kiểm kho mà không phải dựng một backend mới.

Đánh giá tổng thể:

| Hạng mục | Mức sẵn sàng |
|---|---:|
| Google Drive OAuth / token persistence | 9/10 |
| Google Drive read/write operations | 8/10 |
| Drive incremental sync | 9/10 |
| Durable processing jobs | 9/10 |
| Content deduplication | 9/10 |
| AI image preprocessing | 9/10 |
| AI provider governance / rate limit / budget | 9/10 |
| AI routing cho inventory | 4/10 |
| Inventory domain model | 0/10 |
| Inventory review workflow | 0/10 |
| Excel export | 2/10 |
| Inventory UI | 0/10 |
| Monitoring / audit foundation | 8/10 |
| Production readiness của commit hiện tại | 6/10 |

**Mức sẵn sàng tổng thể để bắt đầu phát triển tính năng: khoảng 75%.**

Có thể bắt đầu triển khai ngay sau khi xử lý các blocker ở mục 5.

Không nên bắt đầu bằng cách viết một service riêng đứng ngoài hệ thống. Tính năng inventory nên được xây như một **domain module mới** và tái sử dụng:

- Google OAuth hiện tại.
- `ExternalSourceModel` / `SourceAssetModel` / `AssetModel`.
- Source sync scheduler.
- Durable processing jobs.
- Content hash dedup.
- Managed asset storage.
- `AnalysisImagePreparer`.
- AI provider registry / budget governance.
- PostgreSQL / Alembic.
- Existing permission / tenant model.

---

# 2. Thay đổi quan trọng trong commit mới nhất

Commit `ac772df...` thay đổi một quyết định kiến trúc rất quan trọng so với đánh giá trước.

## 2.1. Google Drive connection hiện yêu cầu quyền read/write

`DRIVE_SCOPES` hiện sử dụng:

```text
https://www.googleapis.com/auth/drive
```

thay vì chỉ:

```text
https://www.googleapis.com/auth/drive.readonly
```

Điều này có nghĩa connection Google Drive mới có thể dùng cho:

- đọc ảnh từ Inbox;
- upload file;
- copy file;
- move file;
- delete file;
- ghi file Excel hoặc backup nếu user có quyền sửa folder tương ứng.

Đây là thay đổi tích cực cho tính năng inventory vì không còn bắt buộc phải có một OAuth connection thứ hai chỉ để archive hoặc xuất Excel.

Tuy nhiên, vẫn nên thiết kế service inventory yêu cầu rõ `require_drive_write_scope=True` trước mọi operation ghi.

## 2.2. Refresh token giữ đúng scope đã persist

Commit mới sửa luồng refresh để lấy scope từ OAuth connection đã lưu thay vì tạo lại credentials với scope mặc định không chính xác.

Điều này giảm nguy cơ:

- refresh token mất write scope;
- token mới không đồng nhất với connection ban đầu;
- worker hoạt động sau một thời gian rồi không ghi Drive được.

Đây là thay đổi phù hợp cho automation dài hạn.

## 2.3. Drive session health được cải thiện

`/api/auth/google/session` hiện trả thêm trạng thái connection như:

- `drive_connected`;
- `drive_usable`;
- `external_source_id`;
- `connection_status`;
- `reconnect_required`.

Inventory UI có thể tái sử dụng trạng thái này nhưng cần bổ sung `drive_writable` riêng; xem mục 5.

---

# 3. Kiến trúc hiện tại phù hợp với inventory ở mức nào

## 3.1. Google Drive Source Sync — rất phù hợp

Source hiện đã có:

```text
Google Drive Changes API
        ↓
ExternalSourceModel
        ↓
SourceAssetModel
        ↓
source_asset_download job
```

Scheduler định kỳ tạo `source_sync` job cho từng source.

Nếu source chưa có cursor, scheduler tự chọn `full` scan. Sau khi có cursor, hệ thống chuyển sang incremental sync.

Điều này phù hợp trực tiếp với yêu cầu:

> "Hệ thống tự check các file của ngày hôm đó và đem đi xử lý."

Không cần Google Apps Script polling riêng.

Inventory nên dùng:

```text
SOURCE_SYNC_POLL_INTERVAL_SECONDS=300
```

cho tần suất khoảng 5 phút.

Có thể giảm xuống 60–120 giây nếu cần phản hồi nhanh hơn.

## 3.2. Drive metadata đã có parent folder

Google Drive incremental mapper đang lưu:

```json
{
  "parents": ["folder-id"],
  "is_folder": false,
  "web_url": "..."
}
```

Do đó inventory có thể xác định file thuộc Inbox mà không cần gọi Drive API lại:

```text
INVENTORY_INBOX_FOLDER_ID in SourceAsset.source_metadata.parents
```

Khuyến nghị MVP:

```text
00_INBOX_NHAN_VIEN/
   image1.jpg
   image2.jpg
   image3.jpg
```

Yêu cầu nhân viên upload **trực tiếp** vào Inbox.

Không cho nhân viên tự tạo folder con trong MVP.

Lý do: descendant routing đòi hỏi thêm ancestry resolution và dễ làm quy trình vận hành phức tạp hơn.

## 3.3. Content dedup đã có sẵn

Source hiện đã có content hash và asset dedup ở pipeline.

Inventory không cần tải file rồi tự hash lại từ đầu.

Tuy nhiên inventory vẫn phải có idempotency riêng vì business semantics khác creative asset semantics.

Ví dụ cùng một ảnh được upload hai lần:

```text
Drive File A ─┐
              ├→ cùng content_hash
Drive File B ─┘
```

Creative Asset pipeline có thể map về cùng Asset.

Inventory cần đánh dấu:

```text
duplicate_submission
```

thay vì tạo hai transaction.

## 3.4. Worker/job system — phù hợp cao

Hệ thống đã có:

- job table bền vững;
- idempotency key;
- lease;
- retry;
- backoff;
- worker heartbeat;
- cancellation;
- failed status;
- tenant policy;
- global feature flags.

Do đó không nên dùng Celery, Redis queue hoặc một scheduler khác chỉ cho inventory ở giai đoạn này.

Inventory nên thêm job vào hệ thống hiện có.

## 3.5. AI preprocessing — nên tái sử dụng hoàn toàn

Source đã có `AnalysisImagePreparer`:

```text
managed asset
   ↓
size validation
   ↓
dimension validation
   ↓
EXIF orientation
   ↓
Pillow / libvips
   ↓
resize
   ↓
flatten transparency
   ↓
JPEG normalization
   ↓
analysis image hash
```

Không nên tạo:

```text
inventory/image_preprocessor.py
```

nếu chỉ lặp lại logic này.

Inventory nên gọi cùng infrastructure để tránh hai pipeline xử lý hình ảnh khác nhau.

## 3.6. AI governance — có thể tái sử dụng

AI service hiện có:

- provider registry;
- model allowlist;
- rate limits;
- budget reservation;
- cost tracking;
- provider emergency stop;
- analysis lease;
- JSON schema validation retry;
- OpenAI / Gemini support.

Inventory cần dùng chung governance này.

Không gọi trực tiếp SDK OpenAI/Gemini từ `inventory_service.py`.

---

# 4. Kiến trúc inventory nên tích hợp vào source hiện tại

Kiến trúc đề xuất:

```text
NHÂN VIÊN
    │
    │ upload JPG / PNG / WebP
    ▼
Google Drive
00_INBOX_NHAN_VIEN
    │
    ▼
SourceSyncScheduler
    │
    ▼
source_sync
    │
    ▼
SourceAssetModel
    │
    ▼
source_asset_download
    │
    ▼
Asset + content hash
    │
    ▼
asset_store
    │
    ▼
InventoryRoutingService
    │
    ├─ Không thuộc Inbox
    │      ↓
    │   Creative Asset pipeline hiện tại
    │
    └─ Thuộc Inbox
           ↓
 inventory_document_detect
           ↓
 inventory_document_analyze
           ↓
 AnalysisImagePreparer
           ↓
 inventory_stock_sheet_v1
           ↓
 JSON extraction
           ↓
 item normalization
           ↓
 business validation
           ↓
      ┌─────────────┐
      │             │
      ▼             ▼
 auto-approved   needs-review
      │             │
      └──────┬──────┘
             ▼
 inventory_transactions
             ↓
 daily inventory run
             ↓
 17:00 finalize
             ↓
 Excel export
             ↓
 Google Drive
```

---

# 5. Blocker phải xử lý trước khi triển khai inventory production

## BLOCKER 1 — Generic AI auto-analysis có thể chọn nhầm metadata profile

Đây là blocker lớn nhất.

Pipeline hiện tại sau khi asset được download/store có logic:

```text
AI_AUTO_ANALYZE_ENABLED
   ↓
select MetadataProfileModel
where active = true
order by created_at desc
limit 1
```

Điều này có nghĩa một ảnh kiểm kho có thể chạy prompt dành cho creative metadata.

### Bắt buộc sửa

Không để inventory phụ thuộc "active profile mới nhất".

Tạo explicit routing:

```text
Asset stored
   ↓
AssetPurposeRouter
   ↓
belongs_to_inventory_inbox?
   ├─ yes → InventoryAnalysisProfile
   └─ no  → current generic behavior
```

Inventory analysis phải persist chính xác:

```text
metadata_profile_id = inventory profile id
prompt_version = inventory-v1
pipeline_version = inventory-v1
```

Không chọn profile theo `created_at desc`.

### Khuyến nghị

Trong giai đoạn rollout đầu:

```text
AI_AUTO_ANALYZE_ENABLED=false
```

cho đến khi routing theo asset purpose hoàn tất.

---

## BLOCKER 2 — Chưa có domain `inventory`

Source chưa có:

- inventory locations;
- item master;
- aliases;
- documents;
- pages;
- extracted lines;
- transactions;
- review queue;
- daily runs;
- Excel exports.

Cần tạo module mới:

```text
apps/api/app/modules/inventory/
```

Không nhét dữ liệu kiểm kho vào generic metadata tables.

---

## BLOCKER 3 — Job type hiện đang whitelist cứng

`JobType` hiện chưa có inventory jobs.

Registry chỉ chấp nhận các job có trong `JOB_TYPES`.

Cần thêm tối thiểu:

```text
INVENTORY_DOCUMENT_DETECT
INVENTORY_DOCUMENT_ANALYZE
INVENTORY_DOCUMENT_NORMALIZE
INVENTORY_DOCUMENT_VALIDATE
INVENTORY_DOCUMENT_ARCHIVE
INVENTORY_DAILY_FINALIZE
INVENTORY_EXCEL_EXPORT
INVENTORY_DAILY_REPORT
```

Sau đó đăng ký handlers trong:

```text
apps/api/app/modules/processing/bootstrap.py
```

và thêm global flags / tenant policy tương ứng.

---

## BLOCKER 4 — CI của commit hiện tại đang fail

GitHub Actions run của commit `ac772df...` hiện có kết luận:

```text
CI: failure
```

Trước khi bắt đầu một feature lớn, branch nên quay lại trạng thái CI xanh.

Trong môi trường đánh giá hiện tại không có GitHub CLI nên chưa lấy được log Actions để kết luận job nào fail.

Yêu cầu trước khi merge inventory work:

```text
frontend             success
api-unit             success
postgres-integration success
elasticsearch        success
pipeline-e2e         success
production gate      success
```

Không nên triển khai inventory trên một baseline đang đỏ vì sẽ khó phân biệt regression cũ và regression mới.

---

## BLOCKER 5 — Drive write capability chưa được biểu diễn rõ trên session API

Commit mới cho `drive_usable=true` khi connection có:

```text
drive.readonly OR drive
```

Nhưng inventory export/archive cần:

```text
drive
```

Do đó cần bổ sung:

```json
{
  "drive_connected": true,
  "drive_readable": true,
  "drive_writable": true
}
```

Inventory Settings không được chỉ kiểm tra `drive_usable`.

### Migration case

Một workspace cũ có readonly token có thể vẫn browse Drive được nhưng không:

- move ảnh;
- upload Excel;
- archive file.

Inventory phải báo:

```text
Google Drive needs reconnection with read/write access.
```

trước khi bật automation.

---

## BLOCKER 6 — Một lỗi hiện tại trong Explorer upload cần sửa trước khi tái sử dụng

Trong `POST /explorer/upload`, sau khi upload thành công có đoạn invalidate breadcrumb dùng:

```text
item_id=item_id
```

nhưng endpoint upload không có biến `item_id` tại vị trí đó.

Điều này có thể tạo lỗi sau khi Google Drive đã upload file thành công.

Không nên dùng HTTP Explorer Upload endpoint làm primitive cho Excel exporter trước khi sửa lỗi này.

Inventory worker nên dùng provider/service trực tiếp thay vì tự gọi API của chính application.

---

## BLOCKER 7 — Move/Delete cần enforce write scope nhất quán

Upload và copy đang gọi source resolver với:

```text
require_drive_write_scope=True
```

Nhưng move/delete hiện chưa enforce nhất quán ở entry point.

Do connection mới mặc định là `drive` nên hoạt động bình thường sau reconnect, nhưng connection legacy readonly có thể lọt đến provider rồi fail 403.

Cần chuẩn hóa:

```text
upload → require write
copy   → require write
move   → require write
delete → require write
```

Inventory archive service cũng phải yêu cầu write scope trước khi thực hiện.

---

# 6. Các vấn đề cần xử lý nhưng không chặn POC

## 6.1. MIME support hiện chỉ có JPEG / PNG / WebP ở ingestion

Source sync chỉ đưa Google Drive file vào download pipeline nếu MIME là:

```text
image/jpeg
image/png
image/webp
```

Trong khi `AnalysisImagePreparer` bên trong có thể decode nhiều format hơn như HEIC/HEIF/TIFF/AVIF.

Hiện tại HEIC vẫn bị chặn trước bước AI.

### MVP recommendation

Yêu cầu nhân viên upload:

```text
JPG / JPEG
```

là đủ.

Không mở rộng MIME trong giai đoạn đầu.

Sau khi quy trình ổn định mới thêm HEIC nếu thực tế có nhu cầu.

---

## 6.2. README đang không còn đúng với code

README hiện vẫn nói Google Drive sử dụng:

```text
drive.readonly
```

và OAuth refresh token giữ trong process.

Trong code hiện tại:

- Drive connect yêu cầu full `drive` scope.
- Persistent auth/database flow đã tồn tại.

README cần được cập nhật để tránh developer triển khai dựa trên thông tin cũ.

---

## 6.3. Archive file sẽ sinh Drive change mới

Khi inventory move file:

```text
00_INBOX
   ↓
01_DA_XU_LY
```

Google Drive Changes API sẽ phát hiện file lại.

Do đó hệ thống phải route theo parent folder và idempotency.

Ví dụ:

```text
Source change after move
     ↓
parent != Inbox
     ↓
không tạo inventory analyze job mới
```

Ngoài ra vẫn dùng:

```text
content_hash
source_asset_id
inventory_document_page
```

để chống duplicate.

---

## 6.4. Không dùng "ngày hôm nay" làm điều kiện duy nhất

Quy tắc đúng:

```text
process every unprocessed Inbox file
```

sau đó xác định `business_date` theo:

1. ngày trên phiếu;
2. source created/upload time ở `Asia/Ho_Chi_Minh`;
3. manager correction.

Nếu job lỗi từ ngày hôm trước và được retry hôm nay thì không được đổi business date.

---

# 7. Database design khuyến nghị

Tạo migration riêng cho inventory.

## `inventory_locations`

```text
id
tenant_id
code
name
active
```

Codes ban đầu:

```text
KHO_PHA_CHE
PHONG_PHA_CHE
KHO_PHONG_RANG
```

## `inventory_folder_bindings`

```text
id
tenant_id
external_source_id
inbox_folder_id
processed_folder_id
reupload_folder_id
excel_folder_id
backup_folder_id
excel_template_file_id
timezone
active
```

Không lưu Folder ID chỉ trong env.

Env có thể là fallback local-development.

## `inventory_documents`

```text
id
tenant_id
business_date
document_type
location_code
status
expected_pages
received_pages
submitted_by
approved_by
approved_at
finalized_at
```

## `inventory_document_pages`

```text
id
tenant_id
document_id
source_asset_id
asset_id
drive_file_id
content_hash
page_number
page_count
raw_extraction_json
analysis_status
created_at
```

Không nên unique toàn tenant chỉ bằng content hash nếu muốn lưu lịch sử duplicate submission.

Khuyến nghị:

```text
unique(tenant_id, source_asset_id)
```

và có field:

```text
duplicate_of_page_id
```

khi content hash trùng.

## `inventory_item_master`

```text
id
tenant_id
sku
name
base_unit
whole_unit
fraction_unit
conversion_factor
category
active
```

## `inventory_item_aliases`

```text
id
tenant_id
item_id
alias
normalized_alias
```

Ví dụ:

```text
TC OLONG      → TRÂN CHÂU OLONG
RICHS (LÙN)  → RICH LÙN
BỘT Đ.XAY     → BỘT BÉO ĐÁ XAY
```

## `inventory_lines`

Lưu cả raw và normalized values.

## `inventory_transactions`

```text
opening_balance
receipt
transfer_in
transfer_out
closing_count
waste
usage_adjustment
```

## `inventory_review_items`

Lưu:

- reason code;
- original value;
- AI suggestion;
- corrected value;
- reviewer;
- reviewed_at.

## `inventory_daily_runs`

Một row/tenant/date để quản lý trạng thái chốt ngày.

## `inventory_exports`

Theo dõi Excel output và backup.

---

# 8. Job design khuyến nghị

## Job 1 — `inventory_document_detect`

Input:

```json
{
  "source_asset_id": "...",
  "asset_id": "..."
}
```

Nhiệm vụ:

- kiểm tra file thuộc Inbox;
- kiểm tra duplicate submission;
- tạo page record;
- enqueue analyze.

Idempotency:

```text
inventory-detect:{source_asset_id}:{provider_version}
```

## Job 2 — `inventory_document_analyze`

- lấy managed asset;
- dùng `AnalysisImagePreparer`;
- tạo analysis với inventory metadata profile ID cụ thể;
- gọi provider qua AI governance hiện tại;
- lưu raw extraction.

Idempotency:

```text
inventory-analyze:{page_id}:{profile_version}
```

## Job 3 — `inventory_document_normalize`

- map item master;
- alias matching;
- unit normalization.

## Job 4 — `inventory_document_validate`

Business rules:

- số không âm;
- item tồn tại;
- unit hợp lệ;
- page đủ;
- waste có reason;
- anomaly threshold;
- duplicate image;
- transfer consistency.

## Job 5 — `inventory_document_archive`

Chỉ chạy sau khi document được approve/finalize.

Trước move:

```text
require_drive_write_scope=True
```

## Job 6 — `inventory_daily_finalize`

Chạy theo timezone business.

Idempotency:

```text
inventory-finalize:{tenant}:{yyyy-mm-dd}:{run_version}
```

## Job 7 — `inventory_excel_export`

- download workbook template/current month;
- copy to temporary file;
- update allowlisted sheets;
- verify protected sheet;
- upload backup;
- replace/update main workbook;
- save Drive IDs/hash.

## Job 8 — `inventory_daily_report`

Tạo summary cho UI và notification channel.

---

# 9. Scheduler design

Source sync scheduler đã chạy trong worker process.

Inventory cần thêm scheduler cho:

```text
16:30 missing check
16:50 final scan
17:00 finalize
17:10 export/report
```

### Không nên

Chỉ dựa vào `threading.Timer` mà không có database idempotency.

Nếu deployment có nhiều worker replica, mỗi worker có thể chạy scheduler.

### Nên

Mỗi scheduled action tạo durable job với deterministic idempotency key.

Ví dụ:

```text
inventory-daily-finalize:{tenant}:{date}
```

Dù ba scheduler replica cùng enqueue, database chỉ giữ một job.

---

# 10. AI extraction design

Tạo metadata profile riêng:

```text
inventory_stock_sheet_v1
```

Output schema tối thiểu:

```json
{
  "document_type": "stock_count",
  "business_date": "2026-08-08",
  "location_code": "PHONG_PHA_CHE",
  "page_number": 1,
  "page_count": 2,
  "employee_name": null,
  "rows": [
    {
      "line_number": 1,
      "raw_item_name": "TC OLONG",
      "whole_quantity": 1,
      "whole_unit": "goi",
      "fraction_quantity": 250,
      "fraction_unit": "gram",
      "waste_quantity": 0,
      "waste_unit": null,
      "waste_reason": null,
      "confidence": 0.97,
      "notes": null
    }
  ],
  "warnings": []
}
```

AI không được:

- tự tạo item mới;
- đoán số không rõ;
- tự đổi đơn vị nếu không có conversion rule;
- bỏ qua handwriting ngoài bảng;
- biến empty thành zero nếu không nhìn thấy số.

---

# 11. Review workflow

Đề xuất ba mức:

```text
AUTO_APPROVE
NEEDS_REVIEW
NEEDS_REUPLOAD
```

Confidence chỉ là một input.

Auto approval cần đồng thời:

```text
confidence >= threshold
AND item match unique
AND units valid
AND all required fields present
AND no anomaly
AND no duplicate
AND document pages complete
```

Nếu bất kỳ điều kiện nào fail:

```text
NEEDS_REVIEW
```

Không sửa dữ liệu AI trực tiếp mà không audit.

---

# 12. Excel export

`openpyxl` chưa có trong requirements.

Cần thêm:

```text
openpyxl
```

Excel vẫn là output, không phải source of truth.

PostgreSQL là nguồn dữ liệu chính.

## Protected sheet

Sheet:

```text
Báo cáo sử dụng NVL trong ca
```

phải được giữ nguyên.

Nên kiểm tra trước/sau bằng serialized cell snapshot hoặc hash.

Ví dụ:

```text
before = protected_sheet_snapshot(workbook)
update_allowed_sheets()
after = protected_sheet_snapshot(workbook)
assert before == after
```

Không chỉ "không chủ động sửa sheet 4"; phải test để đảm bảo library hoặc workbook manipulation không thay đổi nội dung ngoài ý muốn.

---

# 13. Drive archive strategy

Vì source hiện đã có write scope, có hai lựa chọn.

## Option A — Move ảnh sau xử lý

```text
00_INBOX_NHAN_VIEN
    ↓
01_DA_XU_LY/YYYY-MM-DD
```

Ưu điểm:

- Inbox luôn sạch;
- nhân viên dễ nhìn file chưa xử lý.

Nhược điểm:

- move sinh Drive change mới;
- cần chống reprocessing.

## Option B — Không move ảnh trong MVP

Chỉ đánh dấu DB:

```text
processed_at
inventory_document_id
```

Ưu điểm:

- đơn giản;
- giảm write operations;
- giảm sync churn.

Khuyến nghị:

> **POC dùng Option B. Production ổn định mới bật Option A.**

---

# 14. API cần bổ sung

## Settings

```text
GET  /api/inventory/settings
PUT  /api/inventory/settings
POST /api/inventory/settings/folders
POST /api/inventory/settings/test-drive
```

`test-drive` phải kiểm tra:

- source exists;
- Inbox readable;
- output folder writable;
- template readable;
- write scope available.

## Daily

```text
GET  /api/inventory/daily-runs/{date}
POST /api/inventory/daily-runs/{date}/scan
POST /api/inventory/daily-runs/{date}/finalize
POST /api/inventory/daily-runs/{date}/reopen
GET  /api/inventory/daily-runs/{date}/report
```

## Documents

```text
GET  /api/inventory/documents
GET  /api/inventory/documents/{id}
POST /api/inventory/documents/{id}/reanalyze
POST /api/inventory/documents/{id}/approve
POST /api/inventory/documents/{id}/reject
```

## Review

```text
GET   /api/inventory/reviews
PATCH /api/inventory/reviews/{id}
POST  /api/inventory/reviews/{id}/approve
POST  /api/inventory/reviews/{id}/request-reupload
```

## Items

```text
GET/POST/PATCH /api/inventory/items
GET/POST/DELETE /api/inventory/item-aliases
```

## Export

```text
POST /api/inventory/exports/daily
POST /api/inventory/exports/monthly
GET  /api/inventory/exports
```

---

# 15. UI cần bổ sung

Client routing hiện tập trung ở:

```text
apps/client/app/AppRoute.tsx
```

Đề xuất routes:

```text
/inventory
/inventory/review
/inventory/items
/inventory/reports
/inventory/settings
```

Pages:

```text
InventoryTodayPage
InventoryReviewPage
InventoryItemsPage
InventoryReportsPage
InventorySettingsPage
```

## Inventory Today

Hiển thị:

```text
PHÒNG PHA CHẾ      Đủ / thiếu
KHO PHÒNG RANG     Đủ / thiếu
PHIẾU XUẤT KHO     x phiếu
ẢNH ĐÃ XỬ LÝ       x/y
CẦN REVIEW         n dòng
LAST DRIVE SCAN    timestamp
```

---

# 16. Permission model

Không nên dùng `assets.manage` cho toàn bộ inventory.

Thêm permissions riêng:

```text
inventory.read
inventory.review
inventory.items.manage
inventory.finalize
inventory.export
inventory.configure
```

Mapping gợi ý:

| Role | Permission |
|---|---|
| Viewer | inventory.read |
| Supervisor | read + review |
| Inventory Manager | read + review + finalize + export |
| Tenant Admin | tất cả |

Drive write operations chỉ chạy server-side qua tenant source.

Không dùng OAuth token của viewer.

---

# 17. Feature flags nên bổ sung

```text
INVENTORY_AUTOMATION_ENABLED
INVENTORY_AI_ENABLED
INVENTORY_AUTO_APPROVE_ENABLED
INVENTORY_ARCHIVE_ENABLED
INVENTORY_DAILY_FINALIZE_ENABLED
INVENTORY_EXCEL_EXPORT_ENABLED
```

Inventory job global flags có thể dựa trên:

```text
PROCESSING_JOBS_ENABLED
UNIFIED_ASSET_INGESTION_ENABLED
```

AI inventory cần thêm:

```text
DYNAMIC_AI_METADATA_ENABLED
AI_SINGLE_ANALYSIS_ENABLED
```

Không bắt inventory phụ thuộc Elasticsearch.

---

# 18. Cấu hình cần có

Ví dụ:

```dotenv
INVENTORY_AUTOMATION_ENABLED=false
INVENTORY_TIMEZONE=Asia/Ho_Chi_Minh
INVENTORY_SCAN_INTERVAL_SECONDS=300
INVENTORY_MISSING_CHECK_TIME=16:30
INVENTORY_FINAL_SCAN_TIME=16:50
INVENTORY_FINALIZE_TIME=17:00
INVENTORY_EXPORT_TIME=17:10
INVENTORY_AUTO_APPROVE_CONFIDENCE=0.95
INVENTORY_REVIEW_CONFIDENCE=0.80
INVENTORY_MAX_FILE_BYTES=10000000
INVENTORY_AI_PROFILE_NAME=inventory_stock_sheet_v1
INVENTORY_EXCEL_EXPORT_ENABLED=false
INVENTORY_ARCHIVE_ENABLED=false
```

Folder IDs và template IDs nên persist trong DB theo tenant, không đặt cố định trong env production.

---

# 19. Test strategy

## Unit

- folder routing;
- duplicate logic;
- aliases;
- unit conversion;
- confidence rules;
- transaction formula;
- transfer atomicity;
- Excel protected sheet;
- business date timezone.

## Integration

- Drive change → inventory detect;
- duplicate file;
- move archive → không reprocess;
- expired OAuth → refresh → write Drive;
- readonly legacy connection → settings test fails writable check;
- worker retry;
- profile routing;
- AI invalid JSON;
- AI low confidence;
- Excel export retry.

## E2E

```text
upload ảnh
→ source sync
→ download
→ content hash
→ inventory routing
→ AI extraction
→ review
→ finalize
→ Excel
→ backup
```

## Regression bắt buộc

Inventory feature không được phá:

- Explorer browse;
- upload/copy/move/delete;
- creative asset AI;
- Search V3;
- access management;
- existing processing jobs.

---

# 20. Rollout plan đề xuất

## Phase 0 — Baseline cleanup

Trước feature:

1. CI xanh.
2. Sửa upload `item_id` undefined.
3. Enforce write scope cho move/delete.
4. Thêm `drive_writable` vào session/capabilities.
5. Update README cho đúng OAuth scope hiện tại.

## Phase 1 — Inventory skeleton

- migration;
- models;
- repository;
- permissions;
- settings API;
- folder binding.

Không AI.

## Phase 2 — Inbox detection

- detect file thuộc Inbox;
- page records;
- duplicate detection;
- dashboard "files discovered".

Không transaction.

## Phase 3 — Inventory AI

- explicit inventory profile;
- routing;
- extraction schema;
- raw result;
- review queue.

## Phase 4 — Master data

- items;
- aliases;
- unit conversions;
- validation.

## Phase 5 — Transactions

- opening;
- receipt;
- transfer;
- closing;
- waste;
- usage calculation.

## Phase 6 — Daily run

- completeness;
- missing warning;
- finalize;
- reopen;
- audit.

## Phase 7 — Excel

- openpyxl;
- protected sheet tests;
- backup;
- Drive upload.

## Phase 8 — Archive

Chỉ sau khi toàn pipeline ổn định.

## Phase 9 — Shadow production

Trong 7–14 ngày:

```text
manual process
+
automated inventory process
```

So sánh kết quả từng ngày.

## Phase 10 — Cutover

Chỉ bật auto-finalize sau khi:

```text
>= 99% ảnh được phát hiện
>= 98% dòng chuẩn được extract đúng
100% dòng low-confidence được review
0 transaction duplicate
0 protected-sheet change
```

---

# 21. Backlog kỹ thuật theo thứ tự ưu tiên

## P0 — phải làm trước inventory

- [ ] CI xanh cho `ac772df...` hoặc commit kế tiếp.
- [ ] Fix upload undefined `item_id`.
- [ ] Move/delete require write scope.
- [ ] Add `drive_writable` capability.
- [ ] README OAuth documentation update.

## P1 — inventory core

- [ ] Alembic inventory migration.
- [ ] Inventory models/repository.
- [ ] Inventory permissions.
- [ ] Folder binding API/UI.
- [ ] Inventory feature flags.
- [ ] Inventory JobType enum.
- [ ] Worker handler registration.

## P2 — Drive → inventory

- [ ] Inbox route by `parents`.
- [ ] Duplicate submission logic.
- [ ] Inventory document/page creation.
- [ ] Manual scan endpoint.

## P3 — AI

- [ ] `inventory_stock_sheet_v1` profile.
- [ ] Purpose/profile routing.
- [ ] Extraction schema.
- [ ] Review queue.
- [ ] AI cost metrics tagged as inventory.

## P4 — business logic

- [ ] Item master.
- [ ] Alias mapping.
- [ ] Unit conversion.
- [ ] Transactions.
- [ ] Daily run.

## P5 — reporting

- [ ] `openpyxl` dependency.
- [ ] Excel exporter.
- [ ] Protected sheet verification.
- [ ] Daily report.
- [ ] Monthly report.

## P6 — production hardening

- [ ] Archive move.
- [ ] Retention.
- [ ] Metrics.
- [ ] Alerting.
- [ ] E2E tests.
- [ ] Shadow rollout.

---

# 22. Những phần của tài liệu tích hợp cũ cần sửa

File hiện tại:

```text
docs/google-drive-inventory-automation.md
```

vẫn đúng về mục tiêu nghiệp vụ nhưng cần cập nhật các điểm sau.

## Sửa

### Drive permissions

Không còn coi Source Drive là readonly.

Commit mới yêu cầu write scope khi kết nối Drive.

### Archive strategy

Nên chuyển thành:

```text
POC: DB state only
Production: optional move/archive
```

### AI routing

Phải thêm explicit inventory profile router trước generic auto-analysis.

### Feature flags

Phải bao gồm:

```text
UNIFIED_ASSET_INGESTION_ENABLED
CONTENT_DEDUP_ENABLED
```

### Job registration

Phải chỉ rõ:

```text
domain/processing/types.py
modules/processing/bootstrap.py
processing policy
```

### Drive capabilities

Thêm kiểm tra `drive_writable`.

### Existing bugs

Thêm Phase 0 cleanup trước khi inventory.

### Excel

Worker dùng provider/service trực tiếp; không tự gọi `/explorer/upload` nội bộ.

---

# 23. Go / No-Go assessment

## Có thể bắt đầu development?

**GO.**

Hạ tầng nền tảng đủ tốt để bắt đầu domain inventory.

## Có nên bật production ngay sau khi code xong?

**NO-GO cho đến khi:**

```text
CI xanh
Drive writable health check tồn tại
Inventory AI routing explicit
Duplicate submission protection hoàn tất
Review workflow hoàn tất
Excel protected sheet test xanh
Shadow run hoàn tất
```

---

# 24. Kiến trúc cuối cùng được khuyến nghị

Không xây:

```text
Google Apps Script
n8n riêng
Cloudflare Worker riêng
SQLite inventory riêng
server riêng cho OCR
```

ở giai đoạn này.

Source hiện tại đã có đủ nền tảng.

Kiến trúc tốt nhất là:

```text
creative-asset-manager
│
├── existing Drive source sync
├── existing durable worker
├── existing asset storage
├── existing AI governance
│
└── modules/inventory
       ├── folder bindings
       ├── documents
       ├── extraction
       ├── normalization
       ├── review
       ├── transactions
       ├── daily runs
       ├── reports
       └── Excel export
```

Tính năng inventory nên là **một bounded domain trong hệ thống hiện tại**, không phải một automation script gắn ngoài.

---

# 25. Definition of Ready để Codex bắt đầu code

Trước khi giao implementation cho Codex, cần chốt:

- [ ] commit baseline CI xanh;
- [ ] Folder Inbox ID;
- [ ] Folder output Excel ID;
- [ ] Excel template thực tế;
- [ ] tên chính xác các sheet;
- [ ] sheet cần bảo vệ;
- [ ] danh mục hàng chuẩn;
- [ ] alias hiện có;
- [ ] quy tắc đơn vị nguyên/lẻ;
- [ ] mẫu phiếu Phòng pha chế;
- [ ] mẫu phiếu Kho phòng rang;
- [ ] mẫu Phiếu xuất kho;
- [ ] confidence thresholds;
- [ ] policy khi thiếu ảnh lúc 17:00;
- [ ] policy archive ảnh;
- [ ] AI provider/model cho pilot.

Khi đủ checklist này, feature có thể được chia thành PR nhỏ theo Phase 0 → Phase 9 thay vì một PR lớn.
