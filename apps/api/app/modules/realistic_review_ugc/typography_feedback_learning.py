"""Zero-provider Typography Ideas feedback: local geometry and versioned Stage 1 style guidance.

Votes are explicit human decisions; image geometry is low-confidence, non-semantic
metadata. Never train model weights, extract logos/source lettering, copy pixel
layouts or forward user-authored tags/notes as generation instructions.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from hashlib import sha256
from io import BytesIO
import json

from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError
from sqlalchemy import and_, or_, select

from app.core.config import get_settings
from app.core.database import SessionLocal
from sqlalchemy.orm import Session

from app.domain.providers.contracts import OpenStoredAssetInput
from app.modules.realistic_review_ugc.model import RrugcReferenceAssetModel as Asset
from app.modules.realistic_review_ugc.typography_model import (
    RrugcTypographyReferenceModel as Reference,
    RrugcTypographyStyleDnaModel as Dna,
)
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider

ANALYZER_VERSION = "feedback-geometry-v1"
SCHEMA_VERSION = "typography-feedback-geometry/v1"
MAX_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 20_000_000
# Geometry-only: enum output. No names, texts, visual references or exact coordinates.
FEATURE_RULES = {
    "contrast:high": "Favor strong readable contrast between lettering and surrounding space.",
    "contrast:low": "Favor restrained, softly contrasted letter treatments without losing readability.",
    "density:open": "Favor generous negative space and uncluttered letter groups.",
    "density:busy": "Favor intricate lettering rhythms only where every letter stays readable.",
    "placement:centered": "Favor well-centered compact word groupings suited to a curved cap crown.",
    "placement:spread": "Favor balanced wider letter groups contained within the cap-front safe area.",
    "palette:muted": "Favor restrained, mostly muted thread color relationships.",
    "palette:vibrant": "Favor a deliberate vivid accent, rather than indiscriminate recoloring.",
}
NEGATIVE_RULES = {
    "contrast:high": "Avoid harsh, over-contrasted letter treatments that overwhelm the quote.",
    "contrast:low": "Avoid washed-out weak contrast that makes the phrase hard to read.",
    "density:open": "Avoid generic or excessively sparse typography lacking character.",
    "density:busy": "Avoid overcrowded lettering and overly complex ornamental detail.",
    "placement:centered": "Avoid stiff, generic centered letter compositions.",
    "placement:spread": "Avoid stretched, banner-like lettering that will not fit the cap front.",
    "palette:muted": "Avoid muddy or undifferentiated thread color combinations.",
    "palette:vibrant": "Avoid too many saturated thread colors competing for attention.",
}
# Prevent one noisy photo from controlling all styles; use only voted signals.
MAX_VOTED = 160
MAX_GUIDANCE = 1050


def analyze_geometry(raw: bytes) -> dict[str, str]:
    """Bounded deterministic thumbnail statistics, NOT font recognition or OCR."""
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("reference_size_invalid")
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"} or not (
                32 <= source.width <= 9000 and 32 <= source.height <= 9000
            ) or source.width * source.height > MAX_PIXELS:
                raise ValueError("reference_format_or_size_invalid")
            image = ImageOps.exif_transpose(source)
            image.thumbnail((256, 256), Image.Resampling.BILINEAR)
            if image.mode == "RGBA":
                base = Image.new("RGBA", image.size, (245, 245, 245, 255))
                base.alpha_composite(image)
                image = base.convert("RGB")
            else:
                image = image.convert("RGB")
            width, height = image.size
            # Broad central crop suppresses the backdrop but does not assume
            # any text detection. Every result is explicitly approximate.
            box = (width // 8, height // 8, width * 7 // 8, height * 7 // 8)
            crop = image.crop(box)
            gray = crop.convert("L")
            contrast = ImageStat.Stat(gray).stddev[0]
            mean_sat = ImageStat.Stat(crop.convert("HSV").getchannel("S")).mean[0]
            edge = gray.filter(ImageFilter.FIND_EDGES)
            edge_w, edge_h = edge.size
            sample = edge.tobytes()
            signal = [v > 56 for v in sample]
            density = sum(signal) / max(1, len(signal))
            mid_left = edge_w // 4
            mid_right = edge_w * 3 // 4
            center = sum(signal[y * edge_w + x] for y in range(edge_h)
                         for x in range(mid_left, mid_right))
            sides = sum(signal) - center
            return {
                "contrast": "high" if contrast >= 56 else "low",
                "density": "busy" if density >= 0.22 else "open",
                "placement": "centered" if center >= sides * .88 else "spread",
                "palette": "vibrant" if mean_sat >= 88 else "muted",
            }
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("reference_decode_failed") from exc


async def _source_bytes(storage, *, tenant: str, asset: Asset) -> bytes:
    result = await storage.open_asset(OpenStoredAssetInput(
        tenant_id=tenant, asset_id=asset.id,
        remote_file_id=asset.remote_file_id,
        content_type={"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[asset.image_format],
        size_bytes=asset.size_bytes,
    ))
    chunks = []
    total = 0
    try:
        async for part in result.body:
            total += len(part)
            if total > MAX_BYTES:
                raise ValueError("reference_size_invalid")
            chunks.append(part)
    finally:
        await result.close()
    return b"".join(chunks)


# One process-wide bounded pool; never calls a generative/paid provider.
_LOCAL_SLOTS = asyncio.Semaphore(2)


async def analyze_voted_reference(tenant_id: str, reference_id: str, source_hash: str) -> None:
    """One background attempt per vote, idempotent by tenant/reference/hash/version.

    Errors intentionally do not retry or trigger billable jobs; a later vote can
    retry local hints, while the human choice itself remains persisted.
    """
    from sqlalchemy.exc import IntegrityError

    async with _LOCAL_SLOTS:
        try:
            with SessionLocal() as session:
                ref = session.scalar(select(Reference).where(
                    Reference.tenant_id == tenant_id, Reference.id == reference_id,
                    Reference.content_hash == source_hash,
                    Reference.editorial_status != "archived",
                    Reference.source_rights_status != "blocked",
                    Reference.preference.in_(("positive", "negative")),
                ))
                if ref is None:
                    return
                existing = session.scalar(select(Dna.id).where(
                    Dna.tenant_id == tenant_id, Dna.reference_id == reference_id,
                    Dna.image_hash == source_hash, Dna.analyzer_version == ANALYZER_VERSION,
                ))
                if existing:
                    return
                asset = session.scalar(select(Asset).where(
                    Asset.tenant_id == tenant_id, Asset.id == ref.asset_id,
                    Asset.content_hash == source_hash, Asset.status == "ready",
                ))
                if (asset is None or not asset.remote_file_id
                        or asset.image_format not in {"PNG", "JPEG", "WEBP"}
                        or (asset.size_bytes or 0) > MAX_BYTES):
                    return
                # SQLAlchemy ORM instances detach at session exit; take primitives.
                asset_values = (asset.id, asset.remote_file_id, asset.image_format,
                                asset.size_bytes, asset.content_hash)
            storage = build_managed_storage_provider(get_settings())
            if isinstance(storage, UnconfiguredAssetStorageProvider):
                return
            snapshot = type("Stored", (), dict(zip(
                ("id", "remote_file_id", "image_format", "size_bytes", "content_hash"),
                asset_values,
            )))()
            raw = await _source_bytes(storage, tenant=tenant_id, asset=snapshot)
            if sha256(raw).hexdigest() != source_hash:
                return
            features = await asyncio.to_thread(analyze_geometry, raw)
            del raw
            with SessionLocal() as session:
                ref = session.scalar(select(Reference).where(
                    Reference.tenant_id == tenant_id, Reference.id == reference_id,
                    Reference.content_hash == source_hash,
                    Reference.preference.in_(("positive", "negative")),
                    Reference.editorial_status != "archived",
                    Reference.source_rights_status != "blocked",
                ).with_for_update())
                if ref is None or session.scalar(select(Dna.id).where(
                    Dna.tenant_id == tenant_id, Dna.reference_id == reference_id,
                    Dna.analyzer_version == ANALYZER_VERSION, Dna.image_hash == source_hash,
                )):
                    return
                session.add(Dna(
                    tenant_id=tenant_id, reference_id=reference_id,
                    image_hash=source_hash, analyzer_version=ANALYZER_VERSION,
                    schema_version=SCHEMA_VERSION, style_family="local_geometry_estimate",
                    typography_region_json={"estimated": True},
                    font_mood_json={"estimated": True},
                    layout_graph_json={"nodes": [], "edges": []},
                    overlap_rules_json=[], confidence=0.25,
                    distinctiveness_features_json=[
                        k + ":" + v for k, v in sorted(features.items())],
                    manual_override_json={"analysis_kind": "local_feedback", "features": features},
                ))
                # Local feedback hints are NOT G3 Style DNA. Never change
                # Reference.analysis_status: that field belongs to G3 jobs.
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()  # Concurrent vote already captured this hash.
        except Exception:
            # This optional, cost-free inference must never break the vote.
            return


def learned_reference_ids(
    session: Session, *, tenant_id: str, references: list[Reference],
) -> set[str]:
    """Single tenant/hash/version lookup for all visible references (no N+1)."""
    if not tenant_id or not references:
        return set()
    hashes = {ref.id: ref.content_hash for ref in references}
    rows = session.execute(select(Dna.reference_id, Dna.image_hash).where(
        Dna.tenant_id == tenant_id,
        Dna.reference_id.in_(list(hashes)),
        Dna.analyzer_version == ANALYZER_VERSION,
        Dna.schema_version == SCHEMA_VERSION,
    )).all()
    return {ref_id for ref_id, image_hash in rows if hashes.get(ref_id) == image_hash}


def reference_feedback_summary(
    session: Session, *, tenant_id: str, keyword_id: str, user_id: str = "",
) -> dict:
    """Read only voted tenant-visible references, capped and versioned.

    A private source contributes only for the user who selected it. All
    guidance is produced from our fixed vocabulary, never image/text content.
    """
    if not tenant_id or not keyword_id:
        return {"version": 2, "votes": 0, "hints": 0, "guidance": "", "digest": ""}
    refs = list(session.scalars(select(Reference).where(
        Reference.tenant_id == tenant_id,
        Reference.editorial_status != "archived",
        Reference.source_rights_status != "blocked",
        Reference.preference.in_(("positive", "negative")),
        or_(
            Reference.privacy == "workspace",
            Reference.created_by_user_id == user_id,
            Reference.reviewed_by_user_id == user_id,
        ),
        or_(
            Reference.scope == "workspace",
            and_(Reference.scope == "keyword", Reference.keyword_id == keyword_id),
            and_(Reference.scope == "skill", Reference.skill_name == "creative-brief-v3"),
        ),
    ).order_by(Reference.updated_at.desc(), Reference.id.desc()).limit(MAX_VOTED)))
    counter = Counter()
    rows = session.scalars(select(Dna).where(
        Dna.tenant_id == tenant_id,
        Dna.reference_id.in_([ref.id for ref in refs]),
        Dna.analyzer_version == ANALYZER_VERSION,
        Dna.schema_version == SCHEMA_VERSION,
    )).all() if refs else []
    by_reference = {dna.reference_id: dna for dna in rows}
    for ref in refs:
        dna = by_reference.get(ref.id)
        if dna is None or dna.image_hash != ref.content_hash:
            continue
        payload = dna.manual_override_json or {}
        features = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(features, dict):
            continue
        for name, value in features.items():
            key = str(name) + ":" + str(value)
            if key in FEATURE_RULES:
                counter[(ref.preference, key)] += 1
    # Use vote totals and enum counts, never IDs, raw images, user tags or notes.
    samples = sorted((direction, key, count) for (direction, key), count in counter.items())
    positives = sorted(
        ((key, n) for (direction, key), n in counter.items() if direction == "positive"),
        key=lambda item: (-item[1], item[0]),
    )
    negatives = sorted(
        ((key, n) for (direction, key), n in counter.items() if direction == "negative"),
        key=lambda item: (-item[1], item[0]),
    )
    # A vote should influence the very next batch, but one example cannot
    # establish a universal rule. Apply it softly; when picks/rejects disagree
    # on the same feature, do not emit contradictory instructions.
    lines = ["REFERENCE PREFERENCE LEARNING (approximate color/geometry only; never copy reference pixels):"]
    emitted = 0
    for label, values in (("PICKED", positives), ("REJECTED", negatives)):
        if not values:
            continue
        other_direction = "negative" if label == "PICKED" else "positive"
        label_emitted = 0
        candidates = sorted(
            values, key=lambda item: (
                -(item[1] - counter[(other_direction, item[0])]),
                {"density": 0, "placement": 1, "contrast": 2, "palette": 3}.get(
                    item[0].split(":")[0], 9,
                ),
                item[0],
            ),
        )
        for key, count in candidates:
            if label_emitted >= 2 or emitted >= 4 or count <= counter[(other_direction, key)]:
                continue
            # At most two independent hints per direction.
            if count == 1 and label_emitted >= 2:
                continue
            rule = FEATURE_RULES[key] if label == "PICKED" else NEGATIVE_RULES[key]
            strength = (
                f"{label} signal from {count} voted references"
                if count >= 2 else
                f"{label} soft hint from one voted reference (tie-breaker only)"
            )
            lines.append(f"{strength}: {rule}")
            emitted += 1
            label_emitted += 1
    if not emitted and refs:
        lines.append("Votes conflict or remain too uncertain; preserve diverse original lettering.")
    lines.append("These are weak aggregate preferences, not font identification or permission to reuse logos, words or glyph outlines.")
    guidance = "\n".join(lines)[:MAX_GUIDANCE] if refs else ""
    stable = {"version": 2, "votes": len(refs), "signals": samples, "guidance": guidance}
    digest = sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    return {"version": 2, "votes": len(refs),
            "hints": sum(n for _, _, n in samples),
            "guidance": guidance, "digest": digest}
