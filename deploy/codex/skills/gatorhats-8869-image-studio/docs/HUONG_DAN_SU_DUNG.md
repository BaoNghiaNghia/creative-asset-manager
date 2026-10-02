# Hướng dẫn sử dụng — GatorHats 8869 Image Studio V3

## Dùng khi nào

Dùng skill này khi cần **full image workflow** cho Valucap 8869: ecommerce, multi-cap, UGC, sửa ảnh, QA, tiếp tục set, hoặc đóng ZIP.

Nếu mục tiêu chỉ là lấy **một design có sẵn rồi scale qua nhiều colorway 8869**, dùng workflow/skill **Scale Image 8869** để tránh trộn rule recolor vào Image Studio.

## Input tối thiểu cho set mới

1. Upload artwork mặt trước.
2. Nói một colorway official, ví dụ `Natural/Black`.
3. Side text nếu cần; nếu không nói thì mặc định `Your Text`.

Ví dụ:

`Tạo full set 10 ảnh Natural/Black, side text "BAO VU". Tự QA và chỉ sửa ảnh fail.`

Không side text:

`Natural/Navy, full set 10 ảnh, không side text.`

Review master trước:

`Natural/Maroon. Tạo master front trước, chờ tôi duyệt rồi làm tiếp.`

## Các colorway official trong package

- Khaki/Maroon
- Natural/Black
- Natural/Brown
- Natural/Camo Green
- Natural/Charcoal
- Natural/Forest Green
- Natural/Khaki
- Natural/Maroon
- Natural/Mossy Oak Breakup
- Natural/Navy
- Natural/Realtree All Purpose
- Natural/Red
- Natural/Royal

Màu ngoài danh sách chỉ dùng khi bạn nói rõ `custom mockup`.

## Lệnh ngắn trong cùng chat

- `làm tiếp`
- `fix ảnh 3: side text nhỏ hơn 20%`
- `QA toàn bộ set, chưa sửa`
- `fix toàn bộ blocker`
- `đổi side text thành DAD MODE cho các ảnh chưa làm`
- `đóng ZIP ảnh final`

V3 giữ state theo job hiện tại, nên không cần lặp lại toàn bộ thông tin khi vẫn đang làm cùng một artwork.

## Thread color

- Front artwork giữ logic màu gốc và map sang thread có thật trong `mauchi.txt`.
- Front artwork **không tự động match bill**.
- Side text mặc định phối theo bill bằng curated map.
- Nếu bạn chỉ định thread code hợp lệ, lựa chọn của bạn được ưu tiên.

## Khi sửa một ảnh

Nên nói rõ **được thay đổi gì**. V3 áp dụng delta-edit lock: các phần không được yêu cầu sẽ được giữ nguyên tối đa.

Ví dụ:

`Fix ảnh 2: side text nhỏ hơn và lùi về sau một chút. Giữ nguyên artwork, mũ, góc chụp, ánh sáng và crop.`

## QA

V3 chia lỗi thành:
- **BLOCKER**: bắt buộc sửa, như sai chữ, sai form mũ, sai colorway, sai side text, deformation lớn.
- **ACCEPTABLE VARIATION**: không regenerate chỉ vì sai khác nhỏ, như microtexture chỉ thêu hoặc background trắng không đúng tuyệt đối từng pixel.

Mỗi ảnh có tối đa 2 recovery attempts để tránh regenerate loop.
