# Stage 1 — verified 200-family DaFont pool

Stage 1 uses **200 distinct licensed font families**, not 200 copies/styles
of the same handful of font files. Approved font bytes and a full SIL Open Font
License 1.1 document are shipped with both the API skill and the Codex skill.
A font is active only when the registry entry is enabled, approved and marked
embroidery-safe, and the exact SHA-256 digest matches the local font.

## Provisioning and safety

An authorized maintainer can build/reconcile the exact target using:

```bash
python3 scripts/cam_stage1_dafont_expand.py --target 200 --max-pages 5
```

This is an **explicit, offline administrative operation** relative to running
jobs. It fetches DaFont category/author catalogs, downloads individual DaFont
archives with bounded sizes and request pacing, and accepts only complete SIL
OFL 1.1 license texts plus TTF/OTF files that Pillow can load and rasterize.
It rejects filenames that are not plausible embroidery bases, invalid
archives, duplicate font bytes, duplicate family names, missing licenses and
unreadable glyphs. A numbered count is NEVER substituted for actual assets.

The importer mirrors source fonts and registries to:

- `apps/api/managed_skills/gatorhats-stage1-six-designs/`
- `deploy/codex/skills/gatorhats-stage1-six-designs/`

It writes each registry through atomic replacement and can resume after
interruption. An incomplete pool must not be presented as 200 approved fonts
or released. Existing 24 approved families remain unchanged.

## Generation behavior

The worker chooses **six different font families for every new job**. With
200 available families it can exclude fonts from as many as the previous
30 completed font plans, gradually reducing the exclusion window only when
old job history exhausts the remaining candidates. Per-family usage is
penalized over 30 jobs. Each job stores its six-font plan for reliable
retry/resume, while an explicit Regenerate creates a new plan.

Font files are **not** downloaded by generation workers. Job assets are
used locally to render actual brown-thread typography guides; missing or
modified files cause a safe failure, not an imaginary font attribution.
The API endpoint `GET /keyword-images/dafont-pool` reports the verified
installed count and per-font usage in the Stage 1 DaFont Pool modal.

## Release checks

1. Run registry validation and license/ZIP regression tests.
2. Check 200 different families and SHA256 digests in the pool; both skill
   copies must agree.
3. Check distinct-font selection and avoidance of the previous 30 full jobs.
4. Render six Stage 1 design font guides with Pillow (brown and transparent).
5. Deploy the backend release (which synchronizes persistent Codex skills),
   then verify API/worker health and deployed font registry count.
6. Confirm the DaFont Pool UI reports 200 and new jobs select six distinct
   approved families. Never rewrite already-generated review outputs.

OFL license reference: https://openfontlicense.org/
