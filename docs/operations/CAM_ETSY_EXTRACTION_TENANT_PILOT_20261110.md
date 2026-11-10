# CAM Etsy Spy — Real Picked extraction tenant pairing (2026-11-10)

## Production observation

- Picked listing records are tenant-bound to `creative-assets-prod` (269 rows at audit time).
- 246 jobs of external type `etsy_design_extract` are already pending there.
- The previous Windows Image Agent credential belongs to the legacy `boostgame` tenant (zero Picked there). A successful probe of that agent does **not** mean it can claim these 246 jobs.
- Existing production extraction transport/storage are available, with a one-claim-per-Vietnam-day cap and zero claims at the audit time.
- Do not duplicate Picked entries or shift tenant IDs to work around this mismatch.

## Tenant-safe pairing

- Dedicated new device: Windows creates a unique 64-character secret only under `D:\\CAM Image Extension\\.runtime\\extraction`, in `cam-config.creative-assets-prod.staged.json`. Only UUID and SHA256 digest are shared with CAM. The old secret and journal are never sent or deleted.
- CAM offline registration now requires an **explicit** `--tenant-id` equal to `creative-assets-prod` or `boostgame`; other scopes remain forbidden. A device is immutably tenant-bound and must use its own bearer credential.
- On Windows, execute `node agent/tenant-pair.mjs prepare`. Register the printed hash on the CAM production host with `PYTHONPATH=apps/api apps/api/.venv/bin/python scripts/pair-cam-image-agent-hash.py --tenant-id creative-assets-prod --agent-id UUID --token-sha256 SHA256`. Never paste the secret token in a terminal or chat.
- Check the new disabled credential with its probe. On the old Windows relay, use `node agent/pilot-control.mjs disarm`; verify old daemon lock has been released and there is no active journal; only then use `node agent/tenant-pair.mjs activate`. This moves the previous config to `config-backups` without erasing it and enables the new exact tenant device.
- Start the already installed outbound relay using `START_CAM_EXTRACTION_RELAY.cmd`; never open extra Chrome windows or change the Scout/Codex services.

## Production queue safety

- The existing 246 pending jobs are **not** reinserted. One claim/day is the initial pilot.
- The real `FastExtract.cs` can create a 4500×5400 transparent review PNG only when the crop/cloth evidence passes its conservative segmentation. Complex photographs go to `needs_ai` without invoking paid APIs or ChatGPT automatically.
- The result API accepts one immutable SHA256-bound receipt and keeps `manual_qa_required=true`, `print_ready=false`, `rights_status=unverified`; do not sell/reuse a recovered design without verifying commercial rights.
- Use `scripts/cam-etsy-image-pilot-control.py status --tenant-id creative-assets-prod` for real Picked/queue counts; a boostgame-only status always displays zero jobs for the actual production Picked tenant.
- On an ambiguous result, **do not rerun** the job: replay the same receipt under the original lease/journal. If the old relay is busy, do not switch credentials.

## Scope and rollback

- Source changes are limited to offline device registration, tenant-aware pilot controls and paired Windows agent. No Scout Queue V3 job, Picked row or source image is modified to change tenant.
- To halt new claims, use `node agent/pilot-control.mjs disarm` on Windows. CAM can also disarm the transport with `scripts/cam-etsy-image-pilot-control.py disarm --tenant-id creative-assets-prod` (affects all extraction agents; restart API). Retain all journal, backup config and review assets.
- Keep one current and one rollback CAM release only after successful smoke tests; never purge data, extracted PNGs or token backup files.
