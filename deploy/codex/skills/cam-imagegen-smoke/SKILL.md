---
name: cam-imagegen-smoke
description: Minimal VPS capability probe for built-in image generation.
---

Create exactly one simple photorealistic square PNG showing a plain red ceramic mug on a neutral tabletop.

Requirements:
- Use $imagegen as the image-generation capability.
- Save the final image to `output/final.png`.
- Do not use an API-key-backed fallback.
- Do not create additional final images.
- Verify `output/final.png` exists and is a valid PNG before finishing.
