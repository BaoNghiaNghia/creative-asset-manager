from __future__ import annotations

import re
from dataclasses import dataclass

from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcGenerationAttemptModel,
    RrugcReferenceAssetModel,
    RrugcReferenceSetItemModel,
    RrugcReferenceSetModel,
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
    learning_adjustment: float = 0.0
    review_approved_count: int = 0
    review_rejected_count: int = 0


@dataclass(frozen=True, slots=True)
class ReferenceReviewStats:
    approved: int = 0
    rejected: int = 0

    @property
    def total(self) -> int:
        return self.approved + self.rejected


@dataclass(frozen=True, slots=True)
class ReferenceReviewLearning:
    review_count: int
    exact: dict[tuple[str, str], ReferenceReviewStats]
    family: dict[tuple[str, str, str], ReferenceReviewStats]
    reference_sets: dict[tuple[str, str], ReferenceReviewStats]


@dataclass(frozen=True, slots=True)
class ReferenceSetReuseRecommendation:
    reference_set: RrugcReferenceSetModel | None
    score: float | None
    reasons: tuple[str, ...]
    candidate_count: int
    review_approved_count: int = 0
    review_rejected_count: int = 0


@dataclass(frozen=True, slots=True)
class ReferenceSetFeedback:
    reference_set: RrugcReferenceSetModel
    score: float
    reasons: tuple[str, ...]
    review_approved_count: int
    review_rejected_count: int


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


def _product_type(snapshot: dict | None) -> str:
    data = dict(snapshot or {})
    return str(
        data.get("product_type")
        or data.get("type")
        or data.get("category")
        or ""
    ).strip().lower()


def _reference_binding_signature(
    bindings: list[tuple[str, str]],
) -> str:
    normalized = sorted(
        {
            (
                str(role or "").strip().lower(),
                str(reference_asset_id or "").strip(),
            )
            for role, reference_asset_id in bindings
            if str(role or "").strip() and str(reference_asset_id or "").strip()
        }
    )
    return "|".join(
        role + ":" + reference_asset_id
        for role, reference_asset_id in normalized
    )


def _snapshot_reference_set_signature(references: list[dict]) -> str:
    return _reference_binding_signature([
        (
            str(item.get("role") or ""),
            str(item.get("reference_asset_id") or ""),
        )
        for item in references
    ])


def _current_reference_set_signature(
    items: list[RrugcReferenceSetItemModel],
) -> str:
    return _reference_binding_signature([
        (item.role, item.reference_asset_id)
        for item in items
    ])


def _review_adjustment(
    stats: ReferenceReviewStats | None,
    *,
    max_weight: float,
    min_samples: int,
    full_confidence_samples: int,
) -> float:
    if stats is None or stats.total < min_samples:
        return 0.0
    posterior_approved = (stats.approved + 2.0) / (stats.total + 4.0)
    signed_preference = (posterior_approved - 0.5) * 2.0
    confidence = min(1.0, stats.total / float(full_confidence_samples))
    return round(signed_preference * max_weight * confidence, 4)


def build_reference_review_learning(
    *,
    campaign: RrugcCampaignModel,
    attempts: list[RrugcGenerationAttemptModel],
) -> ReferenceReviewLearning:
    target_product_type = _product_type(campaign.product_snapshot_json)
    exact_counts: dict[tuple[str, str], list[int]] = {}
    family_counts: dict[tuple[str, str, str], list[int]] = {}
    reference_set_counts: dict[tuple[str, str], list[int]] = {}
    review_count = 0

    for attempt in attempts:
        if attempt.review_status not in {"approved", "rejected"}:
            continue
        attempt_product_type = _product_type(attempt.product_snapshot_json)
        if target_product_type:
            if not attempt_product_type or attempt_product_type != target_product_type:
                continue

        references = [
            item
            for item in (attempt.product_reference_snapshot_json or [])
            if isinstance(item, dict)
        ]
        if not references:
            continue
        review_count += 1
        approved_index = 0 if attempt.review_status == "approved" else 1
        reference_set_ids = {
            str(item.get("reference_set_id") or "").strip()
            for item in references
            if str(item.get("reference_set_id") or "").strip()
        }
        if len(reference_set_ids) == 1:
            reference_set_id = next(iter(reference_set_ids))
            reference_set_signature = _snapshot_reference_set_signature(references)
            if reference_set_signature:
                counts = reference_set_counts.setdefault(
                    (reference_set_id, reference_set_signature),
                    [0, 0],
                )
                counts[approved_index] += 1

        seen_exact: set[tuple[str, str]] = set()
        seen_family: set[tuple[str, str, str]] = set()

        for item in references:
            asset_id = str(item.get("reference_asset_id") or "").strip()
            role = str(item.get("role") or "").strip().lower()
            reference_type = str(item.get("reference_type") or "").strip().lower()
            source_type = str(item.get("source_type") or "").strip().lower()
            if asset_id and role:
                exact_key = (asset_id, role)
                if exact_key not in seen_exact:
                    counts = exact_counts.setdefault(exact_key, [0, 0])
                    counts[approved_index] += 1
                    seen_exact.add(exact_key)
            if role and reference_type and source_type:
                family_key = (role, reference_type, source_type)
                if family_key not in seen_family:
                    counts = family_counts.setdefault(family_key, [0, 0])
                    counts[approved_index] += 1
                    seen_family.add(family_key)

    return ReferenceReviewLearning(
        review_count=review_count,
        exact={
            key: ReferenceReviewStats(approved=value[0], rejected=value[1])
            for key, value in exact_counts.items()
        },
        family={
            key: ReferenceReviewStats(approved=value[0], rejected=value[1])
            for key, value in family_counts.items()
        },
        reference_sets={
            key: ReferenceReviewStats(approved=value[0], rejected=value[1])
            for key, value in reference_set_counts.items()
        },
    )


def _reference_set_manifest_compatible(
    *,
    campaign: RrugcCampaignModel,
    manifest: CodexSkillManifest,
    reference_set: RrugcReferenceSetModel,
    items: list[RrugcReferenceSetItemModel],
) -> bool:
    if reference_set.status != "active" or reference_set.archived_at is not None:
        return False
    if reference_set.campaign_id and reference_set.campaign_id != campaign.id:
        return False
    if not items or len(items) > manifest.max_references:
        return False
    roles = {str(item.role or "").strip().lower() for item in items}
    return all(
        str(role or "").strip().lower() in roles
        for role in manifest.required_reference_roles
    )


def _reference_set_review_signal(
    *,
    reference_set: RrugcReferenceSetModel,
    items: list[RrugcReferenceSetItemModel],
    review_learning: ReferenceReviewLearning,
) -> tuple[ReferenceReviewStats | None, float]:
    signature = _current_reference_set_signature(items)
    stats = review_learning.reference_sets.get(
        (reference_set.id, signature)
    ) if signature else None
    score = _review_adjustment(
        stats,
        max_weight=100.0,
        min_samples=3,
        full_confidence_samples=8,
    )
    return stats, score


def recommend_reference_set_reuse(
    *,
    campaign: RrugcCampaignModel,
    manifest: CodexSkillManifest,
    reference_sets: list[
        tuple[RrugcReferenceSetModel, list[RrugcReferenceSetItemModel]]
    ],
    review_learning: ReferenceReviewLearning,
) -> ReferenceSetReuseRecommendation:
    compatible = [
        (reference_set, items)
        for reference_set, items in reference_sets
        if _reference_set_manifest_compatible(
            campaign=campaign,
            manifest=manifest,
            reference_set=reference_set,
            items=items,
        )
    ]
    ranked: list[
        tuple[float, RrugcReferenceSetModel, ReferenceReviewStats]
    ] = []
    for reference_set, items in compatible:
        stats, score = _reference_set_review_signal(
            reference_set=reference_set,
            items=items,
            review_learning=review_learning,
        )
        if stats is None or stats.total < 3 or score <= 0:
            continue
        ranked.append((score, reference_set, stats))

    ranked.sort(
        key=lambda row: (
            -row[0],
            -row[2].approved,
            row[2].rejected,
            0 if row[1].campaign_id == campaign.id else 1,
            -(row[1].updated_at.timestamp() if row[1].updated_at else 0.0),
            row[1].id,
        )
    )
    if not ranked:
        return ReferenceSetReuseRecommendation(
            reference_set=None,
            score=None,
            reasons=(),
            candidate_count=len(compatible),
        )

    score, reference_set, stats = ranked[0]
    reasons = [
        f"Human review: {stats.approved} approved / {stats.rejected} rejected",
        f"Evidence: {stats.total} reviewed generations",
    ]
    if reference_set.campaign_id == campaign.id:
        reasons.append("Same campaign")
    else:
        reasons.append("Reusable global set")
    return ReferenceSetReuseRecommendation(
        reference_set=reference_set,
        score=score,
        reasons=tuple(reasons),
        candidate_count=len(compatible),
        review_approved_count=stats.approved,
        review_rejected_count=stats.rejected,
    )


def discouraged_reference_sets(
    *,
    campaign: RrugcCampaignModel,
    manifest: CodexSkillManifest,
    reference_sets: list[
        tuple[RrugcReferenceSetModel, list[RrugcReferenceSetItemModel]]
    ],
    review_learning: ReferenceReviewLearning,
) -> tuple[ReferenceSetFeedback, ...]:
    rows: list[ReferenceSetFeedback] = []
    for reference_set, items in reference_sets:
        if not _reference_set_manifest_compatible(
            campaign=campaign,
            manifest=manifest,
            reference_set=reference_set,
            items=items,
        ):
            continue
        stats, score = _reference_set_review_signal(
            reference_set=reference_set,
            items=items,
            review_learning=review_learning,
        )
        if stats is None or stats.total < 3 or score >= 0:
            continue
        rows.append(
            ReferenceSetFeedback(
                reference_set=reference_set,
                score=score,
                reasons=(
                    f"Human review: {stats.approved} approved / {stats.rejected} rejected",
                    f"Evidence: {stats.total} reviewed generations",
                ),
                review_approved_count=stats.approved,
                review_rejected_count=stats.rejected,
            )
        )
    rows.sort(
        key=lambda row: (
            row.score,
            -row.review_rejected_count,
            row.review_approved_count,
            row.reference_set.id,
        )
    )
    return tuple(rows)


def _learning_signal(
    *,
    learning: ReferenceReviewLearning | None,
    asset: RrugcReferenceAssetModel,
    role: str,
) -> tuple[float, tuple[str, ...], ReferenceReviewStats]:
    if learning is None:
        return 0.0, (), ReferenceReviewStats()

    normalized_role = str(role or "").strip().lower()
    exact = learning.exact.get((asset.id, normalized_role))
    family = learning.family.get(
        (
            normalized_role,
            str(asset.reference_type or "").strip().lower(),
            str(asset.source_type or "").strip().lower(),
        )
    )
    exact_adjustment = _review_adjustment(
        exact,
        max_weight=18.0,
        min_samples=2,
        full_confidence_samples=6,
    )
    family_adjustment = _review_adjustment(
        family,
        max_weight=6.0,
        min_samples=4,
        full_confidence_samples=10,
    )
    adjustment = round(exact_adjustment + family_adjustment, 4)
    reasons: list[str] = []
    exact_stats = exact or ReferenceReviewStats()
    if exact is not None and exact.total >= 2:
        reasons.append(
            "Human review: "
            + str(exact.approved)
            + " approved / "
            + str(exact.rejected)
            + " rejected"
        )
    if family is not None and family.total >= 4:
        reasons.append(
            "Reference family: "
            + str(family.approved)
            + " approved / "
            + str(family.rejected)
            + " rejected"
        )
    return adjustment, tuple(reasons), exact_stats


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
    review_learning: ReferenceReviewLearning | None = None,
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
        ranked: list[
            tuple[
                float,
                RrugcReferenceAssetModel,
                tuple[str, ...],
                float,
                ReferenceReviewStats,
            ]
        ] = []
        for asset in compatible:
            base_score, base_reasons = _score_asset(
                campaign=campaign,
                asset=asset,
                role=role,
                preferred_types=preferred_types,
                product_tokens=product_tokens,
                context_tokens=context_tokens,
            )
            learning_adjustment, learning_reasons, exact_stats = _learning_signal(
                learning=review_learning,
                asset=asset,
                role=role,
            )
            score = round(base_score + learning_adjustment, 4)
            ranked.append(
                (
                    score,
                    asset,
                    base_reasons + learning_reasons,
                    learning_adjustment,
                    exact_stats,
                )
            )
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
        score, asset, reasons, learning_adjustment, exact_stats = selected
        used_asset_ids.add(asset.id)
        recommendations.append(
            ReferenceRecommendation(
                role=role,
                required=required,
                reference_asset=asset,
                score=score,
                reasons=reasons,
                candidate_count=len(compatible),
                learning_adjustment=learning_adjustment,
                review_approved_count=exact_stats.approved,
                review_rejected_count=exact_stats.rejected,
            )
        )
    return recommendations
