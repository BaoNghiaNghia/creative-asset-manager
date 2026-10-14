# GatorHats Products Stage 3 — Seedance 2.5 image-to-video

## Workflow
Products now has Stage 0 (keyword analysis), Stage 1 (embroidery designs), Stage 2 (4-skill photos and 13 colors), and **Stage 3 (Seedance videos)**. Reviews continues using its own Stage 2/3/4/5 routes. URL: `/realistic-review-ugc?workflow=products&stage=products-stage3`.

Stage 3 loads only GatorHats Products Stage 2 designs, then displays completed 13-color hat output images. Select 1–8 reference photos for one Seedance 2.5 video. The ten Ecommerce/Multi-hat/UGC outputs remain unavailable as video references until their respective Stage 2 photo workers and persistent outputs are implemented.

### Layout
- Compact KPI strip: designs, completed source images, selected references, running/completed videos
- Source design carousel with search and pagination, thumbnail image grid with selection count and clear action
- Video director with model `seedance-2.5`, server-provided ratios/durations, realistic motion prompt presets and editable prompt
- Persistent history for the selected design with status, refresh, pre-submission cancel and video playback modal
- Responsive: two columns on desktop, stacked on tablet/mobile; no fake completed outputs or client-only status

### Backend contract
- `POST /api/v1/realistic-review-ugc/products-stage3/videos` uses an explicit ordered array of completed Stage 2 `colorway_job_ids`, stable `client_request_id`, prompt, ratio and duration.
- Tenant-bound validation requires Products root, matching source revision, completed image, authentic Managed Storage bytes, MIME and SHA-256 verification; no arbitrary external URLs accepted.
- The adapter registers previously generated Stage 2 imagery as canonical AssetModel and AssetStorageObjectModel records, preserving existing source files. The native `video_generate` job, Dola/Seedance capabilities, canary gate, idempotency and queue are reused.
- The video worker also supports read-only Managed Storage reference assets where no SourceAsset link exists, verifying SHA-256 before sending the image to Dola. Existing externally linked assets retain their current resolver.
- `GET /api/v1/realistic-review-ugc/products-stage3/videos?source_plan_id=...` reads durable matching video runs, not ephemeral browser state. Native `GET /api/v1/video-generations/{id}` and `GET /api/v1/video-generations/{id}/video` power refresh/playback.
- If the tenant has not been enabled for video generation, Stage 3 remains browsable with the Generate action disabled. Stage 3 never overrides production canary or provider credentials.

### QA
- Frontend route separation, Stage 3 UI rendering, source/result controls and TypeScript build
- Backend existing video handler and Stage 3 adapter tests, retry idempotency and output provenance validation
- Deploy backend before frontend, then run production health/UI smoke. Fully authenticated in-app Stage 3 QA still requires a secure browser session.
