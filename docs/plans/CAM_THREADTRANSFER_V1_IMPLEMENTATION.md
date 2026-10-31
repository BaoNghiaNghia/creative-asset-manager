# CAM ThreadTransfer Lite V1 — tối giản Stage 2 Slot 1

**Cập nhật:** 2026-10-31
**Phạm vi:** GatorHats Products / Stage 2 / `prod_s2_ec1` (Front Ecommerce).
**Trạng thái:** Lite V1 đã commit và deploy backend vào production (ff5a70658); 46/46 targeted tests đạt. Chưa kích hoạt Skill default và chưa có native end-to-end image smoke đạt; thao tác activate và chạy smoke dưới tài khoản creative-assets bị lớp an toàn của công cụ điều khiển VPS chặn. Đường v4 hiện tại vẫn là mặc định.
**Production đã đối chiếu:** release `23b3a505a` ngày 2026-10-31, source `main` cùng revision.

## 1. Điều đã có; không xây lại

- Stage 1 cung cấp **artwork gốc** và palette (nguồn duy nhất cho nội dung chữ, hình dáng, bố cục, màu).
- Slot 1 đã có **official 8869 front stock**, placement, pigment-only guide, edit mask và `build_slot1_material_pass()`.
- Có `CodexImageGenRunner.generate_from_references()` qua Codex CLI / built-in `$imagegen`; worker, queue, storage, retries, history, các downstream Slots 2–7 đã có.
- Có `finish_slot1_refinement()`: QA màu, geometry, thread texture/halo, contact band 2px và hard restore pixel stock ngoài mask.
- **Đã có trong backend production:** đường Lite mới chạy một `generate_from_references()` điều phối hai native ImageGen calls và kiểm chứng hash riêng của macro/final. **Chưa có smoke provider thật:** chưa được phép khẳng định Pass B đã edit thành công từ macro trên production. Job v4 cũ vẫn chỉ chạy một lượt.
- **Mask hiện là ảnh tham chiếu cho model**, không phải tham số native inpaint mask. Bảo toàn stock được thực thi ở backend sau khi tạo.

## 2. Quyết định mới: 1 phiên Codex, 2 lượt ImageGen tuần tự

```text
Stage 1 artwork + official stock + placement/mask hiện hữu
                         │
                ONE Codex exec / ONE compact Skill
                         │
         A. $imagegen → working/thread_macro.png
             (macro satin/tatami làm texture reference)
                         │
         B. $imagegen EDIT original guided stock
            input: target + macro + artwork + mask
                         │
         output/final.png → existing composite/QA
                         │
            Existing storage / output history
```

- **Chỉ 1 Codex session/job**, nhưng phải có **đúng 2 ImageGen calls**: một macro, một edit. Không tuyên bố một call ImageGen làm được hai ảnh.
- Pass B phải được gọi **sau** khi Pass A có file macro thật, đọc macro như ảnh tham chiếu; không chỉ nhắc đến macro trong prompt. Nếu không có native image-edit, fail rõ ràng, không tạo mũ từ text.
- Chỉ 1 candidate cho mỗi pass. **Không thử 3 phiên bản, không vòng review tự động, không text LLM planner/scout trong Stage 2**, không gọi API key trả phí dự phòng.
- Macro nằm trong **temporary Codex workspace**, được xóa cùng job sau khi backend nhận và kiểm tra final; **không upload Drive**, không làm bảng cache hoặc lịch sử macro riêng.
- **Giai đoạn đầu dùng nguyên stock đã có và guide hiện tại**; chưa crop ROI / resize / map lại tọa độ vì làm tăng điểm lỗi. Nếu đo thực tế cho thấy full-image là nút thắt, mới thêm crop có QA đối chiếu pixel.
- Dùng **Skill mới, pin theo job** (ví dụ `gatorhats-8869-slot1-threadtransfer-lite-v1`) làm công tắc bật/tắt: job v4 cũ giữ nguyên; chọn lại v4 là rollback cho **job mới**. **Không thêm env feature flag**, không thay Skill v4 đã pin, không sửa Stage 1 hay Slots 2–7.
- Không rút bỏ existing backend preservation/QA để tiết kiệm vài mili-giây: đó là điều kiện không được hy sinh.

### Giảm token/code so với plan 2026-10-30

| Loại bỏ | Thay thế |
|---|---|
| Hai Codex CLI executions + hai lần nạp Skill/context | Một `codex exec`, hai `$imagegen` calls trong cùng phiên |
| Prompt v2/v3/v4 + bản giải thích trùng nhau | Một Skill tối đa ~180 từ, chỉ hai lệnh A/B; prompt job ngắn chỉ nhắc role files |
| Module orchestration `slot1_thread_transfer.py` | Nhánh nhỏ trong `ecommerce_pipeline.py`; runner giữ nguyên API |
| ROI crop / paste mapping giai đoạn đầu | Dùng pigment guide/mask và QA hiện có |
| Upload macro lên Drive, cache/table mới | `working/thread_macro.png` tạm, cleanup sẵn có |
| Feature flag mới / dashboard / metrics system / multiple retries | Chọn Skill theo job, dùng logging/retry hiện có; không tăng số ImageGen calls |
| Inpaint tests trên 10–20 mẫu và A/B nhiều màu trước MVP | Một output thật có bằng chứng hai calls; sau đó mới mở rộng |

**Chi phí:** giảm overhead Codex text/context và bớt I/O trung gian; **không** mặc định giảm chi phí tạo ảnh so với v4 một lượt (2 ImageGen calls có thể tốn hơn). Đo số call, token/duration thực tế bằng log sẵn có; không tự đặt mức tiết kiệm %.

## 3. Skill và prompt ngắn (implementation contract)

**Role inputs của job:** `person` = guided original stock; `design_reference` = exact Stage 1 art; `unaltered_stock_reference` = official cap; `slot1_edit_mask` = protected edit area. Không truyền thêm Pinterest/UGC/brief/preview không liên quan.

**Một Skill ngắn, không prompt stacking:**

```text
Use built-in $imagegen in this same Codex session, sequentially.
A — Edit design_reference into one photoreal close-up of low-profile
machine embroidery on neutral twill: fine satin/fill yarn, visible
fibers and tiny stitch-foot compression. Keep exact glyphs and pigments.
Save the native PNG to working/thread_macro.png.
B — After viewing working/thread_macro.png, use $imagegen IMAGE-EDIT
on person (the ORIGINAL guided stock target). Use the macro for
thread material ONLY; use design_reference for exact text/colors,
unaltered_stock_reference for the real cap, and slot1_edit_mask as a
protected-area reference. Subtle crown-conforming highlights/contact;
no new hat, no patch, no embossed/plastic/printed thread.
Save ONE native final PNG to output/final.png.
No other image calls, no Python re-render, no API-key fallback.
If either image-edit operation is unavailable, stop with an error.
```

**Job prompt:** `Execute Stage 2 Slot 1 two-pass Skill. Preserve the exact design, original cap and protected mask. Produce working/thread_macro.png and output/final.png only.`

Không tự mở rộng prompt ở `ecommerce_pipeline.py` với `VIEW_PROMPTS` + `embroidery_prompt_for_product_slot` + lệnh v4 thêm lần nữa cho **Lite job**. Các job v4 cũ vẫn dùng prompt hiện tại.

## 4. Chỉ các thay đổi code tối thiểu để chạy thật

1. **Thêm 1 Skill mới** (nội dung ở trên); cài vào Codex HOME theo quy trình đồng bộ Skill hiện hữu. Không sửa SHA Skill v4.
2. **`ecommerce_pipeline.py`**: cho phép pin/Regenerate với tên Skill mới; nhánh `prod_s2_ec1` này chọn prompt ngắn + material/contact mask đã có; gọi `generate_from_references()` **một lần**. Lưu metadata `slot1_embroidery_pipeline.version = threadtransfer-lite-v1` đúng tên (không tiếp tục ghi `ai-material-localized-v3` cho Lite).
3. **Xác nhận bằng file và native tool evidence trước cleanup**: `working/thread_macro.png` và `output/final.png` là PNG hợp lệ, riêng biệt; hai lượt native `$imagegen` thực tế, không phải Pillow/mock/ảnh copy. Runner phải trả về final; sau đó áp `finish_slot1_refinement()` cũ, lưu phiên bản và cleanup như hiện tại.
4. **Tests tối thiểu**: Skill v4 unaffected, 2-call/macro missing → fail, no-op/wrong-color/outside-mask → fail, 1 successful mock + 1 **real provider smoke**. Không cần migration, queue, frontend build nếu không đổi UI.

### Hai điều kiện không được lược bỏ

- **Native image provenance:** hiện chỉ có file output không chứng minh đã thực hiện 2 ImageGen calls. Nếu tool events/native hashes không xác minh được, báo `unverified`, không gọi `completed` giả.
- **Source fidelity:** text/màu chuẩn, mọi pixel ngoài mask đúng stock, không có patch rectangle, foam, emboss hoặc chất liệu in phẳng. Reuse QA đã có và kiểm tra bằng mắt ảnh đầu tiên.

## 5. Đường thực hiện ngắn nhất

1. Triển khai Skill Lite + nhánh Stage 2 nhỏ + kiểm chứng output A/B và metadata; chạy targeted tests, không đổi database/UI.
2. Dùng **một job Stage 1 đã chọn** để smoke qua Codex/ImageGen thật; xem macro, final và hình gốc. Nếu đạt, pin Skill Lite làm lựa chọn mặc định cho **job Slot 1 mới**; nếu lỗi, giữ v4 hiện tại, không xuất bản kết quả lỗi. Dọn workspace job an toàn.

**Kiểm tra thực tế ngày 2026-10-31:** backend `ff5a70658` deploy thành công, API + 4 image worker + video + visual đã đạt health. `13` Lite/pinning tests và `33` Slot1/Runner/Ecommerce regression tests đều đạt. Production `previous=23b3a505a` giữ rollback. Main có thêm wrapper `ec8ceaff7` để chạy `bash scripts/cam-activate-slot1-threadtransfer-lite.sh --apply|--rollback`. Lần `--check` cho biết registry Lite chưa được ghi (registration xảy ra trong `--apply`), tuy nhiên `--apply` và live Codex smoke ở tài khoản service bị chính sách an toàn của công cụ chặn. Không bypass/đổi account đặc quyền. **Không có output thật Lite nào đã được xác minh, không khẳng định hoàn tất.** Job v4/historical outputs vẫn nguyên trạng.

## Changelog

- 2026-10-30: kế hoạch Fast-Track cũ (2 sessions, module orchestration, flag, ROI).
- 2026-10-31: audit `main` + live release; chuyển sang Lite: **1 session / 2 image calls / 1 Skill / existing mask+QA / no new infrastructure**.
- 2026-10-31: `ff5a70658` triển khai backend + Skill Lite, 46 targeted tests đạt; wrapper kích hoạt `ec8ceaff7` trên `main`; default chưa được đổi do chặn quyền runtime. Temporary smoke files đã xóa.
