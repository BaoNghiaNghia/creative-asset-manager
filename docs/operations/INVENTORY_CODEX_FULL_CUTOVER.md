# Inventory Codex CLI production cutover

Updated 2026-11-18. Scope: CAM Inventory only. Etsy Spy, TikTok Shop,
RRUGC, creative ImageGen, and GatorHats model providers remain unchanged.

## Verified architecture and safety

- Account 01 uses the existing ChatGPT Codex CLI login under creative-assets.
- Account 02 is never an automatic auth or quota fallback.
- RuntimeInventoryCodexGateway covers image extraction, structured semantic
  JSON, V3 planning, V4 multi-round tool calls and daily carry-forward.
- Codex runs ephemeral and read-only with a restricted environment, no Google
  token, R2 key, paid API key, unrestricted shell or write permission.
- Codex emits schema-bound JSON tool requests. Only CAM's allowlisted tool hosts
  can execute them. Tenant scopes, read budgets, evidence hashes, staged edit
  guards, canonical material IDs and read-back verification remain authoritative.
- The historical Gemini file IDs and DB columns remain, preserving old links.
  New provider records use codex_cli / codex-cli-default.
- Default CLI timeout is 80 seconds per call, bounded for the 30-minute V5J
  one-shot service. Busy profile and timeout errors retry with backoff.

## Phased, guarded production rollout

1. Pass all Codex, Inventory V3/V4, carry-forward, semantic, scheduler,
   cutover, and permission tests. Check Git diff --check.
2. Deploy the new backend source. No runtime provider switch occurs automatically.
3. Run both synthetic-only operator checks under creative-assets:

    bash scripts/cam-codex-runtime.sh inventory-smoke

    bash scripts/cam-codex-runtime.sh inventory-tools-smoke

   Both must return validated JSON and writes=0. They do not access real
   customer Inventory spreadsheets.
4. Confirm the Inventory V5J lifecycle service is inactive (no active cycle).
   Run read-only status:

    apps/api/.venv/bin/python -m app.operations.inventory_codex_cutover check

5. Activate under the authorized operator with root permission:

    apps/api/.venv/bin/python -m app.operations.inventory_codex_cutover activate

   The operator stops the V5J timer, updates only the named creative-assets-prod
   Inventory control row and four nonsecret INVENTORY_* environment fields,
   safely restarts affected services and resumes the timer. It aborts on
   mismatched tenant, settings drift, in-progress cycle or unsafe filesystem.
6. Confirm API and Inventory UI ready, provider/model persisted, timer active,
   no Gemini requests in new Inventory jobs, and staged-plan verification
   before any Google Sheets write.
7. Update current provider attribution in UI while retaining the historical
   Gemini copy labels and schema keys as archival identifiers.

## Explicit rollback

If a new Codex workflow fails its safety gates, the operator can invoke:

    apps/api/.venv/bin/python -m app.operations.inventory_codex_cutover rollback

This restores the previous Inventory provider/model fields and environment
keys from nonsecret rollback state and restarts the affected services. The
legacy Gemini storage keys and historical workbooks are not rewritten.
A failed mid-cutover also attempts this restoration automatically; if
automatic rollback fails, operator review is mandatory.

## Operational limits

ChatGPT Codex CLI account usage limits still apply; these sessions are not
unlimited or a separate paid API. Unknown materials or unsupported quantity
units must remain review_required. A CLI authentication or quota failure
must never silently fall back to Gemini or mutate workbooks.
