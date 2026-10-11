# CAM AI Operations — production triage and bounded recovery (2026-10-11)

## Observed, read-only production diagnosis

The API, image workers, video workers and Elasticsearch are online. An operational dashboard marked **93 Visual Search** jobs as needing review; database grouped by exact error `visual_index_source_unavailable=93`. This indicates source access/record health; do not try to solve it with Elasticsearch reindexing alone. Current-generation Visual Search backfill already uses a once-per-source recovery policy. This release also selects an **alternate accessible source linked to the same asset and content hash**, if the original is no longer streamable; it remains single-retry fenced.

**Video analysis**: 12,593 eligible video files; 12,455 completed analysis jobs; 12,377 indexed. Exactly **138 failed video_analyze jobs** observed. Error codes:
- 41 `video_proxy_source_invalid` — invalid/unreadable source; no blind retry.
- 33 provider `gemini_video_bad_request` — invalid or unsupported request; no blind retry.
- 23 `video_proxy_process_failed` — FFmpeg/proxy issue; inspect per-source log/ffprobe first.
- 18 `video_proxy_storage_exhausted` — **blocked by disk headroom**; production backend deploy showed about 5.8 GiB available while default `VIDEO_PROXY_MIN_FREE_DISK_BYTES` safety reserve is 8 GiB, plus input/output working space. Do NOT lower reserve or requeue these until host headroom is restored.
- 10 `gemini_video_invalid_metadata`, 5 `gemini_video_transport_error`, plus smaller grouped errors.
- Recovery CLI retries only **six eligible, transient** jobs (`gemini_video_transport_error` or `video_proxy_timeout`) in dry-run; source must still be active. Other 132 failures remain for source repair/space/provider-specific examination. The CLI records one recovery marker per job to avoid repeated automatic loops and preserves historical run/chunk records.

**Image pipelines**: 435 pipelines in terminal failure states, **434 intentionally excluded** as invalid/unsupported/oversized. Only one currently actionable projection failure. Ten historical projection jobs have error code `ValueError`; 5 failed image analyses have managed storage problems. The protected recovery tool checks pipeline and completed analysis before requeueing; this release also repairs stale `analysis_id` in a failed projection job, but **never overrides an explicit active-analysis pin**.

**Gemini**: at least 16 image-analysis `gemini_http_error` jobs; live image-worker logs include model 404 (deprecated/removed models) and 429 (quota exhaustion). Respect shared project quotas/cooldowns; do not bulk retry 4xx. Provider model config needs updates through approved workflow if old model remains selected.

**Managed Google storage**: configured and shown `connected` in metadata, but live folder permission validation reported: `The selected Google account cannot manage files in the active storage folder`. A dry-run refresh validation returned `invalid_refresh_token`. **Account reconnection/permission repair is required**; do not assume token is usable or mark all storage jobs retriable. This release's recovery wrapper adds `--skip-storage-validation` specifically to avoid altering OAuth/reconnect flags during unrelated bounded recoveries.

## Safe operator commands (production VPS project root)

Read-only:
```bash
bash scripts/cam-pipeline-attention-diagnostics.sh
bash scripts/cam-pipeline-attention-repair.sh --skip-storage-validation --limit 20
bash scripts/cam-pipeline-attention-repair.sh --video-only --skip-storage-validation --limit 200
```

After source code is deployed and reviewed, bounded operator actions:
```bash
# For transient video transport/proxy-timeout only. Leaves disk-full and invalid source jobs failed.
bash scripts/cam-pipeline-attention-repair.sh --video-only --skip-storage-validation --apply --limit 20
# For verified image analysis/projection jobs only, no OAuth flag modifications.
bash scripts/cam-pipeline-attention-repair.sh --skip-storage-validation --apply --limit 20
```

On each action re-run diagnostics and verify job transitions. Job counts may change when background workers act. **Do not delete failed jobs, previous analysis versions, source records or video chunks**; completed images/video remain intact.

### Remaining external dependencies

- Reconnect the authorized managed Google Drive account and ensure it has edit permission on the exact configured storage folder.
- Restore VPS free disk space above proxy safety reserve + expected source/output requirements (prefer increasing VPS volume or approved retention cleanup of non-authoritative temporary data). Do not change the 8 GiB safety floor on a near-full 40 GiB VPS.
- Inspect malformed/unsupported video sources and Gemini 400 errors individually; do not write a mass-retry loop.
- Fix Gemini model access/quota separately; avoid moving all traffic to shared Review Scout keys.
- Retest on desktop/tablet/mobile and smoke production after deploying.

Rollback: revert the isolated Visual Search alternate-source recovery, projection stale pointer update, scoped video repair helper, and UI wording. The one-time recovery markers are additive in job JSON and do not delete prior data.
