# Muse Image Lite QA Stage V2 — P0/P1 integration

Base bdc561922b2744c1e73a5bd6872204e3b81ef90c. Private original-byte PNG staging, independent QA reject, bounded operator retry and rejected reservation release. Native >=2000 publication gate; actual provider-native capability must be verified, no upscale. Role 01 only. No migration, Video change, Skill registry change, or cron.

Regression: 52 passed, 2 PostgreSQL-specific skipped. Separate disposable PostgreSQL claim and retry contention tests each passed. Offline Muse runner 30/30, not renderer evidence.

Deploy only by official immutable --no-migrate --no-restart release, then API-only restart at safe video idle boundary. Verify current release, ready, /work contract and reservation, video PIDs. Stage root private, 0700/0600, max32MiB per PNG, aggregate192MiB, reserve2GiB. No new render or QA receipt at deploy.

Current pilot 060a42d0-bc23-4bda-be69-4677c1f467ff, epoch1 attempt b769288220bb4dd0be019e1c4bfbee93. Preserve journal, token, lease and exact role-01.png SHA 4796591cace70568f5162646cfb0f7e462264515ab595adfcae0af3805bf12c1. Stage original, not chat copy. Independent reject only after exact bytes reviewed; retry only if actual native >=2000 capability verified. No automatic pack expansion or Hybrid compositor. Keep rollback release; reconcile new journal before rollback after any state transition.
