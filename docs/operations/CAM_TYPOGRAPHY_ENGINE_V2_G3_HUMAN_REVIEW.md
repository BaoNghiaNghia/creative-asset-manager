# G3 — Human Style DNA Review and Structural Override

**Status: code-only, feature OFF; requires CI and G0.1 before backend production deployment.**
This checkpoint adds a controlled review step after an AI result is validated. It **does not** call AI, change the Stage 1 V1 skill, register a worker or copy the words/logo in reference artwork.

## API and permissions

- `GET /api/v1/realistic-review-ugc/typography/references/{id}/dna` (`typography_reference.read`) returns the raw versioned AI DNA, plus `review`: decision (`pending` by default), reason code, reviewed timestamp, whether an override exists, and **effective_dna only if accepted and still eligible**. Never returns private Managed Storage keys or a provider payload.
- `GET .../references/{id}/analysis` (`read`) also exposes `dna_review_status`, separate from asynchronous job `analysis_status` and `dna_available`.
- `PATCH .../references/{id}/dna/{dna_id}/review` (`typography_reference.manage`) accepts:
  - `decision`: `accepted`, `needs_review`, or `rejected`.
  - `reason_code`: one fixed enum, consistent with decision. Low-confidence (under 0.65) acceptance requires explicit `low_confidence_reviewed`.
  - `override`: optional and **only** for accepted DNA. Allowed structural fields: normalized typography bounding rectangle, general font-mood enums, a small layout graph with generated node IDs `n1…n12`, and overlap rules capped at 35% occlusion. All free text, original lettering, unrecognized fields and raw image data are rejected.
- Server enforces the V2 flag, exact tenant allowlist, permission, current analyzer version and source SHA256. **Acceptance** additionally requires source rights `owned` / `licensed` / `permitted_reference`, editorial status `approved` and an active reference. Rejected/needs-review decisions can flag bad DNA before rights are verified. Archived and stale results cannot be reviewed.
- Row locking prevents review writes racing concurrent rights changes; the review JSON and audit event share one transaction. Audit stores reviewer identity, decision, reason, version and an override-present boolean, **not** private text, source bytes or provider output.

## Data preservation and future matcher rule

- AI DNA remains immutable in its original `typography_region_json`, `font_mood_json`, `layout_graph_json`, `overlap_rules_json`, hash and analyzer version. Only its existing `manual_override_json` is updated; **no additional migration** is required beyond 0188/0189.
- `effective_reviewed_dna(reference, dna)` is the guarded reader for future G4 Style Lab/matcher: it returns a validated merged structure only when the manual status is accepted, its stored source hash/analyzer version are current, tenant/reference IDs match, rights remain verified and editorial status remains approved. Otherwise it returns `None`.
- A previously accepted review is shown as `needs_review / approval_invalidated` if rights or approval are later revoked, and no effective structure is returned. Marking a sample as near-duplicate or copy-risk can be done with fixed reason codes, without leaking original phrases.
- This review mechanism is **not** perceptual duplicate detection or a completed G3 pilot: automatic near-duplicate screening, live sample evidence, ROI quality comparisons, per-image cost, and eight human signoffs remain separate.

## Example requests

```json
{"decision":"accepted","reason_code":"approved"}
```

```json
{
  "decision":"accepted",
  "reason_code":"region_corrected",
  "override":{"typography_region":{"x":0.12,"y":0.20,"w":0.66,"h":0.49}}
}
```

```json
{"decision":"needs_review","reason_code":"near_duplicate"}
```

No request is made on production until explicit V2 tenant canary and G0.1 release safety gates are verified. Pilot operator must cross-check reviewed Style DNA against the eight legally authorized reference photos and record sanitized cost/latency.
