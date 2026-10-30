# CAM ThreadTransfer V1 — Fast-Track Production MVP

**Ngày cập nhật:** 2026-10-30
**Trạng thái:** Đã rà soát source + runtime; **chưa triển khai ThreadTransfer V1, chưa chạy end-to-end bằng provider thật**
**Phạm vi:** GatorHats Products → Stage 2 → **Slot 1 / Front Ecommerce**
**Pipeline ID:** `slot1_thread_transfer_v1`
**Nhánh triển khai duy nhất:** `main`
**Mục tiêu:** Chạy được **1 job thật trên production** nhanh nhất, tạo ảnh thêu có texture chân thực dựa trên macro reference, giữ nguyên stock mũ và artwork Stage 1.

## 1. Quyết định thiết kế: chỉ làm phần còn thiếu

**Giữ nguyên:** Stage 1 / Typography Engine V2, thiết kế đã chọn và màu chỉ; ảnh stock Valucap 8869/hat color; vùng front embroidery đã căn; các Slot 2–7; giao diện Slot 1, output/history, Run All, Download All; job queue, image workers, storage, Codex/ImageGen runner, mask/composite và QA hiện hữu.

**Thay đổi duy nhất ở đường chạy Slot 1:** tách thành một lượt sinh **macro embroidery reference** và một lượt **image-edit vùng thêu trên mũ**; đưa ứng viên qua composite + QA hiện hữu.

```text
Stage 1 selected artwork + original stock hat
                    |
           Existing input checks
                    |
PASS A: ImageGen -> 1 macro stitch reference (one candidate)
                    |
PASS B: ImageGen edit on cropped crown/embroidery ROI
        inputs: stock ROI + exact artwork guide + macro reference
                    |
Existing hard-mask composite + existing Slot 1 QA
                    |
Existing Slot 1 final output / history / downstream
```

**Không** paste nguyên miếng vải chữ nhật lên mũ. Macro chỉ là **tham chiếu chất liệu sợi**; alpha/silhouette/màu của Stage 1 là chuẩn tuyệt đối cho chữ, ảnh stock là chuẩn cho toàn bộ mũ.

### Loại bỏ khỏi đường găng MVP

- **Không** cài ComfyUI, Qwen, GPU/RTX 5080, Cloudflare FLUX, gateway mới hoặc một image API mới trước khi thử runner đang có.
- **Không** xây 3D mesh, mô phỏng vật lý mũ, DST/PES, vector hóa từng mũi thêu, hệ thống cache riêng.
- **Không** thêm database schema/migration, queue mới, scheduler mới, microservice mới, dashboard debug mới.
- **Không** triển khai A/B 10–20 designs, 3 màu stock, blind review, analytics lớn hoặc 7 giai đoạn gating trước lần chạy đầu.
- **Không** thay đổi skills của Slots 2–7, UI Stage 2, hay code Typography V2.
- **Không** thêm nhiều feature flags/điều chỉnh cấu hình; **chỉ một công tắc rollback** là đủ.
- **Không** bắt tạo cố định 3 macro references hay 3 image-edit candidates cho mỗi lần chạy. Giữ nguyên contract số output hiện tại của Slot 1 và cơ chế lưu history.

**Chỉ giữ các bước an toàn không thể bỏ:** provider thật sự chỉnh ảnh được, ảnh stock không đổi ngoài mask, typography/màu đúng, output lưu được và job hoàn thành; rollback có thể thực hiện bằng một flag.

## 2. Evidence rà soát ngày 2026-10-30

### Đã xác minh trong source `main` và runtime VPS

- Checkout `main`: `/srv/creative-asset-manager-source/.cam-stage2-three-row-prod-20261027`, tại `027548192` (09:52 +07 ngày 30/10, lúc rà soát); plan cũ là file untracked, chưa triển khai.
- Production current symlink: `/opt/creative-asset-manager/releases/52fdc61c76871c2450bfc1fb6626907bdca32470`. Có symlink `previous`; không đồng nhất production với HEAD mới nhất của main.
- Runtime `creative-asset-manager-api` và image workers `creative-asset-manager-image-worker[1..4]` đang **active**; `/usr/bin/codex` có trên máy. **Chưa** chứng minh account/provider của worker hoàn thành được cả hai edit call; cần smoke thật.
- Disk root 50 GB, đang dùng ~91%, còn ~4.4 GB; ưu tiên dọn **chỉ** working files của job hoàn tất, không đụng asset/output/DB/release hiện dùng.
- Code `apps/api/app/modules/realistic_review_ugc/ecommerce_pipeline.py`: `prod_s2_ec1` hiện làm **1** `generate_from_references` rồi gọi `finish_slot1_refinement`; **không** có Pass A macro và Pass B dùng macro reference.
- `apps/api/app/modules/realistic_review_ugc/slot1_embroidery.py` đã có `build_slot1_material_pass`, edit mask, color/geometry QA và composite trả về pixel stock gốc ngoài mask.
- `apps/api/app/providers/ai/codex_image.py` có `CodexImageGenRunner.generate_from_references` nhận target + role-labelled reference images, gọi Codex CLI/`$imagegen`, cung cấp cleanup staging; **mask là ảnh tham chiếu, không phải native provider inpaint mask**.
- Skill v4: `deploy/codex/skills/gatorhats-8869-ecommerce-front-thread-v4/SKILL.md`. Version metadata `ai-material-localized-v3` vẫn đang được ghi trong đường chạy v4; ThreadTransfer phải ghi tên pipeline thực tế.
- Provider flags hiện hữu: `PROCESSING_JOBS_ENABLED`, `IMAGE_GENERATION_ENABLED`, `MANAGED_ASSET_STORAGE_ENABLED`, `CODEX_IMAGE_GENERATION_ENABLED`. Không cần thiết kế lại.
- Skill **đang được pin trong DB production** và sự sẵn sàng **edit có dùng macro reference thực tế** chưa xác nhận. Kiểm tra chính xác ở bước P0, không đoán.

### Điểm nghẽn kỹ thuật duy nhất trước khi làm MVP

**Cần chứng minh bằng job thật** rằng Codex runner có quyền chạy `$imagegen` để (1) tạo macro từ PNG thiết kế và (2) image-edit ROI của ảnh stock với ảnh macro role-labelled. Runner nhận nhiều file reference **không tự chứng minh** provider có thể chỉnh ảnh đúng cách. Nếu model chỉ sinh full-frame hat mới, fail và giữ đường cũ; không gắn nhãn inpaint giả.

## 3. Implementation MVP: 3 bước, không hơn

### P0 — Kiểm tra nhanh đường chạy [SOURCE/RUNTIME AUDIT DONE; LIVE PROVIDER SMOKE PENDING]

1. Đọc `main` mới nhất và production current; kiểm tra skill pin và quyền imagegen của worker (không đọc/xuất bí mật đăng nhập).
2. Chọn **1 artwork Stage 1 đã picked + 1 stock front đúng colorway** và placement đã pin; không sửa Stage 1.
3. Thử **một** lượt reference và **một** lượt edit thông qua `CodexImageGenRunner` trong môi trường job thực. Đo đầu ra: có edit dựa trên input ảnh và macro, không phải text-to-image. Giới hạn chi phí/lần thử.
4. Nếu provider/skill thiếu edit thật: ghi lỗi có thể chẩn đoán, **dừng**; chỉ khi đó mới đánh giá provider thay thế. Không dựng ComfyUI trước.

**Done:** 1 macro PNG và 1 edit PNG thực tế, prompt và đúng input roles xác nhận được; không phải mockup/chat demo.

### P1 — Tích hợp nhỏ vào Slot 1 [PENDING]

Chỉ sửa/tạo các thành phần dưới đây:

| Vị trí | Việc cần làm |
|---|---|
| `ecommerce_pipeline.py` | Nhánh `prod_s2_ec1` có flag; gọi Pass A rồi Pass B, các slot khác giữ nguyên |
| **1 file mới** `slot1_thread_transfer.py` | Hàm phối hợp 2 pass, crop/ROI, map kết quả trở lại ảnh gốc, tên attempt riêng cho A/B, cleanup |
| `slot1_embroidery.py` | Tái sử dụng mask/composite/QA; chỉ thay đổi nếu crop mapping cần tương thích |
| **1 Skill mới** `gatorhats-8869-slot1-thread-transfer-v1` | Chỉ dẫn role cho Pass A vs B; tuyệt đối giữ artwork/stock, 1 PNG mỗi invocation |
| `config.py` | Một flag `CAM_STAGE2_SLOT1_THREAD_TRANSFER_ENABLED=false` |
| Tests sẵn có | Bổ sung test nhỏ cho crop mapping/stock protection, route chọn flag, failure mode, metadata |

Cụ thể:
1. **Pass A:** target = canonical Stage 1 artwork (PNG/transparent composited for provider); sinh **1 macro thêu low-profile trên cotton/twill**, có satin/tatami thread, sợi và mép chạm vải thật. Không đổi chữ, màu, tỷ lệ hoặc thêm patch border. Reference lưu bằng **CAM asset storage hiện tại** khi dùng lại được; không tạo cache service, migration hay bảng DB.
2. **Pass B:** crop khu vực thêu/front ROI trên ảnh stock, thêm biên đủ để nhìn được độ cong bề mặt; chuẩn bị guide pigment, mask (chỉ vai trò tham chiếu), artwork chuẩn và macro. Gọi **image edit trên ROI thực**, không tái tạo nguyên mũ; chuyển crop edited về đúng tọa độ/độ phân giải rồi đưa qua `finish_slot1_refinement`.
3. **Composite/QA:** chỉ chấp nhận sợi được tạo bởi AI bên trong silhouette + contact band nhỏ đã cho phép. Dùng hard gate hiện có về màu/hình dáng, không-no-op, lỗi in phẳng/halo, pixel outside-mask **exactly unchanged**. Kèm **1 lượt retry Pass B tối đa** nếu output fail; không sinh lại macro khi retry edit. Khi fail thì báo failed, **không** lặng lẽ xuất bản ảnh giả.
4. **Output:** ghi `slot1_embroidery_pipeline.version=slot1_thread_transfer_v1` và source/reference hashes + pass status trong metadata JSON có sẵn. Giữ contract ảnh Slot 1, lịch sử, số output theo workflow hiện tại; không thêm modal/ảnh debug mới. Không xóa ảnh/skill v4.

**Cần tránh:** dùng `attempt_id` trùng nhau cho A/B vì runner cleanup workspace theo attempt; phải đặt ID tách biệt, xóa staging job sau khi storage thành công/thất bại. Bảo đảm không lặp Pass A do retry Pass B.

### P2 — Chạy production thật và chốt MVP [PENDING]

1. Commit/push **chỉ code liên quan lên `main`**, tuyệt đối không gộp nhầm root checkout dirty hay các nhánh Typography V2. Chạy test tập trung:
   - `test_slot1_embroidery.py`
   - `test_ecommerce_pipeline.py`
   - `test_slot1_skill_repin.py`
   - `test_codex_image.py`
   - thêm test ThreadTransfer; chạy build API/client **chỉ khi các phần đó thực sự thay đổi**.
2. Deploy theo pipeline production đang có; giữ `CAM_STAGE2_SLOT1_THREAD_TRANSFER_ENABLED=false` lúc xác minh health. Không thêm hạ tầng/worker; cập nhật Skill vào môi trường Codex worker hiện hữu.
3. Enable flag cho **một lần regenerate Slot 1 đã chọn**. Không bấm Run All 1–7 khi chưa ổn; không batch re-run các output cũ.
4. Mở ảnh output ở kích thước mũ và crop vùng thêu: kiểm tra chữ/màu, cong theo crown bằng ánh sáng/chất liệu (không làm cong baseline chữ), đường chỉ nhỏ, hơi lún ở mép, không bị cao su/3D puff/in phẳng, không đổi stock bên ngoài mask.
5. Nếu output hợp lệ, chạy thêm một artwork khác (và colorway khác nếu cần) rồi chốt MVP. Nếu hỏng, tắt **một flag**, giữ ảnh cũ, không rollback toàn hệ thống, không xóa asset lịch sử.

**Acceptance tối thiểu:** một job **thật** kết thúc completed và lưu được output; hard mask outside-diff = 0; artwork text/silhouette/color đúng; nhìn macro crop có sợi thật/tiếp xúc vải chứ không phải print/emboss; image worker không lỗi và pipeline downstream đọc được front output.

Không coi test unit hay mockup là bằng chứng chạy được trên production.

## 4. Một công tắc, một đường rollback

```dotenv
CAM_STAGE2_SLOT1_THREAD_TRANSFER_ENABLED=false
```

- `false` → thực thi đường Slot 1 v4 đang dùng (không sửa lịch sử).
- `true` → chỉ job **mới** của `prod_s2_ec1` vào ThreadTransfer V1.
- Job đã pin/đang chạy phải giữ hành vi nhất quán; không đổi engine giữa job.
- Provider image edit unavailable → lỗi rõ, **không** fallback sang full-frame synthetic hat. Rollback khi fail bằng flag off và khởi động lại đúng service theo quy trình hiện hữu.
- Giữ tối đa 1 macro + 1 Pass B candidate mặc định, retry Pass B thêm 1 lần nếu cần; không gọi nhiều AI vô ích.

## 5. Prompt lõi — ngắn, không prompt stacking

**Pass A (reference)**
> Edit the supplied exact Stage 1 artwork into a macro photograph of real **flat machine embroidery** sewn into neutral woven twill. Preserve visible lettering silhouettes, line breaks, pigments, spacing and layout. Fine satin stitches following curves, compact tatami fill for broad strokes, tiny filament highlights and needle entry points, subtle cloth compression. No puff foam, printed fill, plastic, embossed bevel, patch edge, new text or decorative background. This is a *material reference*, not a cap photo.

**Pass B (localized cap edit)**
> Edit **only the supplied crop of the original cap front**. The crop is the target photo; the Stage 1 artwork defines exact glyphs, colors and placement; the macro embroidery image defines *thread material only*. Integrate realistic fine machine stitches sewn **into this cap's existing curved twill**, with restrained directional highlights and tiny thread-foot compression. Do not warp lettering baselines or change glyph outlines. No pasted rectangular patch, sticker, plastic/foam, thick grooves or printed look. Preserve cap weave/lighting. All outside-mask pixels will be restored by backend; do not regenerate the whole hat or add new text.

**V1 nguyên tắc:** hai prompt và một Skill có hai role rõ ràng; không giữ hàng loạt biến thể prompt v2/v3/v4 chồng lên nhau trong đường ThreadTransfer.

## 6. Việc để sau MVP — chỉ khi có bằng chứng cần

- Persistent reference cache liên job, nhiều macro candidates/3-output diversification nếu chi phí hoặc quality cần.
- UI hiển thị reference và so sánh before/after, bảng quality/cost lớn.
- Automatic perceptual realism scorer, A/B lớn 10–20 artwork/màu, thêm model/provider.
- ComfyUI/Qwen trên máy tool, GPU nội bộ, Cloudflare, batch optimizer, 3D/geometry engine.
- Tối ưu sâu Slot 2–7 hoặc Stage 1 — **ngoài phạm vi** ThreadTransfer V1.

## 7. Continuation khi người dùng gõ “tiếp tục”

Đọc tài liệu này, kiểm tra HEAD `main` + production current + dirty files; tiến hành **bước P0/P1/P2 đầu tiên chưa xong** và ưu tiên đưa đến output thật. Không hỏi lại xác nhận chung. Sau mỗi lượt, cập nhật trạng thái, file/commit, test, ảnh thử thực tế, blocker duy nhất nếu có. Không tuyên bố đã deploy/chạy thành công nếu chưa kiểm chứng. Không xóa dữ liệu nguồn, sản phẩm, output, database hoặc release rollback; dọn staging tạm sau run.

## Changelog
- **2026-10-29:** Kế hoạch tham chiếu 7 giai đoạn T0–T6, chưa triển khai.
- **2026-10-30:** Rà soát `main`/runtime và thay bằng **3 bước P0–P2**, chỉ một Skill + một orchestration file + một flag; giữ nguyên CAM worker/storage/composite/QA; P0 live provider edit vẫn pending; **chưa code/deploy ThreadTransfer V1**.
