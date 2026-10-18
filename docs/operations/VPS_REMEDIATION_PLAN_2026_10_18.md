# VPS CAM — kế hoạch khắc phục có kiểm soát (18/10/2026)

## Phạm vi và bằng chứng hiện tại

Đây là kế hoạch xử lý production, **không** phải xác nhận tất cả vấn đề đã được sửa. Luôn kiểm tra lại số liệu ngay trước thay đổi cấu hình, xóa release hoặc deploy.

| Hạng mục | Kết quả đã quan sát | Hành động |
|---|---|---|
| Disk | 50 GB, ~79% đã dùng, ~9,9 GB trống | Giữ headroom cho migration/restore |
| RAM và swap | ~4,1 GiB available; 1,3/2 GiB swap đã dùng | Theo dõi, không swapoff |
| Journald | Đã giảm từ ~510 MB xuống ~196,5 MB; thu hồi 321,4 MB | Áp dụng giới hạn archived journal khi có lịch bảo trì |
| Scout Manager | Chỉ giữ 0.1.28, SHA-256 khớp; bỏ 18 file ~101,3 MiB | Hoàn tất |
| QA artifacts | Giữ 5 frontend/3 root runs; giữ baselines/proposals/smoke | Hoàn tất |
| Local preview | Đã đóng 4 Vite QA preview bị bỏ quên | Hoàn tất |
| API/DB/search | Nginx valid, API live/ready, PostgreSQL, Elasticsearch healthy | Không gián đoạn |
| Backup | Timer active; service gần nhất 16/10 thành công | **Chưa có restore rehearsal được kiểm chứng** |
| Network | eth0 routable (configuring), IPv4 default route OK; wait-online failed | Xác minh gateway IPv6 với provider |
| IPv6 | Public IPv6 hiện diện, không có IPv6 default route | Không suy đoán cấu hình route |
| Docker | Elasticsearch volume ~3,78 GB; postgres:16.4 image ~617 MB không chạy | Giữ nguyên |
| Production release | Backend quan sát được là 848f7322; CI bản 20351cd9 xanh | **Chưa deploy vì thiếu backup restore gate** |

## P0 — Chứng minh backup có thể phục hồi (ưu tiên chặn phát hành)

1. **Operator có quyền riêng**, không phải CodeLocal vượt sandbox, xác minh identity PostgreSQL, hệ thống backup, lịch service và bản backup thực sự tồn tại. `systemctl show creative-asset-manager-db-backup.service -p Result -p ExecMainStatus` chỉ là bằng chứng service success, không phải restore success.
2. Tại runtime được ủy quyền, chạy `python -m app.operations.database_backup_cli verify-config` rồi `... list`. Đối chiếu remote backup ID, timestamp, kích thước, checksums và DB nguồn. Không in token hay nội dung production.env.
3. Tải file vào staging bảo vệ ngoài repository, xác minh SHA-256 và MD5, dùng `pg_restore --list` để kiểm tra định dạng. Đây chưa phải rehearsal.
4. **Restore vào một DB rỗng, cách ly, tên mới** (ví dụ `cam_restore_rehearsal_20261018`); trước đó xác nhận tên/URL khác tuyệt đối DB production, đủ disk và RAM; dùng `pg_restore --no-owner --no-acl --exit-on-error`. Tuyệt đối không `--clean` trên production.
5. Kiểm chứng cấu trúc bảng, revision Alembic, bảng mẫu và constraints. Lưu chứng cứ restore riêng (checksum, DB nguồn/đích ẩn danh, thời gian, kết quả, người thực hiện); chỉ sau đó mới dọn DB tạm với operator được phép.

**Gate PASS:** cùng một artifact được xác thực checksum và restore thành công vào DB độc lập. Nếu chưa đạt, **không chạy migration hoặc deploy production**.

## P1 — Sửa gốc networkd-wait-online, bảo toàn SSH

1. Đã bổ sung công cụ chỉ đọc `python3 scripts/cam-vps-post-reboot-repair.py --network-check`. Nó phân loại IPv4 route/eth0/IPv6/wait-online và luôn báo `NETWORK_CHANGE_APPLIED=NO`.
2. Đã tái hiện `DEGRADED_REVIEW_REQUIRED`: IPv4 qua eth0 có sẵn; `eth0` vẫn configuring; thiếu IPv6 default route trong khi có IPv6 global. Đây là **dấu hiệu** cấu hình IPv6 thiếu hoàn chỉnh, chưa chứng minh gateway đúng.
3. Đề nghị nhà cung cấp VPS xác nhận prefix IPv6, gateway `2401:3660::1`, yêu cầu on-link nếu gateway off-prefix, và cấu hình DHCPv6/RA áp dụng cho NIC.
4. Chỉ operator từ **provider recovery console** mới backup Netplan và thử cấu hình với `netplan generate` → `netplan try --timeout 120`; giữ IPv4, SSH port 6709, HTTPS, DNS và phương án rollback. Không `netplan apply` qua SSH không có console.
5. Sau thay đổi: kiểm chứng `ip -4 route show default`, `ip -6 route show default`, `networkctl status eth0`, DNS, `/live`, `/ready`, và wait-online ở boot an toàn; chỉ sau đó xem xét `systemctl reset-failed` để dọn trạng thái cũ.

**Gate PASS:** eth0 hoàn tất setup, IPv4 không bị mất, và lỗi wait-online không tái phát. Không mask/disable wait-online hoặc đặt `optional: true` chỉ để giấu lỗi.

## P1 — Kiểm kê release backend/frontend và bảo vệ rollback

Operator với quyền filesystem production phải thống kê size/mtime của `/opt/creative-asset-manager/releases` và `/var/www/creative-asset-manager/releases`, symlink `current`/`previous`, deploy lock và release đang được process systemd sử dụng.
Không xóa bất cứ `current`, `previous`, release đang chạy, target dự kiến deploy, artifact backup nào. Dọn release backend chỉ qua retention logic đã được kiểm thử của `scripts/cam-rebuild-backend.sh`; không `rm -rf` thủ công. Frontend current/previous phải bảo toàn hash và rollback.
CodeLocal hiện chỉ có quyền workspace; đọc/xóa filesystem production bằng script trung gian để lách quyền **không được phép**.

## P2 — Giới hạn log, cache và tài nguyên

- Đã vệ sinh journal cũ ~321,4 MB, Scout cũ ~101,3 MiB, QA tạm khoảng 26 MB; đã dừng 4 Vite preview.
- Sửa `scripts/cam-qa-disk-audit.sh`: `--plan-safe` chỉ in kế hoạch, `--clean-safe` tiếp tục `apt-get clean`, vacuum archived journal quá 14 ngày **và** giới hạn archived journal 256 MiB. Mặc định vẫn chỉ audit; không prune Docker/volume/production releases.
- `journalctl --vacuum-size` chỉ tác động archived files; active logs có thể khiến tổng vượt cap. Nếu cần độ bền, operator đánh giá `SystemMaxUse` bằng drop-in journald và kiểm thử riêng, không auto-restart journald.
- Giữ image postgres:16.4 cho integration, Elasticsearch volume ~3,78 GB và `.codelocal/pod-audit` ~325 MB cho đến khi xác minh owner/lifecycle. Không dọn `.venv`, `node_modules` đang dùng.
- Cảnh báo đề xuất: disk warning <8 GiB, critical <5 GiB; RAM available <2 GiB; journal >512 MiB; backup trễ quá lịch; chứng cứ restore chưa có.
- Cập nhật 8 gói Mesa trong cửa sổ bảo trì có Browser QA Chrome/Xvfb, không làm đồng thời với thay đổi mạng/database.

## Checklist và lệnh kiểm chứng

**Có thể chạy ngay trong quyền hiện hành, không mutate:**

```bash
python3 scripts/cam-vps-post-reboot-repair.py --network-check
bash scripts/cam-qa-disk-audit.sh --plan-safe
systemctl --failed --no-pager
systemctl list-timers creative-asset-manager-db-backup.timer --no-pager
systemctl show creative-asset-manager-db-backup.service -p Result -p ExecMainStatus
docker system df
df -h
free -h
curl -fsS https://creative-assets.ddns.net/live
curl -fsS https://creative-assets.ddns.net/ready
```

**Không tự thực hiện từ CodeLocal workspace:** đọc production secrets, download và restore backup, sửa Netplan, sửa systemd/journald, xóa release production. Các bước này yêu cầu operator console được cấp quyền và audit log.

| Gate | Trạng thái 18/10 |
|---|---|
| P2 journal/Scout/QA/preview cleanup | Hoàn tất, health đã kiểm tra |
| P1 network read-only diagnostic + regression | Hoàn tất, phát hiện degraded |
| P2 bounded journal housekeeping source | Đã code, đợi review/operator rollout |
| P0 backup checksum + isolated restore rehearsal | **Chưa xác minh** |
| P1 provider IPv6 answer + safe Netplan change | **Chưa hoàn tất** |
| P1 protected production release inventory | **Chưa đủ quyền** |
| Release Branding DNA | **Dừng ở safety gate, production hiện hữu giữ nguyên** |

Rollback: nếu operator thay đổi Netplan và mất mạng, dùng provider console khôi phục YAML cũ; nếu deploy frontend lỗi, dùng immutable `--rollback`; không downgrade migration production bị cấm và không dùng dữ liệu backup để ghi đè production khi chưa qua quy trình khôi phục riêng.
