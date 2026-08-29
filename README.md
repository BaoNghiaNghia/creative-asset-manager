# Creative Asset Manager

Creative Asset Manager (CAM) là nền tảng đa tenant dùng để quản lý, xử lý và tìm kiếm tài sản sáng tạo từ Google Drive và SharePoint. Hệ thống đồng bộ metadata từ nguồn, lưu trạng thái nghiệp vụ trong PostgreSQL, tạo thumbnail/projection, phân tích ảnh và video bằng AI, lập chỉ mục tìm kiếm và cung cấp các công cụ vận hành tập trung.

## Tính năng chính

- **Asset Explorer**: duyệt thư mục theo nhu cầu, breadcrumb, tìm kiếm, lọc ảnh/video, xem thumbnail và chi tiết tài sản.
- **Nguồn dữ liệu đám mây**: kết nối Google Drive và SharePoint bằng các phiên OAuth độc lập.
- **Quản lý tài sản**: gắn tag, chấm điểm, đổi tên, sao chép, di chuyển và đưa file hoặc thư mục vào thùng rác.
- **Pipeline xử lý**: đồng bộ nguồn, tải file, lưu trữ được quản lý, tạo projection/thumbnail, phân tích AI và lập chỉ mục video.
- **AI Operations**: theo dõi hàng đợi, tiến độ, lỗi, chi phí ước tính và thao tác retry/cancel có kiểm soát.
- **Tìm kiếm V3**: tìm kiếm tài sản trên Elasticsearch với quy trình rebuild và chuyển alias an toàn.
- **Inventory**: tự động tạo snapshot, đối soát dữ liệu Google Sheet và theo dõi daily run theo múi giờ của tenant.
- **Vận hành production**: release bất biến, health probe, worker ảnh/video tách biệt, audit log, retention và sao lưu cơ sở dữ liệu.

## Kiến trúc tổng quan

| Thành phần | Công nghệ | Vị trí |
| --- | --- | --- |
| Giao diện web | React 18, TypeScript, Vite | `apps/client` |
| API | FastAPI, SQLAlchemy, Alembic | `apps/api` |
| Worker xử lý | Python worker theo vai trò ảnh/video | `apps/worker` |
| Inventory scheduler | Bộ lập lịch cho daily inventory | `apps/inventory_scheduler` |
| Inventory worker | Worker chạy snapshot và reconciliation | `apps/inventory_worker` |
| Cơ sở dữ liệu | PostgreSQL | cấu hình qua `DATABASE_URL` |
| Tìm kiếm | Elasticsearch | Search V3 |
| Nguồn tài sản | Google Drive, Microsoft SharePoint | provider adapters trong API |

PostgreSQL là nguồn dữ liệu nghiệp vụ chính. File gốc tiếp tục nằm tại provider; pipeline có thể tạo bản sao, projection và thumbnail trong vùng lưu trữ được quản lý tùy theo chính sách cấu hình. Các công việc nền được lưu bền vững để API và worker có thể phối hợp, retry và tiếp tục sau khi tiến trình khởi động lại.

## Cấu trúc repository

```text
apps/
  api/                  FastAPI, migration và nghiệp vụ backend
  client/               Ứng dụng React/Vite
  worker/               Worker xử lý ảnh và video
  inventory_scheduler/  Bộ lập lịch Inventory
  inventory_worker/     Worker Inventory
database/migrations/    Alembic migrations
deploy/                 Mẫu cấu hình và công cụ production
docs/                   Kiến trúc, kế hoạch và runbook vận hành
infrastructure/         Cấu hình hạ tầng
scripts/                Script phát triển, kiểm thử và triển khai
```

## Yêu cầu môi trường

- Python 3.12.
- Node.js 18 trở lên; CI hiện sử dụng Node.js 22.
- PostgreSQL cho môi trường dùng dữ liệu bền vững và bắt buộc trên production.
- Elasticsearch khi bật Search V3.
- Tài khoản/ứng dụng OAuth Google hoặc Microsoft nếu cần kết nối nguồn thật.

## Chạy môi trường phát triển

### 1. Chuẩn bị cấu hình

Sao chép mẫu cấu hình và điền các giá trị phù hợp với môi trường cục bộ:

```bash
cp .env.example apps/api/.env
```

Không commit secret, access token, refresh token hoặc credential production vào Git.

### 2. Chạy API

Từ thư mục gốc của repository:

```bash
make api
```

Lệnh này gọi `scripts/dev-api.sh`, tự tạo hoặc sửa virtual environment tại `apps/api/.venv`, cài dependency khi `requirements.txt` thay đổi, chạy migration và mở API tại `http://127.0.0.1:8000`.

Có thể truyền trực tiếp tùy chọn cho Uvicorn bằng:

```bash
bash scripts/dev-api.sh
```

### 3. Chạy giao diện web

Trong terminal khác:

```bash
make client
```

Lệnh này cài dependency bằng npm và khởi động Vite dev server.

## Cơ sở dữ liệu và migration

Áp dụng migration mới nhất:

```bash
cd apps/api
.venv/bin/python -m alembic upgrade head
```

Khởi tạo hoặc cập nhật các tag hệ thống `public` và `draft` theo cách idempotent:

```bash
cd apps/api
.venv/bin/python -m app.operations.tag_cli seed-system-tags
```

Production không tự thay đổi schema khi API khởi động. Migration phải được chạy trong quy trình release; ứng dụng sẽ từ chối khởi động nếu cơ sở dữ liệu không kết nối được, dùng SQLite hoặc không ở đúng Alembic head duy nhất.

## Cấu hình

Hai tệp mẫu là nguồn tham chiếu cho các biến môi trường:

- `.env.example`: cấu hình phát triển và danh sách tính năng có thể bật.
- `deploy/production.env.example`: cấu hình production dành cho service native.

Các nhóm cấu hình chính gồm:

- URL ứng dụng, CORS, trusted host và reverse proxy.
- PostgreSQL, connection pool và migration.
- Google OAuth/Drive và Microsoft OAuth/SharePoint.
- Đồng bộ nguồn, pipeline, worker và retry policy.
- Managed storage, retention và thumbnail/projection.
- Nhà cung cấp AI như Gemini hoặc OpenAI.
- Search V3 và Elasticsearch.
- Video analysis, video indexing và giới hạn xử lý.
- Inventory scheduler, Inventory worker và Google Sheet.
- Sao lưu cơ sở dữ liệu lên Google Drive bằng credential được quản lý.

Chỉ bật `PROXY_HEADERS_ENABLED` khi ứng dụng thật sự đứng sau reverse proxy tin cậy; cấu hình `PROXY_TRUSTED_IPS` bằng IP hoặc CIDR cụ thể.

## Kết nối Google Drive

1. Tạo hoặc chọn project trên Google Cloud Console.
2. Bật Google Drive API và cấu hình OAuth consent screen.
3. Tạo OAuth client loại **Web application**.
4. Thêm redirect URI cho local:

   ```text
   http://localhost:8000/api/auth/google/callback
   ```

5. Khai báo `GOOGLE_CLIENT_ID` và `GOOGLE_CLIENT_SECRET` trong `apps/api/.env`.
6. Khởi động lại API và đăng nhập Google từ giao diện.

Ứng dụng yêu cầu các scope cần thiết để nhận danh tính người dùng và truy cập Drive. Với triển khai công khai, cần hoàn tất quy trình xác minh OAuth tương ứng của Google.

## Kết nối SharePoint

1. Tạo **App registration** trong Microsoft Entra.
2. Thêm Web redirect URI:

   ```text
   http://localhost:8000/api/auth/microsoft/callback
   ```

3. Thêm delegated permissions `User.Read`, `Sites.Read.All` và `Files.Read.All`.
4. Cấp admin consent nếu chính sách tenant yêu cầu.
5. Khai báo `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET` và `MICROSOFT_TENANT_ID`.
6. Có thể giới hạn một site bằng `SHAREPOINT_SITE_HOSTNAME` và `SHAREPOINT_SITE_PATH`.
7. Khởi động lại API và chọn **Connect SharePoint**.

Phiên Google Drive và SharePoint độc lập; đổi provider không làm mất phiên của provider còn lại.

## Kiểm thử và kiểm tra chất lượng

API:

```bash
cd apps/api
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q app
```

Client:

```bash
cd apps/client
npm test
npm run typecheck
npm run build
```

Kiểm thử tích hợp từ thư mục gốc:

```bash
make integration-test
```

Khi thay đổi script shell, chạy thêm `bash -n <đường-dẫn-script>` trước khi commit.

## Health probe

- `GET /live`: kiểm tra tiến trình API còn hoạt động, không truy cập dependency.
- `GET /ready`: kiểm tra PostgreSQL và Elasticsearch khi tính năng liên quan được bật.
- `GET /version`: trả về `APP_VERSION` và `BUILD_COMMIT` đã được xác thực.

## Triển khai production trên VPS

Production dùng Nginx cho frontend, systemd cho API/worker và Docker Compose chỉ cho Elasticsearch. Hai luồng deploy frontend và backend độc lập:

```bash
cd /srv/creative-asset-manager-source
git pull --ff-only
sudo scripts/deploy-cam-frontend.sh
sudo scripts/cam-rebuild-backend.sh
```

Các script tạo release bất biến, chuyển symlink nguyên tử và hỗ trợ rollback:

```bash
sudo scripts/deploy-cam-frontend.sh --rollback
sudo scripts/cam-rebuild-backend.sh --rollback
```

Backend deploy kiểm tra cấu hình, xác nhận chỉ có một Alembic head, chạy migration, khởi động lại API cùng worker ảnh/video và thực hiện dọn dẹp disk trước/sau deploy. Rollback backend không tự downgrade schema.

Xem quy trình đầy đủ tại [`docs/operations/VPS_DEPLOYMENT.md`](docs/operations/VPS_DEPLOYMENT.md).

## Tìm kiếm, pipeline và AI Operations

- Tìm kiếm trên giao diện sử dụng Search V3; các đường tìm kiếm Explorer cũ đã được loại bỏ.
- Rebuild search phải đi qua quy trình quản trị và chuyển alias có kiểm soát.
- AI Operations là nơi theo dõi Image AI, Video AI, trạng thái các step, lỗi ổn định và thao tác retry/cancel.
- Worker ảnh và video có vai trò tách biệt nhưng dùng chung hàng đợi xử lý trong PostgreSQL.

Tài liệu liên quan:

- [`docs/operations/AI_OPERATIONS.md`](docs/operations/AI_OPERATIONS.md)
- [`docs/operations/AI_BATCH.md`](docs/operations/AI_BATCH.md)
- [`docs/operations/SEARCH_REBUILD.md`](docs/operations/SEARCH_REBUILD.md)
- [`docs/operations/SOURCE_SYNC_SCHEDULER.md`](docs/operations/SOURCE_SYNC_SCHEDULER.md)
- [`docs/operations/WORKER_RUNTIME.md`](docs/operations/WORKER_RUNTIME.md)

## Inventory

Inventory sử dụng scheduler và worker riêng để tạo snapshot, đọc dữ liệu Google Sheet, chuẩn hóa vật tư và thực hiện đối soát theo ngày làm việc của tenant. Mọi thao tác production cần tuân theo runbook và feature flag tương ứng.

- [`docs/operations/INVENTORY_PRODUCTION_ROLLOUT.md`](docs/operations/INVENTORY_PRODUCTION_ROLLOUT.md)
- [`docs/inventory-v4-production-runbook.md`](docs/inventory-v4-production-runbook.md)

## Sao lưu và bảo trì

- Sao lưu PostgreSQL: [`docs/operations/DATABASE_BACKUP.md`](docs/operations/DATABASE_BACKUP.md)
- Dọn dẹp managed storage: [`docs/operations/MANAGED_STORAGE_CLEANUP.md`](docs/operations/MANAGED_STORAGE_CLEANUP.md)
- Chính sách retention: [`docs/operations/RETENTION.md`](docs/operations/RETENTION.md)
- Lưu phiên OAuth an toàn: [`docs/operations/AUTH_PERSISTENCE.md`](docs/operations/AUTH_PERSISTENCE.md)

Không dùng OAuth của Source Drive thuộc tenant/người dùng cho backup production. Backup phải sử dụng credential Google Drive được quản lý riêng và chỉ xóa bản local sau khi bản remote đã được xác minh thành công.

## Nguyên tắc an toàn

- Không log hoặc commit secret và token.
- Luôn ràng buộc dữ liệu nghiệp vụ theo tenant và kiểm tra quyền trước khi mutation.
- Dùng route download/proxy do backend kiểm soát thay vì tin URL đầu vào tùy ý.
- Các thao tác retry, cancel, rebuild và retention phải có audit và idempotency phù hợp.
- Thực hiện rollout bằng feature flag/canary khi thay đổi pipeline, search hoặc worker.
- Không chạy Alembic downgrade tự động trên production.

## Tài liệu bổ sung

- Kiến trúc: [`docs/architecture`](docs/architecture)
- Runbook vận hành: [`docs/operations`](docs/operations)
- Quyết định kiến trúc: [`docs/adr`](docs/adr)
- Kế hoạch triển khai: [`docs/plans`](docs/plans)

Khi tài liệu mô tả trạng thái cũ mâu thuẫn với migration, code hoặc runbook production hiện hành, hãy ưu tiên code đang chạy và runbook vận hành mới nhất, đồng thời cập nhật lại tài liệu liên quan trong cùng thay đổi.
