# CAM ThreadTransfer Lite V1 — tối giản Stage 2 Slot 1

**Cập nhật:** 2026-10-31
**Phạm vi:** GatorHats Products / Stage 2 / `prod_s2_ec1` (Front Ecommerce).
**Trạng thái:** ĐÃ DEPLOY VÀ KÍCH HOẠT LITE V1 CHO JOB SLOT 1 MỚI. Backend commit `b86676ded`, 50/50 targeted tests đạt; một job thật Stage 1 (`design_05.png`) hoàn tất với `threadtransfer-lite-v1`, native_passes=2, output_version=1 và QA backend đạt. Skill v4 vẫn có thể khôi phục làm default mà không thay đổi các job cũ. Chưa xác nhận nghiệm thu thẩm mỹ trực quan của output.
**Production đã đối chiếu:** backend release `b86676ded` ngày 2026-10-31, rollback backend `be01ab549`; default Slot 1 là `gatorhats-8869-slot1-threadtransfer-lite-v1`. Source `main` đã push cùng code triển khai.

## 1. Điều đã có; không xây lại

- Stage 1 cung cấp **artwork gốc** và palette (nguồn duy nhất cho nội dung chữ, hình dáng, bố cục, màu).
- Slot 1 đã có **official 8869 front stock**, placement, pigment-only guide, edit mask và `build_slot1_material_pass()`.
- Có `CodexImageGenRunner.generate_from_references()` qua Codex CLI / built-in `$imagegen`; worker, queue, storage, retries, history, các downstream Slots 2–7 đã có.
- Có `finish_slot1_refinement()`: QA màu, geometry, thread texture/halo, contact band 2px và hard restore pixel stock ngoài mask.
- **Đã xác minh qua production pilot:** đường Lite dùng một `generate_from_references()` điều phối hai native ImageGen calls, kiểm chứng hash macro/final riêng biệt trước cleanup, sau đó composite/QA bảo vệ stock và ghi output version. Job v4 cũ vẫn pin v4 và không bị sửa.
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

**Kiểm tra thực tế ngày 2026-10-31:** sửa discovery/registry bằng `dc72760aa`; xử lý ràng buộc layout và mã QA bằng `be01ab549`; xử lý halo trung gian của Lite bằng `b86676ded`. Production smoke dùng một source thật `design_05.png`, job `0035f3bb-15aa-457c-8559-492d222dfbe4`. Hai lần thử trước lần lượt gặp lỗi QA tổng quát và `ecommerce_slot1_qa_emboss_halo`; lần thử thứ ba hoàn tất (`completed`, output_version=1, `threadtransfer-lite-v1`, `native_passes=2`, attempt=1/1). Đã bật lại Lite làm default sau khi job thành công, xác nhận qua `--check`. Test release `50 passed`; API, image/video/visual healthy; disk 86% used và backend previous=`be01ab549`. **Hạn chế còn lại:** QA backend không đồng nghĩa đã nghiệm thu trực quan ảnh cuối. Operator có thể xem output trong Stage 2 và dùng nút Pick/Regenerate khi cần.

## Changelog

- 2026-10-30: kế hoạch Fast-Track cũ (2 sessions, module orchestration, flag, ROI).
- 2026-10-31: audit `main` + live release; chuyển sang Lite: **1 session / 2 image calls / 1 Skill / existing mask+QA / no new infrastructure**.
- 2026-10-31: `ff5a70658` triển khai backend + Skill Lite; `ec8ceaff7` thêm wrapper kích hoạt. `dc72760aa` sửa phát hiện Skill cho tenant cũ. `be01ab549` tăng fidelity prompt/mã QA. `b86676ded` sửa hậu xử lý halo, thêm test. 50/50 tests đạt; production pilot hoàn tất; Lite đã kích hoạt; các temporary smoke files đã xóa.
## 2026-10-31 — Slot 1 photographic fiber improvement (V1.1)

Nguyên nhân người dùng báo output giống chữ thêu phồng hơn ảnh mẫu: Lite V1 đã chạy 2 ImageGen native nhưng Pass B chỉnh ảnh mũ toàn khung, khiến chữ chiếm tỷ lệ nhỏ trong khung 1024px. Hậu xử lý composite theo mask nguồn và QA texture chỉ dựa high-pass variance nên nhiễu ngẫu nhiên/gân to vẫn có thể bị coi là vật liệu chỉ thật. QA V1 trước đó chỉ chứng minh native provenance, không chứng minh thẩm mỹ.

- Chỉ với Lite: tạo **square-letterboxed source-crown ROI** từ ảnh official stock quanh front embroidery field; role `person` được crop nguyên vẹn, không render lại. Macro/stock-reference/mask vẫn là các role cũ; 2 native calls, 1 Codex session như V1. Crop output được map/paste lại khung original trước khi kiểm tra màu và khôi phục pixel ngoài mask.
- Tăng contact band Lite lên 3 px ở kích thước **stock gốc**, giảm nguy cơ viền thêu bị cắt sắc; giới hạn tối đa 8/255 độ tối và giữ nguyên hình vải/side logo. V4 và các Slot 2–7 không đổi.
- QA mới yêu cầu fiber-direction coherence (chặn noise giả chỉ) và tỷ lệ chi tiết sợi mảnh so với gân to (chặn coarse artificial puff), kèm mã lỗi riêng. Bảo toàn glyph alpha, màu, stock ngoài mask và native hashes như trước.
- Cập nhật Skill lên 1.1.0 (giữ tên Skill Lite V1 để job/provenance vẫn tương thích), metadata Output History có `material_revision=roi-fiber-v1.1` và tọa độ ROI. Không tác động file ảnh đã hoàn thành, không tự regenerate hàng loạt.
- Test cục bộ targeted/regression: **79 passed** trước khi deploy; cần kiểm tra hình thực tế trên output mới sau khi production cập nhật. QA tự động chưa thay thế nghiệm thu thị giác người thật, và stock gốc không được thay đổi ánh sáng/bố cục theo ảnh benchmark.
- Production verification for V1.1: deployment `da22a7e08` completed and API/4 image workers healthy; Codex skill filesystem version is 1.1.0. Since a registry row can remain pinned to 1.0.0 after deploy, Slot 1 Skill selection now performs one targeted, transaction-scoped registry update when the installed bundle digest differs. It does not create an extra image job or commit an Ecommerce row lock. The next explicit Regenerate/Retry will automatically pin the new installed Skill revision. Existing job history and completed assets stay unchanged.


## 2026-11-01 — ThreadTransfer Lite V1.3: original-stock photo inpaint

**Cause identified from the user's before/after examples:** Existing Lite V1.2
performed two verified native image edits, but in Pass B the edit TARGET was
already a flat pigment composited over the cap. The model was asked to convert
paint into thread; this can create inflated emboss/rubber highlights. The guide
was also hard-clipped to the canonical silhouette at save time, leaving the
physical stitch foot/cloth interaction underrepresented. A native ImageGen
reference-mask image is not a provider-enforced inpaint mask.

**Scoped remedy (Slot 1 Lite only):**

1. Pass A remains a native edit of original artwork into a photographed
   embroidery material macro on neutral twill. Its native SHA is verified.
2. Pass B edits the **original unprinted cap crown ROI** as `person` instead
   of the pigment-painted cap. A newly separated, identically cropped
   `slot1_layout_guide` reference carries the immutable Stage 1 text, palette
   and placement. The same `slot1_edit_mask` reference still indicates the
   intended stitch/contact pixels. Native ImageGen is instructed to create
   stitches DIRECTLY on actual twill, guided by the macro texture.
3. Exact canvas dimensions are declared on each input (the old ROI reference
   metadata could advertise full-cap dimensions for a smaller edit crop).
4. Backend verifies major letter components against intended colored geometry
   to reject partially missing/displaced lettering, then composites AI fibers
   within the original canonical alpha with a maximum 3px low-contrast
   stitch-foot contact band. Original pixels outside the edit region are copied
   unchanged. No fake synthesized yarn textures are used.
5. Output History identifies `material_revision=roi-original-stock-inpaint-v1.3`,
   `edit_target=original_unprinted_stock`, and the exact canvas/ROI values.
   Legacy v4, other Slots and previously stored outputs are unchanged. The
   existing default Lite Skill auto-refresh on explicit Retry/Regenerate
   repins the new version and SHA; queued old jobs keep their original pin.

**Limitations / acceptance:** `slot1_edit_mask` is a photo reference for the
native model, not a true API-level inpainting mask. Stock-pixel protection is
enforced by the deterministic postprocessor. Unit/integration tests verify
geometry, color, per-glyph coverage, authenticity-of-native-file hashes and
legacy compatibility; these checks do **not** prove the final embroidery
matches the user's sample photograph aesthetically. A real newly generated
Slot 1 output needs manual visual confirmation before marking V1.3 as
appearance-accepted. Two image edits per new Lite output remain required.


## 2026-11-01 — Slot 1 Direct Inpaint V1: retire custom Skill dependency

**Audit:** Production default was still the Lite v1.3.0 local Skill, with
multiple old Front Skills enabled in the shared registry. Two native image
edits were technically proven, but no convincing evidence that the macro
improved front-fabric realism. A role image for a mask is *not* a native
provider-enforced inpaint mask, and a composite QA can mask an AI-generated
replacement photograph. The number of custom Skill configurations and prompts
increases the chance of unexpected precedence and inconsistent Retry behavior.

**Simplified new/default path for Stage 2 Slot 1 only:**

- Internal version marker `cam-slot1-direct-inpaint-v1`, SHA-pinned with
  `skill_source=internal`. It is not a Codex SKILL.md or external registry
  assignment. New queue and explicit Retry/Regenerate use it automatically.
- Input: one original unprinted factory cap ROI as the actual native edit
  target; reference inputs are the original Stage 1 design, aligned layout and
  edit-mask reference. Mask is not claimed as a native provider-enforced mask.
- ONE native ImageGen IMAGE-EDIT call in one Codex exec, using a short built-in
  instruction. The Codex runner requires the original native PNG SHA and
  exactly one native generated image; it rejects fake/coded final images.
  No macro, two-pass chain, user-installed Skill ZIP or text-to-image fallback.
- Existing output history, original cap pixels outside the mask, and the
  original Stage 1 typography/colors/placement remain protected. The post-QA
  also rejects a model that repaints the raw factory stock outside the mask.
- Historical queued jobs still use their originally pinned Skill and workflow.
  This is intentional for backwards compatibility; the old registry rows and
  SKILL.md bundles are not deleted (doing so would break running/queued jobs).
  Other Stages and Slots continue using their own Skills as before.
- UI Manage Skills indicates Slot 1 is built-in and does not present a custom
  Skill selector. The API refuses future changes to the obsolete Slot 1
  default. No new database table or migration.

**Acceptance caveat:** Tests verify prompt contract, original-stock input,
native image provenance, spatial protection, historical behavior and QA, but
do not prove the edited embroidery meets photographic aesthetic expectations.
A newly generated real Slot 1 example must be visually inspected before
calling the new appearance production-accepted.
