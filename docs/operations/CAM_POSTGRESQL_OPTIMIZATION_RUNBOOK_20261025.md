# CAM PostgreSQL — Runbook tối ưu production v1.1

**Ngày cập nhật:** 25/10/2026 (Asia/Ho_Chi_Minh)
**Nhánh nguồn:** `main`
**Database:** `creative_asset_manager` / PostgreSQL 16
**Mục tiêu:** Giảm CPU/IO của claim job, điều chỉnh autovacuum và index, dọn dữ liệu đủ điều kiện, giữ nguyên tính đúng đắn của các pipeline.

## 1. Phạm vi mới và quyết định

Theo yêu cầu ngày 25/10/2026, **loại khỏi kế hoạch thực thi hai giai đoạn cũ: P1 (Backup/restore) và P4 (A/B hiệu năng)**. Không yêu cầu hoàn thành hai giai đoạn đó để tiếp tục P2, P3, P5, P6 hoặc P7.

- **Không** triển khai restore, tạo volume bổ sung hay phép thử so sánh A/B như một phần của runbook tối ưu này.
- **Có** kiểm tra dung lượng, SQL chỉ đọc, test tính đúng đắn, đo số liệu vận hành trước/sau khi kích hoạt và rollback khi có lỗi.
- **Không** sửa hoặc vượt qua các cơ chế backup/retention/authorization vốn có của ứng dụng. Nếu nghiệp vụ xóa bị ứng dụng chặn, giữ nguyên dữ liệu và chuyển sang công việc không phá hủy.
- Chỉ cập nhật kế hoạch trong lượt này; không áp dụng DDL, DML, chỉnh worker, cập nhật production hoặc giải phóng hold.

**Mã bước giữ nguyên** để bảo toàn khả năng đối chiếu các checkpoint và commit cũ: P0 → P2 → P3 → P5 → P6 → P7. Lịch sử runbook v1.0 vẫn có thể truy xuất trong Git.

## 2. Baseline đã xác nhận (P0)

| Nhóm | Quan sát |
|---|---|
| Toàn database | Khoảng 4.04 GB ở lần đo mới; 195 bảng, 704 index ở lần kiểm kê ban đầu |
| `processing_jobs` | Khoảng 1.86 GB, trong đó index khoảng 1.31 GB |
| Dữ liệu cần bảo vệ | 117.026 job `search_index_sync`/`visual_index_sync` đang giữ lịch tới 01/01/2099, đã đối soát đủ ID |
| Nhóm đến hạn nhưng bị policy chặn | 6.057 job `video_analyze`; không được tự bật lại provider |
| Nhóm cần kiểm tra backpressure | 3.352 job `asset_store`; chưa xác nhận toàn bộ có thể claim |
| Autovacuum | Đã bật; default vacuum scale factor 0.20; `processing_jobs` ~66–67 nghìn dead tuples, dưới ngưỡng mặc định ~175 nghìn ở lần đo |
| Truy vấn claim | Có các nhánh JOIN policy và Sort theo priority/fairness; `FOR UPDATE SKIP LOCKED` phải được giữ nguyên |
| Dung lượng ổ production | Khoảng 9.1 GiB trống tại checkpoint trước; cần đo lại trước từng thao tác |

**Giới hạn của số liệu:** `pg_stat_database` và `pg_stat_user_indexes` là bộ đếm tích lũy; `n_live_tup`/`n_dead_tup` là ước lượng. Không kết luận bảng trống hoặc index thừa chỉ dựa trên những thống kê này.

### P0 — Thao tác lặp lại

1. Ghi nhận commit `main`, DB size, filesystem free bytes, phiên đang chờ khóa, dead tuples và hoạt động vacuum.
2. Chạy script read-only `scripts/cam-pg-claim-baseline-readonly.sh` để lấy 3–5 mẫu khi thực sự cần so sánh xu hướng.
3. Đối soát nhóm hold: tổng số, liên kết ID, lịch `2099-01-01`; không sửa `next_attempt_at`.
4. Lưu mẫu theo ngày giờ, không đưa secret, tenant ID, row payload hoặc dữ liệu khách hàng vào log.
5. Cập nhật số liệu và trạng thái công việc vào mục nhật ký cuối tài liệu.

## 3. Cổng an toàn còn áp dụng

| Cổng | Yêu cầu tối thiểu | Khi không đạt |
|---|---|---|
| **S0 — Baseline** | Có mẫu thống kê mới và kiểm tra trạng thái API/worker | Chỉ tiếp tục đọc/đối chiếu |
| **S1 — Dung lượng & tải** | Còn ≥8 GiB trống, đủ headroom riêng cho WAL/index; không có lock wait kéo dài | Không chạy bảo trì nặng |
| **S2 — Tính đúng đắn** | Unit/integration tests và test PostgreSQL 2 session cho fairness, policy, `SKIP LOCKED`, không rerun completed | Không kích hoạt code claim mới |
| **S3 — Canary & rollback** | Thay đổi có thể đảo ngược, quan sát 15–30 phút và `/live` / `/ready` PASS | Tắt cờ hoặc revert bản phát hành; không tự sửa lịch sử pipeline |
| **S4 — Retention** | Chạy dry-run, kiểm tra FK/idempotency/asset-storage links; hệ thống nghiệp vụ cho phép xóa | Không xóa; không bypass guard mặc định |

Các cổng **không yêu cầu một giai đoạn backup/restore riêng hoặc một đợt A/B**. Các ràng buộc bảo toàn dữ liệu trong source vẫn giữ nguyên; xóa dữ liệu quan trọng không được phép nếu chưa có bằng chứng hợp lệ theo chính sách ứng dụng.

## 4. Danh sách giai đoạn còn hiệu lực

| Giai đoạn | Việc cần làm | Trạng thái |
|---|---|---|
| **P0 — Baseline** | Kiểm kê bảng, index, lock, vacuum và đối soát hold | **Đã có baseline; lấy lại mẫu trước khi sửa** |
| **P2 — Autovacuum** | Tối ưu ngưỡng cho bảng có churn lớn; xem xét VACUUM/ANALYZE bình thường | **Sẵn sàng triển khai khảo sát/canary** |
| **P3 — Claim & policy** | Lọc sớm nhóm bị policy chặn, tối ưu claim; giữ fairness/priority/`SKIP LOCKED` | **Đã xác định điểm nóng; cần test chức năng trước kích hoạt** |
| **P5 — Index** | Phân loại index lớn, trùng/không hiệu quả, đánh giá bloat thật | **Chưa thay đổi index** |
| **P6 — Dọn dữ liệu** | Dọn retention được phép, log hết hạn, đối soát incident/Storage/AI | **Chỉ dry-run, xóa phải qua guard ứng dụng** |
| **P7 — Lưu trữ dài hạn** | Thiết kế partition/archival, không phá FK/unique/idempotency | **Thiết kế, chưa migration** |

### P2 — Tối ưu autovacuum

**Mục đích:** Chống tích tụ dead tuples trong `processing_jobs`, giảm chi phí đọc heap/index.

1. Đo `n_live_tup`, `n_dead_tup`, `n_mod_since_analyze`, `last_autovacuum`, `last_autoanalyze`, `relfrozenxid` và hoạt động IO/CPU tại thời điểm xử lý.
2. Tính ngưỡng autovacuum thực tế: `autovacuum_vacuum_threshold + autovacuum_vacuum_scale_factor * reltuples`. Đối chiếu bảng có churn cao, tránh thay đổi ngưỡng toàn cluster.
3. Thử một thay đổi reloptions nhỏ, có thể đảo ngược, trên **một bảng một lần** nếu có bằng chứng rõ ràng. Ví dụ để xem xét: `processing_jobs` vacuum scale factor 0.08 và analyze scale factor 0.03; đây **không phải cấu hình đã áp dụng**.
4. Giám sát autovacuum frequency/duration, IO, CPU, lock, dead tuples trong 15–30 phút và theo chu kỳ dài hơn. Nếu regression, reset reloptions theo change log.
5. Chỉ xem xét `VACUUM (ANALYZE)` thường khi phù hợp tải; **không dùng `VACUUM FULL`** cho production đang phục vụ.

**Đạt:** tần suất vacuum phù hợp hơn, dead tuples không tăng kéo dài và không làm worker/API chậm đi.

### P3 — Tối ưu claim và policy

1. Dùng query thực từ `TenantAwareJobClaimer` và script `scripts/cam_pg_claim_plan_readonly.py` để xem kế hoạch SQL mà không thực hiện claim.
2. Ưu tiên tránh quét vô ích các nhóm bị chặn ở mức tenant/job type/provider. Xem xét backoff khi một nhóm không đủ policy/quota, nhưng phải cập nhật khi policy/quota thay đổi.
3. **Không release** 117.026 job hold; **không** tự bật provider Video bị disabled; không tự thay đổi quyết định Managed Storage backpressure.
4. Giữ thứ tự ưu tiên, fairness, policy checks, limit theo tenant/provider, quota được kiểm tra tại lúc claim, và `FOR UPDATE SKIP LOCKED`.
5. Dùng test unit/integration, test đồng thời hai session trên môi trường thích hợp, xác minh không claim trùng và không thay đổi kết quả nghiệp vụ. Không cần một giai đoạn so sánh hiệu năng A/B riêng.
6. Triển khai nhỏ/có feature flag khi các cổng S0–S3 đạt; theo dõi thời gian query, CPU, buffer, lock, pending/due counts. Rollback ngay nếu thiếu công bằng hoặc rerun job hoàn tất.

**Đạt:** giảm scan/poll vô ích mà vẫn bảo toàn kết quả claim hợp lệ.

### P5 — Rà soát index

1. Tổng hợp `pg_relation_size`, `pg_indexes_size`, `idx_scan`, `idx_tup_read`, danh sách index và định nghĩa index theo từng bảng.
2. Rà soát 4 index lớn trên `processing_jobs`; kiểm tra constraint/FK/unique, workload và thời điểm reset stats trước khi cân nhắc index trùng.
3. Không xóa index chỉ vì `idx_scan=0`. Đo bloat bằng phương pháp thích hợp trước khi cân nhắc rebuild.
4. Chỉ lên lịch `REINDEX CONCURRENTLY`/`CREATE INDEX CONCURRENTLY` sau khi xác minh đủ dung lượng cho index mới và WAL, tải thấp, có điểm rollback và cổng S1–S3 đạt.
5. Không sử dụng `REINDEX` chặn bảng hoặc đổi nhiều index đồng thời.

**Đạt:** giảm lượng index/bloat được chứng minh là không hiệu quả, không làm chậm truy vấn quan trọng.

### P6 — Dọn dữ liệu và retention

1. Chạy dry-run bằng nghiệp vụ retention sẵn có, phân loại `application_logs` theo `expires_at` thực tế; không xóa khi không có bản ghi hợp lệ để xóa.
2. Bảo vệ `cam_incident_20261024_job_hold`, `cam_incident_20261024_source_reuse` và các khóa liên kết đến pipeline/asset-storage.
3. Với completed jobs, giữ identity/idempotency; chỉ xem xét tối ưu payload theo nghiệp vụ retention hiện có và khả năng khôi phục do ứng dụng yêu cầu.
4. Kiểm tra FK, Inventory, AI outputs, `asset_storage_objects` và reuse trước khi xóa bất kỳ dữ liệu vật lý nào.
5. Nếu cơ chế retention nội bộ yêu cầu bằng chứng phục hồi mà chưa có, **không bypass**; ghi BLOCKED cho hành động xóa đó rồi tiếp tục P2/P3/P5 không phá hủy.

**Đạt:** chỉ xóa bản ghi quá hạn/đủ điều kiện có bằng chứng, không chạy lại AI, không upload lại, không mất pipeline.

### P7 — Thiết kế lưu trữ dài hạn

- Đánh giá partition/archive cho `processing_jobs` theo thời gian/trạng thái với lưu ý tenant key, unique keys và FK.
- Kiểm tra tác động tới claim fairness, idempotency, completion counts và API truy vấn history.
- Thiết kế data retention policy riêng cho AI analyses, logs và incident evidence.
- Không thực hiện migration/archival hàng loạt chỉ vì dự báo dung lượng.

## 5. Lệnh kiểm tra an toàn (read-only)

Chạy từ nhánh `main` của CAM theo quyền vận hành được cấp:

```bash
# Đo claim liên tiếp 5 mẫu, cách nhau 3 giây
bash scripts/cam-pg-claim-baseline-readonly.sh 5 3

# Xem planner SQLAlchemy, không thực thi claim
PYTHONPATH=apps/api apps/api/.venv/bin/python scripts/cam_pg_claim_plan_readonly.py --role image
PYTHONPATH=apps/api apps/api/.venv/bin/python scripts/cam_pg_claim_plan_readonly.py --role visual
```

SQL thống kê, chạy qua `psql` với quyền SELECT/`pg_stat_*`:

```sql
SET default_transaction_read_only=on;
SET statement_timeout='10s';
SET lock_timeout='2s';
SELECT relname,n_live_tup,n_dead_tup,n_mod_since_analyze,
       last_autovacuum,last_autoanalyze
FROM pg_stat_user_tables
WHERE relname IN ('processing_jobs','asset_ai_analyses','source_assets','asset_pipelines');
SELECT state,wait_event_type,wait_event,count(*)
FROM pg_stat_activity
WHERE datname=current_database()
GROUP BY 1,2,3 ORDER BY count(*) DESC;
SELECT indexrelname,idx_scan,idx_tup_read,
       pg_size_pretty(pg_relation_size(indexrelid))
FROM pg_stat_user_indexes
WHERE relname='processing_jobs'
ORDER BY pg_relation_size(indexrelid) DESC;
```

## 6. Quy trình kích hoạt và rollback

1. Ghi lại nguồn code, SQL/config sẽ đổi, tình trạng các dịch vụ và dữ liệu thống kê.
2. Chỉ thay **một** mục đã được kiểm chứng ở mỗi lần phát hành; không trộn dọn dữ liệu với thay claim.
3. Chạy các test bảo vệ hành vi, kiểm tra kết nối/khóa và khả năng rollback/disable feature.
4. Bật canary hoặc thực hiện bảo trì ngoài giờ cao điểm (khi phù hợp).
5. Theo dõi ít nhất 15–30 phút: CPU/IO, slow query, queue depth, stuck job, lock waits, `/live` và `/ready`.
6. Nếu lỗi: hoàn nguyên config/code đã đổi; ghi số liệu trước/sau, không khôi phục bừa trạng thái pipeline.

**Hard-stop:** không bao giờ tự rerun completed jobs, mở hold 2099, ghi stock, tự approve manager, bypass retention/backup guard trong code, xóa incident evidence, hoặc vượt quyền truy cập của môi trường.

## 7. Nhật ký và bước tiếp theo

- **25/10/2026, trước v1.1:** P0 đã kiểm kê 195 bảng/704 index; P3 đã có quan sát SQLAlchemy EXPLAIN read-only; 47/47 test policy/claim và 2 test planner PASS tại lần kiểm tra trước.
- **25/10/2026, v1.1:** Loại hai giai đoạn cũ theo yêu cầu, điều chỉnh điều kiện của P2/P3/P5/P6 và cổng an toàn. Các công cụ chẩn đoán hiện có không bị xóa khỏi repository. Không thay đổi DB, API, worker hoặc production.
- **Việc xử lý kế tiếp:** P2 — làm mới thống kê autovacuum và xác định reloptions riêng cho `processing_jobs`, sau đó P3 — tối ưu lọc policy theo bằng chứng và test tính đúng đắn. P5/P6/P7 xử lý sau theo cổng tương ứng.

**Trạng thái:** Tài liệu được cập nhật; các thay đổi PostgreSQL theo v1.1 **chưa kích hoạt**.
