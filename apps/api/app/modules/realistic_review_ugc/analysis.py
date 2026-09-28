from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from app.domain.providers.contracts import AiMetadataAnalysisInput, AiMetadataProvider


ANALYZER_VERSION = "rrugc-reference-v4-authenticity-ensemble"
QUALITY_FIRST_MAX_AI_RISK = 0.15
QUALITY_FIRST_MIN_QUALITY = 0.60
QUALITY_FIRST_MIN_UGC = 0.65
AI_CONFIRMATION_TRIGGER = 0.30
AI_STRONG_CUE_THRESHOLD = 0.60
AI_CONFIRMATION_SYNTHETIC_THRESHOLD = 0.60
AI_MIN_CONFIRMATION_CONFIDENCE = 0.55
AI_CALIBRATION_MIN_PER_CLASS = 5


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
    ai_anatomy_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    ai_text_symbol_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    ai_geometry_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    ai_texture_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    ai_lighting_reflection_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    ai_background_consistency_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    ai_detector_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    product_fit_score: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1, max_length=500)


class AiAuthenticityConfirmationDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    camera_photo_probability: float = Field(ge=0.0, le=1.0)
    synthetic_probability: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    anatomy_risk: float = Field(ge=0.0, le=1.0)
    text_symbol_risk: float = Field(ge=0.0, le=1.0)
    geometry_risk: float = Field(ge=0.0, le=1.0)
    texture_risk: float = Field(ge=0.0, le=1.0)
    lighting_reflection_risk: float = Field(ge=0.0, le=1.0)
    background_consistency_risk: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list, max_length=6)
    summary: str = Field(min_length=1, max_length=400)


@dataclass(frozen=True, slots=True)
class AiRiskCalibration:
    real_count: int = 0
    ai_count: int = 0
    real_mean: float | None = None
    ai_mean: float | None = None

    @property
    def active(self) -> bool:
        return (
            self.real_count >= AI_CALIBRATION_MIN_PER_CLASS
            and self.ai_count >= AI_CALIBRATION_MIN_PER_CLASS
            and self.real_mean is not None
            and self.ai_mean is not None
            and self.ai_mean >= self.real_mean + 0.10
        )


@dataclass(frozen=True, slots=True)
class AiRiskAssessment:
    raw_score: float
    calibrated_score: float
    detector_confidence: float
    strong_evidence_count: int
    confirmed: bool
    signal_json: dict[str, Any]
    calibration_applied: bool


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


def ai_visual_cues(document: ReferenceAnalysisDocument) -> dict[str, float]:
    return {
        "anatomy": document.ai_anatomy_risk,
        "text_symbol": document.ai_text_symbol_risk,
        "geometry": document.ai_geometry_risk,
        "texture": document.ai_texture_risk,
        "lighting_reflection": document.ai_lighting_reflection_risk,
        "background_consistency": document.ai_background_consistency_risk,
    }


def raw_ai_risk(document: ReferenceAnalysisDocument) -> tuple[float, int]:
    cues = ai_visual_cues(document)
    values = sorted(cues.values(), reverse=True)
    top_three = values[:3]
    evidence_mean = sum(top_three) / max(1, len(top_three))
    peak = values[0] if values else 0.0
    raw = _bounded(
        0.45 * document.ai_risk_score
        + 0.35 * evidence_mean
        + 0.20 * peak
    )
    # Low-confidence model output should not be allowed to create a strong
    # synthetic verdict on its own.
    confidence_factor = 0.65 + 0.35 * document.ai_detector_confidence
    raw = _bounded(raw * confidence_factor)
    strong_count = sum(value >= AI_STRONG_CUE_THRESHOLD for value in values)
    return round(raw, 4), strong_count


def build_ai_risk_calibration(
    rows: Iterable[tuple[str, float | None]],
) -> AiRiskCalibration:
    real = [
        float(score)
        for label, score in rows
        if label == "real" and score is not None
    ]
    ai = [
        float(score)
        for label, score in rows
        if label == "ai" and score is not None
    ]
    return AiRiskCalibration(
        real_count=len(real),
        ai_count=len(ai),
        real_mean=(sum(real) / len(real)) if real else None,
        ai_mean=(sum(ai) / len(ai)) if ai else None,
    )


def calibrate_ai_risk(raw_score: float, calibration: AiRiskCalibration) -> tuple[float, bool]:
    raw_score = _bounded(raw_score)
    if not calibration.active:
        return round(raw_score, 4), False
    assert calibration.real_mean is not None
    assert calibration.ai_mean is not None
    span = calibration.ai_mean - calibration.real_mean
    # Human-reviewed real examples map near 0.05 risk and reviewed AI examples
    # near 0.95 risk. Values outside the learned centers are clipped.
    calibrated = 0.05 + 0.90 * ((raw_score - calibration.real_mean) / span)
    return round(_bounded(calibrated), 4), True


def assess_ai_risk(
    document: ReferenceAnalysisDocument,
    *,
    confirmation: AiAuthenticityConfirmationDocument | None = None,
    calibration: AiRiskCalibration | None = None,
) -> AiRiskAssessment:
    primary_raw, primary_strong = raw_ai_risk(document)
    cues = ai_visual_cues(document)
    signal_json: dict[str, Any] = {
        "primary_model_risk": document.ai_risk_score,
        "primary_confidence": document.ai_detector_confidence,
        "primary_cues": cues,
        "primary_raw": primary_raw,
    }

    raw = primary_raw
    confidence = document.ai_detector_confidence
    strong_count = primary_strong
    confirmed = False

    if confirmation is not None:
        confirmation_cues = {
            "anatomy": confirmation.anatomy_risk,
            "text_symbol": confirmation.text_symbol_risk,
            "geometry": confirmation.geometry_risk,
            "texture": confirmation.texture_risk,
            "lighting_reflection": confirmation.lighting_reflection_risk,
            "background_consistency": confirmation.background_consistency_risk,
        }
        confirmation_strong = sum(
            value >= AI_STRONG_CUE_THRESHOLD
            for value in confirmation_cues.values()
        )
        confirmation_risk = _bounded(
            0.70 * confirmation.synthetic_probability
            + 0.30 * (
                sum(sorted(confirmation_cues.values(), reverse=True)[:3]) / 3.0
            )
        )
        confirmation_risk *= 0.65 + 0.35 * confirmation.confidence
        raw = _bounded(0.45 * primary_raw + 0.55 * confirmation_risk)
        confidence = _bounded(
            0.40 * document.ai_detector_confidence
            + 0.60 * confirmation.confidence
        )
        strong_count = max(primary_strong, confirmation_strong)
        confirmed = (
            confirmation.synthetic_probability >= AI_CONFIRMATION_SYNTHETIC_THRESHOLD
            and confirmation.confidence >= AI_MIN_CONFIRMATION_CONFIDENCE
            and confirmation_strong >= 1
        )
        signal_json["confirmation"] = {
            "camera_photo_probability": confirmation.camera_photo_probability,
            "synthetic_probability": confirmation.synthetic_probability,
            "confidence": confirmation.confidence,
            "cues": confirmation_cues,
            "evidence": confirmation.evidence,
            "summary": confirmation.summary,
        }

    active_calibration = calibration or AiRiskCalibration()
    calibrated, calibration_applied = calibrate_ai_risk(raw, active_calibration)
    signal_json["ensemble_raw"] = round(raw, 4)
    signal_json["calibrated"] = calibrated
    signal_json["strong_evidence_count"] = strong_count
    signal_json["confirmed"] = confirmed
    signal_json["calibration"] = {
        "active": calibration_applied,
        "real_count": active_calibration.real_count,
        "ai_count": active_calibration.ai_count,
        "real_mean": active_calibration.real_mean,
        "ai_mean": active_calibration.ai_mean,
    }
    return AiRiskAssessment(
        raw_score=round(raw, 4),
        calibrated_score=calibrated,
        detector_confidence=round(confidence, 4),
        strong_evidence_count=strong_count,
        confirmed=confirmed,
        signal_json=signal_json,
        calibration_applied=calibration_applied,
    )


def should_confirm_ai_risk(
    document: ReferenceAnalysisDocument,
    policy: ReferenceFilterPolicy,
) -> bool:
    raw, strong_count = raw_ai_risk(document)
    return (
        raw >= max(AI_CONFIRMATION_TRIGGER, policy.max_ai_risk_score)
        and strong_count >= 1
    )


def evaluate_reference(
    document: ReferenceAnalysisDocument,
    policy: ReferenceFilterPolicy,
    *,
    effective_ai_risk_score: float | None = None,
    ai_evidence_count: int | None = None,
    ai_detector_confidence: float | None = None,
    ai_risk_confirmed: bool | None = None,
    manual_ai_label: str | None = None,
) -> ReferenceDecision:
    ai_risk = (
        document.ai_risk_score
        if effective_ai_risk_score is None
        else _bounded(effective_ai_risk_score)
    )
    final_score = round(_bounded(
        0.15 * document.smile_score
        + 0.25 * document.mobile_ugc_score
        + 0.20 * document.quality_score
        + 0.20 * document.product_fit_score
        + 0.20 * (1.0 - ai_risk)
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

    if manual_ai_label == "ai":
        return ReferenceDecision("rejected_ai_risk", "MANUAL_AI_LABEL", final_score)
    if manual_ai_label != "real" and ai_risk > policy.max_ai_risk_score:
        # Legacy callers that do not provide ensemble evidence retain the prior
        # threshold behavior. The production analyzer supplies evidence and
        # requires corroboration before rejecting a photo as synthetic.
        if ai_evidence_count is None:
            return ReferenceDecision("rejected_ai_risk", "AI_RISK_HIGH", final_score)
        confidence = 0.0 if ai_detector_confidence is None else ai_detector_confidence
        corroborated = (
            bool(ai_risk_confirmed)
            and confidence >= AI_MIN_CONFIRMATION_CONFIDENCE
            and ai_evidence_count >= 1
        ) or (
            ai_evidence_count >= 2
            and confidence >= 0.70
            and ai_risk >= 0.65
        )
        if corroborated:
            return ReferenceDecision("rejected_ai_risk", "AI_RISK_CONFIRMED", final_score)

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

The workflow wants authentic camera photographs of real scenes. Synthetic-image detection is
uncertain, so do not call an image AI-generated from a vague polished look alone. Score separate
visible evidence categories independently. Compression, portrait-mode blur, HDR, beauty filters,
phone sharpening, JPEG artifacts, shallow depth of field, and professional lighting can all occur
in real photographs and are NOT sufficient evidence by themselves.

Do not identify the person and do not infer protected or demographic attributes.
Only evaluate visible composition and image suitability.

Definitions:
- people_count: number of visibly present people.
- primary_head_ratio: height of the primary visible head bounding region divided by full image height, in 0..1. Use null when there is no usable visible head.
- smile_score: 0..1 strength of a clearly positive/smiling visible expression.
- head_visible: true only if the primary head is sufficiently visible for adding or replacing a hat.
- existing_headwear: true when the primary person is already wearing any hat/cap/helmet/head covering.
- head_occlusion: 0..1 fraction/severity of the primary head obscured by crops, objects, hands, other people, heavy hair coverage, or frame edges.
- mobile_ugc_score: 0..1 likelihood the composition feels like a casual, candid, handheld/smartphone-style real-life photo rather than a polished studio/ad pose.
- quality_score: 0..1 technical usefulness: adequate resolution impression, focus, lighting, and visible facial/head details.
- ai_risk_score: overall 0..1 synthetic-image suspicion before the separate evidence categories below.
- ai_anatomy_risk: malformed or internally inconsistent hands, fingers, teeth, ears, eyes, limbs, or body joins.
- ai_text_symbol_risk: malformed, semantically unstable, fused, duplicated, or impossible text/logos/symbols.
- ai_geometry_risk: impossible object structure, perspective, edges, repeated objects, or intersections.
- ai_texture_risk: repeated/smeared textures, waxy skin, fused hair, fabric, jewelry, or material transitions.
- ai_lighting_reflection_risk: physically inconsistent shadows, highlights, mirrors, reflections, or light direction.
- ai_background_consistency_risk: duplicated people/objects, melted details, impossible depth, bokeh, or background transitions.
- ai_detector_confidence: 0..1 confidence that the visible evidence is sufficient to judge authenticity. Use LOW confidence when resolution/crop/compression hides evidence.
- product_fit_score: 0..1 suitability for adding a hat to a bare head or naturally replacing existing headwear while preserving the person, pose, background, camera and lighting.
- summary: concise factual explanation of the visible composition and main suitability issue, max 2 sentences.

Required JSON keys:
people_count, primary_head_ratio, smile_score, head_visible, existing_headwear,
head_occlusion, mobile_ugc_score, quality_score, ai_risk_score,
ai_anatomy_risk, ai_text_symbol_risk, ai_geometry_risk, ai_texture_risk,
ai_lighting_reflection_risk, ai_background_consistency_risk, ai_detector_confidence,
product_fit_score, summary.
""".strip()


def ai_authenticity_confirmation_prompt() -> str:
    return """
Act as a second-pass image-authenticity reviewer. Return exactly one JSON object and no prose.
Your only task is to distinguish an ordinary camera photograph from synthetic/AI/CGI imagery.

Be evidence-based. Do NOT treat polished lighting, shallow depth of field, smartphone portrait
mode, HDR, retouching, beauty filters, JPEG artifacts, or high-quality commercial photography as
proof of AI generation. Inspect independent categories: anatomy, text/symbols, geometry,
textures/material joins, lighting/reflections, and background consistency. A synthetic verdict
should normally require multiple concrete inconsistencies. If detail is too small or compressed,
lower confidence rather than inventing evidence.

Do not identify people or infer protected/demographic attributes.

Required JSON keys:
camera_photo_probability, synthetic_probability, confidence,
anatomy_risk, text_symbol_risk, geometry_risk, texture_risk,
lighting_reflection_risk, background_consistency_risk,
evidence, summary.

evidence must contain at most 6 short visible observations; use an empty list when no concrete
synthetic cue is visible.
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


async def confirm_ai_authenticity(
    *,
    provider: AiMetadataProvider,
    tenant_id: str,
    candidate_id: str,
    image_bytes: bytes,
    image_mime_type: str,
    width: int,
    height: int,
) -> AiAuthenticityConfirmationDocument:
    result = await provider.analyze_single(
        AiMetadataAnalysisInput(
            tenant_id=tenant_id,
            asset_id=candidate_id,
            prompt=ai_authenticity_confirmation_prompt(),
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
            metadata_profile="rrugc_ai_authenticity_confirmation",
            metadata_profile_version=ANALYZER_VERSION,
            image_width=width,
            image_height=height,
            json_schema=AiAuthenticityConfirmationDocument.model_json_schema(),
            analysis_id=candidate_id + ":ai-confirmation",
        )
    )
    return AiAuthenticityConfirmationDocument.model_validate(dict(result.metadata))


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
