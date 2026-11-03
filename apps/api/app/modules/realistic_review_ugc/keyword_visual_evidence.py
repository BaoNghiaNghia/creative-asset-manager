"""Rights-gated, read-only G3 Typography DNA evidence for one keyword.

An approved, analyzed reference is only style evidence, never a verified
embroidery design or permission to copy its lettering. No AI calls or downloads.
"""
from __future__ import annotations

from itertools import combinations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.typography_analysis_queue import VERIFIED_RIGHTS
from app.modules.realistic_review_ugc.typography_dna_review import (
    REVIEW_SCHEMA, effective_reviewed_dna,
)
from app.modules.realistic_review_ugc.typography_model import (
    RrugcTypographyReferenceModel as Reference,
    RrugcTypographyStyleDnaModel as Dna,
)
from app.modules.realistic_review_ugc.typography_similarity import (
    has_stale_roi_hash, is_typography_dhash, typography_hamming_distance,
)

MAX_REFERENCES = 24
MATCH_DISTANCE = 6


def keyword_visual_evidence(session: Session, *, tenant_id: str, keyword_id: str, user_id: str = "") -> dict:
    empty = {
        "status": "no_approved_g3_reference", "analyzed_count": 0,
        "items": [], "similar_pairs": [],
        "candidate_image_checked": False, "originality_verified": False,
    }
    if not tenant_id or not keyword_id:
        return empty
    rows = session.execute(
        select(Reference, Dna)
        .join(Dna, and_(Dna.tenant_id == Reference.tenant_id,
                        Dna.reference_id == Reference.id,
                        Dna.image_hash == Reference.content_hash))
        .where(
            Reference.tenant_id == tenant_id,
            or_(
                Reference.privacy == "workspace",
                and_(Reference.privacy == "private", Reference.created_by_user_id == user_id),
            ),
            Reference.keyword_id == keyword_id,
            Reference.scope == "keyword",
            Reference.source_rights_status.in_(VERIFIED_RIGHTS),
            Reference.editorial_status == "approved",
            Reference.analysis_status == "ready",
            Dna.confidence >= .65,
            Dna.schema_version == "typography-dna/v1",
            # Approved source != accepted Style DNA; filter before LIMIT.
            Dna.manual_override_json["schema_version"].as_string() == REVIEW_SCHEMA,
            Dna.manual_override_json["decision"].as_string() == "accepted",
        )
        .order_by(Dna.analyzed_at.desc(), Dna.id.desc())
        .limit(MAX_REFERENCES)
    ).all()
    if not rows:
        return empty
    # One current visual DNA per reference (avoid double-counting versions).
    selected = {}
    for reference, dna in rows:
        selected.setdefault(reference.id, (reference, dna))
    items = []
    for reference, dna in selected.values():
        # Accepted human review can be invalidated by revoked rights, stale
        # source hashes or unsafe structural overrides; share G4's validator.
        reviewed = effective_reviewed_dna(reference, dna)
        if reviewed is None:
            continue
        graph = reviewed["layout_graph"]
        font = reviewed["font_mood"]
        hash_ok = is_typography_dhash(dna.perceptual_hash) and not has_stale_roi_hash(dna.manual_override_json)
        items.append({
            "reference_id": reference.id,
            "style_family": reviewed["style_family"],
            "font_mood": str(font.get("primary") or "")[:80],
            "layout_nodes": len(graph.get("nodes", [])) if isinstance(graph.get("nodes"), list) else 0,
            "layout_edges": len(graph.get("edges", [])) if isinstance(graph.get("edges"), list) else 0,
            "confidence": round(float(reviewed["confidence"]), 3),
            "perceptual_hash_available": bool(hash_ok),
            "fingerprint": dna.perceptual_hash if hash_ok else None,
        })
    if not items:
        return empty
    pairs = []
    for left, right in combinations(items, 2):
        if not (left["fingerprint"] and right["fingerprint"]):
            continue
        distance = typography_hamming_distance(left["fingerprint"], right["fingerprint"])
        if distance <= MATCH_DISTANCE:
            pairs.append({
                "left_reference_id": left["reference_id"],
                "right_reference_id": right["reference_id"],
                "distance": distance,
                "assessment": "similar_typography_roi",
            })
    pairs.sort(key=lambda item: (item["distance"], item["left_reference_id"]))
    for item in items:
        item.pop("fingerprint", None)
    return {
        "status": "approved_g3_references", "analyzed_count": len(items),
        "items": items, "similar_pairs": pairs[:12],
        "candidate_image_checked": False, "originality_verified": False,
    }
