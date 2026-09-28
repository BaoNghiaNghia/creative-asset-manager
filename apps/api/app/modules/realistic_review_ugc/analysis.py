from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.providers.contracts import AiMetadataAnalysisInput, AiMetadataProvider


ANALYZER_VERSION = "rrugc-reference-v3-headwear-optional"
QUALITY_FIRST_MAX_AI_RISK = 0.15
QUALITY_FIRST_MIN_QUALITY = 0.60
QUALITY_FIRST_MIN_UGC = 0.65


class ReferenceAnalysisDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    people_count: int = Field(ge=0, le=12)
    primary_head_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    smile_score: float = Field(ge=0.0, le=1.0)
    head_visible: bool
    existing_headwear: bool
    head_occlusion: float = Field(ge=0.0, le=1.0)
    mobile_ugc_score: float = Field(ge=0.0, le=1.0)
    quality_score: float = Field(ge=0.0, le=1.0)
    ai_risk_score: float = Field(ge=0.0, le=1.0)
    product_fit_score: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1, max_length=500)


@dataclass(frozen=True, slots=True)
class ReferenceFilterPolicy:
    min_head_ratio: float = 0.20
    max_head_ratio: float = 0.45
    min_smile_score: float = 0.65
    max_head_occlusion: float = 0.25
    max_ai_risk_score: float = QUALITY_FIRST_MAX_AI_RISK
    min_quality_score: float = QUALITY_FIRST_MIN_QUALITY
    min_ugc_score: float = QUALITY_FIRST_MIN_UGC
    min_product_fit_score: float = 0.55
    require_head_visible: bool = True
    reject_headwear: bool = False


@dataclass(frozen=True, slots=True)
class ReferenceDecision:
    status: str
    reject_reason: str | None
    final_score: float

    @property
    def approved(self) -> bool:
        return self.status == "approved"


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def evaluate_reference(
    document: ReferenceAnalysisDocument,
    policy: ReferenceFilterPolicy,
) -> ReferenceDecision:
    final_score = round(_bounded(
        0.15 * document.smile_score
        + 0.25 * document.mobile_ugc_score
        + 0.20 * document.quality_score
        + 0.20 * document.product_fit_score
        + 0.20 * (1.0 - document.ai_risk_score)
    ), 4)

    if document.people_count < 1:
        return ReferenceDecision("rejected_no_person", "NO_PERSON", final_score)
    if policy.require_head_visible and not document.head_visible:
        return ReferenceDecision("rejected_head_occlusion", "HEAD_NOT_VISIBLE", final_score)
    if document.primary_head_ratio is None:
        return ReferenceDecision("rejected_head_ratio", "HEAD_RATIO_UNAVAILABLE", final_score)
    if not policy.min_head_ratio <= document.primary_head_ratio <= policy.max_head_ratio:
        return ReferenceDecision("rejected_head_ratio", "HEAD_RATIO_OUT_OF_RANGE", final_score)
    if policy.reject_headwear and document.existing_headwear:
        return ReferenceDecision("rejected_existing_headwear", "EXISTING_HEADWEAR", final_score)
    if document.head_occlusion > policy.max_head_occlusion:
        return ReferenceDecision("rejected_head_occlusion", "HEAD_OCCLUSION", final_score)
    if document.ai_risk_score > policy.max_ai_risk_score:
        return ReferenceDecision("rejected_ai_risk", "AI_RISK_HIGH", final_score)
    if document.quality_score < policy.min_quality_score:
        return ReferenceDecision("rejected_quality", "QUALITY_SCORE_LOW", final_score)
    if document.mobile_ugc_score < policy.min_ugc_score:
        return ReferenceDecision("rejected_context", "UGC_SCORE_LOW", final_score)
    if document.smile_score < policy.min_smile_score:
        return ReferenceDecision("rejected_expression", "SMILE_SCORE_LOW", final_score)
    if document.product_fit_score < policy.min_product_fit_score:
        return ReferenceDecision("rejected_context", "PRODUCT_FIT_LOW", final_score)
    return ReferenceDecision("approved", None, final_score)


def analysis_prompt() -> str:
    return """
You are evaluating a Pinterest lifestyle reference for a quality-first real-photo workflow.
Return exactly one JSON object and no prose.

The workflow wants authentic camera photographs of real scenes and should reject synthetic,
AI-generated, illustrated, CGI, rendered, or heavily artificial-looking images even when they
are photorealistic. Be conservative when synthetic cues are present.

Do not identify the person and do not infer protected or demographic attributes.
Only evaluate visible composition and image suitability.

Definitions:
- people_count: number of visibly present people.
- primary_head_ratio: height of the primary visible head bounding region divided by full image height, in 0..1. Use null when there is no usable visible head.
- smile_score: 0..1 strength of a clearly positive/smiling visible expression.
- head_visible: true only if the primary head is sufficiently visible for adding or replacing a hat.
- existing_headwear: true when the primary person is already wearing any hat/cap/helmet/head covering. Existing headwear is allowed unless the campaign explicitly rejects it.
- head_occlusion: 0..1 fraction/severity of the primary head obscured by crops, objects, hands, other people, heavy hair coverage, or frame edges.
- mobile_ugc_score: 0..1 likelihood the composition feels like a casual, candid, handheld/smartphone-style real-life photo rather than a polished studio/ad pose.
- quality_score: 0..1 technical usefulness: adequate resolution impression, focus, lighting, and visible facial/head details.
- ai_risk_score: 0..1 visual-risk estimate that the image may be synthetic rather than a camera photograph. Raise this score for photorealistic AI/render cues such as inconsistent hands/fingers/teeth, malformed accessories or text, impossible reflections/geometry, repeated textures, waxy or over-smoothed skin, implausible hair/background transitions, unnatural bokeh, or lighting/material inconsistencies. When uncertain between a real photograph and a synthetic image, use a conservative higher risk score. This is a risk signal, not proof.
- product_fit_score: 0..1 suitability for adding a hat to a bare head or naturally replacing existing headwear while preserving the person, pose, background, camera and lighting.
- summary: concise factual explanation of the visible composition and main suitability issue, max 2 sentences.

Required JSON keys:
people_count, primary_head_ratio, smile_score, head_visible, existing_headwear,
head_occlusion, mobile_ugc_score, quality_score, ai_risk_score,
product_fit_score, summary.
""".strip()


async def analyze_reference_image(
    *,
    provider: AiMetadataProvider,
    tenant_id: str,
    candidate_id: str,
    image_bytes: bytes,
    image_mime_type: str,
    width: int,
    height: int,
) -> tuple[ReferenceAnalysisDocument, str, str | None]:
    result = await provider.analyze_single(
        AiMetadataAnalysisInput(
            tenant_id=tenant_id,
            asset_id=candidate_id,
            prompt=analysis_prompt(),
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
            metadata_profile="rrugc_reference",
            metadata_profile_version=ANALYZER_VERSION,
            image_width=width,
            image_height=height,
            json_schema=ReferenceAnalysisDocument.model_json_schema(),
            analysis_id=candidate_id,
        )
    )
    document = ReferenceAnalysisDocument.model_validate(dict(result.metadata))
    return document, result.provider, result.model


def policy_from_campaign(campaign: Any) -> ReferenceFilterPolicy:
    return ReferenceFilterPolicy(
        min_head_ratio=float(campaign.min_head_ratio),
        max_head_ratio=float(campaign.max_head_ratio),
        min_smile_score=float(campaign.min_smile_score),
        max_head_occlusion=float(campaign.max_head_occlusion),
        max_ai_risk_score=min(
            float(campaign.max_ai_risk_score),
            QUALITY_FIRST_MAX_AI_RISK,
        ),
        min_quality_score=max(
            float(campaign.min_quality_score),
            QUALITY_FIRST_MIN_QUALITY,
        ),
        min_ugc_score=max(
            float(campaign.min_ugc_score),
            QUALITY_FIRST_MIN_UGC,
        ),
        min_product_fit_score=float(campaign.min_product_fit_score),
        require_head_visible=bool(campaign.require_head_visible),
        reject_headwear=bool(campaign.reject_headwear),
    )
