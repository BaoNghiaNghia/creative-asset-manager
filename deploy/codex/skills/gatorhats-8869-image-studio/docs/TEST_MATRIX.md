# Manual Test Matrix — V3

Use these prompts after installing the skill.

| Test | Prompt | Expected behavior |
|---|---|---|
| New set | `Natural/Black, tạo full set 10 ảnh từ artwork này.` | Uses NEW_SET; reads only Natural/Black stock; front art keeps own colors |
| No side text | `Natural/Navy, 10 ảnh, không side text.` | Side embroidery absent everywhere |
| Targeted edit | `Fix ảnh 3: side text nhỏ hơn 20%.` | EDIT mode; unrelated properties frozen |
| Continue | `Làm tiếp.` | Continues pending output; does not restart |
| QA only | `QA set này, chưa sửa.` | Reports blockers/acceptable variation; no generation |
| Custom color | `Custom mockup White/Black.` | Allowed as custom; never called official stock |
| Missing official color | `White/Black, tạo official set.` | Corrects/asks for custom intent; does not claim official |
| Color scaling conflict | `Scale design này ra đủ 7 màu 8869.` | COLOR_SCALE_HANDOFF; does not apply full Image Studio workflow by default |
| Thread behavior | `Giữ artwork vàng/đen, Natural/Navy.` | Front remains yellow/black mapped to inventory; side text can coordinate Navy |
| Reference leakage | Non-dog artwork + UGC request | No dog/barber content unless current job calls for it |
| Retry loop | Repeated minor white variance | Acceptable variation; no endless regeneration |
