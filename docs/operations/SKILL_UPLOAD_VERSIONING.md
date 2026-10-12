# Skill upload-time release versions

CAM's **display/release version** for all managed image-generation skills uses `hh_mm_dd_mm_yy` in **Asia/Ho_Chi_Minh** (UTC+07:00), for example `18_39_12_10_26` for 12 October 2026 at 18:39 Vietnam time. The two `mm` fields mean **minutes** then **month**.

## Contracts

- ZIP upload, replacement and Stage 1 ZIP update: record the successful, changed installation time against the matching registry version; a content-identical reupload keeps its previous timestamp.
- Hosted OpenAI skill ZIP upload: stamp the created provider version when upload and runtime sync complete.
- Existing/bundled skills with no captured upload event: use the version's first recorded/indexed time; **do not describe this as a verified original upload timestamp**.
- The API exposes `upload_version`/ `version_labels` in the runnable Skill catalog and `*_upload_version` per registry entry and version row.
- **Runtime/provider version IDs remain unchanged.** Job submissions, version/default/sync/delete API arguments, retained job records and provider calls use the actual embedded version or provider ID; the UI translates them into time-based labels.
- Version identity is *not* the minute display label; multiple uploads in the same minute can have equal labels. The actual provider/runtime version (and SHA256 where available) disambiguates them. No synthetic seconds or false upload minutes are invented.
- A refresh/discovery, enable/disable, edit note, or change Stage default must not change the recorded upload time.
- If an existing local skill is replaced with changed files but embeds the same semantic version, the install/update timestamp changes, and the old release remains in audit/job history; it is not falsely offered as an installable local bundle version.
- Stage 1–4 old job skill versions are kept as originally pinned. Do not rewrite historical jobs to the newest timestamp: it could imply those jobs ran a later ZIP.

## Verification

Backend: `cd apps/api && .venv/bin/python -m pytest tests/modules/realistic_review_ugc/test_skill_registry.py -q`.

Frontend: `cd apps/client && npm run test -- SkillManagerModal.test.tsx KeywordImageStageManual.test.tsx`.

Staging/production smoke: exercise `stage2-skills/registry`, `stage2-skills`, Manage Skills modal, and selected versions after deployment; version IDs passed to actions must remain provider IDs, not display aliases.
