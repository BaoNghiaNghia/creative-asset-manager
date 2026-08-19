# VIDEO-8B production capability deployment

Target commit: `125ead34f1c331c665ebc2c46849b961616a1117`
VIDEO index version: `125ead34f1c3`

Run this only on the production VPS, from its clean source checkout at the target
commit. It installs capability while leaving video processing disabled; it does
not start VIDEO-8C or a pilot.

```bash
cd /srv/creative-asset-manager-source
git status --short
git rev-parse HEAD
VIDEO_8B_TARGET_COMMIT=<reviewed-full-commit> \
VIDEO_8B_RELEASE_ID=<reviewed-12-character-release-id> \
bash scripts/video_8b_production_deploy.sh
```

The script fails closed unless the native API and worker services, root-owned
production environment file, immutable release root, loopback Elasticsearch,
`ffmpeg`, `ffprobe`, and writable video state directory are present. It also
requires at least `1,567,108,864` free bytes and these flags to remain false:

```text
VIDEO_SEARCH_ENABLED=false
VIDEO_ANALYSIS_ENABLED=false
VIDEO_PROXY_ENABLED=false
```

It installs the supplied immutable release, migrates forward only, provisions
`<ELASTICSEARCH_INDEX_PREFIX>-video-v3-<reviewed-release-id>`, validates the VIDEO
mapping, and confirms IMAGE aliases are unchanged before release switch.

If the physical index already exists but is incompatible, stop without deleting
or changing it. An operator must rerun after a distinct reviewed operator version such as
`<reviewed-release-id>-r2` (and update the handoff through the normal change process).

If service health fails after switching, use the established rollback only:

```bash
sudo deploy/bin/cam-deploy rollback-release
```

Rollback never downgrades PostgreSQL and does not delete the VIDEO index. Do not
enable VIDEO flags, enqueue video jobs, run backfill, invoke Gemini, create video
proxies, or start VIDEO-8C as part of this operation.
