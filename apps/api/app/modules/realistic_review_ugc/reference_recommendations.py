from __future__ import annotations

import re
from dataclasses import dataclass

from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcReferenceAssetModel,
)
from app.providers.ai.codex_image import CodexSkillManifest


_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class ReferenceRecommendation:
    role: str
    required: bool
    reference_asset: RrugcReferenceAssetModel | None
    score: float | None
    reasons: tuple[str, ...]
    candidate_count: int


def preferred_reference_types(role: str) -> tuple[str, ...]:
    token = str(role or "").strip().lower()
    if "artwork" in token or "logo" in token:
        return ("artwork", "detail", "product")
    if "detail" in token:
        return ("detail", "product", "artwork")
    if any(part in token for part in ("product", "front", "side", "back")):
        return ("product", "detail", "artwork")
    if "scene" in token:
        return ("scene", "person", "other")
    if "person" in token:
        return ("person", "scene", "other")
    return ()


def _tokens(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        result: set[str] = set()
        for item in value:
            result.update(_tokens(item))
        return result
    return set(_TOKEN_RE.findall(str(value).lower()))


def _role_tokens(role: str) -> set[str]:
    tokens = _tokens(role)
    if "front" in tokens:
        tokens.update({"frontal", "frontview"})
    if "side" in tokens:
        tokens.update({"profile", "sideview"})
    if "back" in tokens:
        tokens.update({"rear", "backview"})
    if "artwork" in tokens or "logo" in tokens:
        tokens.update({"artwork", "logo", "design", "embroidery", "embroidered"})
    if "detail" in tokens:
        tokens.update({"detail", "closeup", "close", "embroidery", "embroidered"})
    if "person" in tokens:
        tokens.update({"person", "model", "wearer", "lifestyle"})
    if "scene" in tokens:
        tokens.update({"scene", "setting", "context", "lifestyle"})
    return tokens


def _campaign_product_tokens(campaign: RrugcCampaignModel) -> set[str]:
    snapshot = dict(campaign.product_snapshot_json or {})
    return _tokens(
        [
            snapshot.get("product_type"),
            snapshot.get("sku"),
            snapshot.get("product_sku"),
            snapshot.get("name"),
            snapshot.get("product_name"),
            snapshot.get("title"),
            snapshot.get("brand"),
        ]
    )


def _campaign_context_tokens(campaign: RrugcCampaignModel) -> set[str]:
    context = dict(campaign.product_context_json or {})
    visual = dict(context.get("visual_context") or {})
    return _tokens(
        [
            context.get("themes"),
            context.get("operator_themes"),
            context.get("detected_themes"),
            context.get("preferred_scenes"),
            visual.get("themes"),
            visual.get("scene_hints"),
            visual.get("audience_hints"),
            visual.get("occasion_hints"),
            visual.get("product_cues"),
        ]
    )


def _asset_tokens(asset: RrugcReferenceAssetModel) -> set[str]:
    return _tokens(
        [
            asset.original_filename,
            asset.source_key,
            asset.profile_key,
            asset.tags_json or [],
            asset.themes_json or [],
        ]
    )


def _bounded_signal(value: float | None, weight: float) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(1.0, float(value))) * weight


def _score_asset(
    *,
    campaign: RrugcCampaignModel,
    asset: RrugcReferenceAssetModel,
    role: str,
    preferred_types: tuple[str, ...],
    product_tokens: set[str],
    context_tokens: set[str],
) -> tuple[float, tuple[str, ...]]:
    reasons: list[str] = []
    score = 0.0

    if preferred_types:
        type_rank = preferred_types.index(asset.reference_type)
        # Keep role/type compatibility lexicographically dominant. The maximum
        # combined metadata/context/quality bonus is below the 100-point gap.
        score += (300.0, 200.0, 100.0)[min(type_rank, 2)]
        reasons.append("Type match: " + asset.reference_type)

    metadata_tokens = _asset_tokens(asset)
    role_overlap = sorted(_role_tokens(role) & metadata_tokens)
    if role_overlap:
        score += min(24.0, len(role_overlap) * 12.0)
        reasons.append("Role metadata: " + ", ".join(role_overlap[:3]))

    if asset.source_campaign_id == campaign.id:
        score += 25.0
        reasons.append("Same campaign")

    product_overlap = sorted(product_tokens & metadata_tokens)
    if product_overlap:
        score += min(16.0, len(product_overlap) * 4.0)
        reasons.append("Product match: " + ", ".join(product_overlap[:3]))

    context_overlap = sorted(context_tokens & metadata_tokens)
    if context_overlap:
        score += min(15.0, len(context_overlap) * 3.0)
        reasons.append("Context match: " + ", ".join(context_overlap[:3]))

    quality_signal = _bounded_signal(asset.quality_score, 10.0)
    if quality_signal:
        score += quality_signal
        reasons.append("Quality: " + str(round(float(asset.quality_score or 0.0) * 100)) + "%")
    score += _bounded_signal(asset.context_score, 5.0)
    score += _bounded_signal(asset.visual_score, 3.0)

    return round(score, 4), tuple(reasons)


def suggested_reference_set_name(
    campaign: RrugcCampaignModel,
    manifest: CodexSkillManifest,
) -> str:
    snapshot = dict(campaign.product_snapshot_json or {})
    subject = str(
        snapshot.get("sku")
        or snapshot.get("product_sku")
        or snapshot.get("name")
        or snapshot.get("product_name")
        or campaign.name
        or "Campaign"
    ).strip()
    return (subject + " · " + manifest.display_name)[:200]


def recommend_reference_assets(
    *,
    campaign: RrugcCampaignModel,
    manifest: CodexSkillManifest,
    assets: list[RrugcReferenceAssetModel],
) -> list[ReferenceRecommendation]:
    product_tokens = _campaign_product_tokens(campaign)
    context_tokens = _campaign_context_tokens(campaign)
    used_asset_ids: set[str] = set()
    recommendations: list[ReferenceRecommendation] = []

    role_specs = [
        *((role, True) for role in manifest.required_reference_roles),
        *((role, False) for role in manifest.optional_reference_roles),
    ]
    for role, required in role_specs:
        preferred_types = preferred_reference_types(role)
        compatible = [
            asset
            for asset in assets
            if asset.status == "ready"
            and asset.archived_at is None
            and (not preferred_types or asset.reference_type in preferred_types)
        ]
        ranked: list[tuple[float, RrugcReferenceAssetModel, tuple[str, ...]]] = []
        for asset in compatible:
            score, reasons = _score_asset(
                campaign=campaign,
                asset=asset,
                role=role,
                preferred_types=preferred_types,
                product_tokens=product_tokens,
                context_tokens=context_tokens,
            )
            ranked.append((score, asset, reasons))
        ranked.sort(
            key=lambda row: (
                -row[0],
                -(row[1].updated_at.timestamp() if row[1].updated_at else 0.0),
                row[1].id,
            )
        )
        selected = next(
            (row for row in ranked if row[1].id not in used_asset_ids),
            None,
        )
        if selected is None:
            recommendations.append(
                ReferenceRecommendation(
                    role=role,
                    required=required,
                    reference_asset=None,
                    score=None,
                    reasons=(),
                    candidate_count=len(compatible),
                )
            )
            continue
        score, asset, reasons = selected
        used_asset_ids.add(asset.id)
        recommendations.append(
            ReferenceRecommendation(
                role=role,
                required=required,
                reference_asset=asset,
                score=score,
                reasons=reasons,
                candidate_count=len(compatible),
            )
        )
    return recommendations
