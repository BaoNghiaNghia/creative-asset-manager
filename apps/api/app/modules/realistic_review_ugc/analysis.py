from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from app.domain.providers.contracts import AiMetadataAnalysisInput, AiMetadataProvider


ANALYZER_VERSION = "rrugc-reference-v8-product-variants"
QUALITY_FIRST_MAX_AI_RISK = 0.15
QUALITY_FIRST_MIN_QUALITY = 0.60
QUALITY_FIRST_MIN_UGC = 0.55
AI_CONFIRMATION_TRIGGER = 0.30
AI_STRONG_CUE_THRESHOLD = 0.60
AI_CONFIRMATION_SYNTHETIC_THRESHOLD = 0.60
AI_MIN_CONFIRMATION_CONFIDENCE = 0.55
AI_CALIBRATION_MIN_PER_CLASS = 5
REFERENCE_PREFERENCE_MIN_PER_CLASS = 3
AUTO_APPROVE_MIN_FINAL_SCORE = 0.72
AUTO_APPROVE_MIN_PHONE_AUTHENTICITY = 0.60
AUTO_APPROVE_MAX_AI_RISK = 0.20


class ReferenceAnalysisDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    people_count: int = Field(ge=0, le=12)
    primary_head_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    smile_score: float = Field(ge=0.0, le=1.0)
    head_visible: bool
    existing_headwear: bool
    head_occlusion: float = Field(ge=0.0, le=1.0)
    mobile_ugc_score: float = Field(ge=0.0, le=1.0)
    phone_authenticity_score: float = Field(default=0.5, ge=0.0, le=1.0)
    artistic_editorial_risk: float = Field(default=0.0, ge=0.0, le=1.0)
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
    matched_variant_id: str | None = Field(default=None, max_length=36)
    matched_variant_name: str | None = Field(default=None, max_length=300)
    matched_color: str | None = Field(default=None, max_length=120)
    color_match_score: float = Field(default=0.0, ge=0.0, le=1.0)
    product_shape_score: float = Field(default=0.5, ge=0.0, le=1.0)
    scene_type: str = Field(default="unknown", max_length=40)
    framing_type: str = Field(default="unknown", max_length=40)
    camera_angle: str = Field(default="unknown", max_length=40)
    pose_type: str = Field(default="unknown", max_length=40)
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
class ReferencePreferenceModel:
    good_count: int = 0
    bad_count: int = 0
    good_centroid: dict[str, float] | None = None
    bad_centroid: dict[str, float] | None = None

    @property
    def active(self) -> bool:
        return (
            self.good_count >= REFERENCE_PREFERENCE_MIN_PER_CLASS
            and self.bad_count >= REFERENCE_PREFERENCE_MIN_PER_CLASS
            and bool(self.good_centroid)
            and bool(self.bad_centroid)
        )


@dataclass(frozen=True, slots=True)
class ReferenceFilterPolicy:
    min_head_ratio: float = 0.18
    max_head_ratio: float = 0.70
    min_smile_score: float = 0.00
    max_head_occlusion: float = 0.65
    max_ai_risk_score: float = QUALITY_FIRST_MAX_AI_RISK
    min_quality_score: float = 0.60
    min_ugc_score: float = 0.55
    min_product_fit_score: float = 0.55
    min_phone_authenticity_score: float = 0.45
    max_artistic_editorial_risk: float = 0.70
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


REFERENCE_PREFERENCE_FEATURES = (
    "phone_authenticity",
    "mobile_ugc",
    "product_fit",
    "quality",
    "non_artistic",
    "low_ai_risk",
)


def reference_preference_features(
    *,
    phone_authenticity_score: float | None,
    mobile_ugc_score: float | None,
    product_fit_score: float | None,
    quality_score: float | None,
    artistic_editorial_risk: float | None,
    ai_risk_score: float | None,
) -> dict[str, float]:
    def value(raw: float | None, fallback: float = 0.5) -> float:
        return _bounded(fallback if raw is None else raw)

    return {
        "phone_authenticity": value(phone_authenticity_score),
        "mobile_ugc": value(mobile_ugc_score),
        "product_fit": value(product_fit_score),
        "quality": value(quality_score),
        "non_artistic": 1.0 - value(artistic_editorial_risk),
        "low_ai_risk": 1.0 - value(ai_risk_score),
    }


def build_reference_preference_model(
    rows: Iterable[tuple[str, dict[str, Any] | None]],
) -> ReferencePreferenceModel:
    buckets: dict[str, list[dict[str, float]]] = {"ref_good": [], "ref_bad": []}
    for label, payload in rows:
        if label not in buckets or not isinstance(payload, dict):
            continue
        if payload.get("reference_preference_trainable") is False:
            continue
        features = payload.get("reference_preference_features", payload)
        if not isinstance(features, dict):
            continue
        normalized: dict[str, float] = {}
        for key in REFERENCE_PREFERENCE_FEATURES:
            raw = features.get(key)
            if isinstance(raw, (int, float)):
                normalized[key] = _bounded(float(raw))
        if len(normalized) == len(REFERENCE_PREFERENCE_FEATURES):
            buckets[label].append(normalized)

    def centroid(items: list[dict[str, float]]) -> dict[str, float] | None:
        if not items:
            return None
        return {
            key: sum(item[key] for item in items) / len(items)
            for key in REFERENCE_PREFERENCE_FEATURES
        }

    return ReferencePreferenceModel(
        good_count=len(buckets["ref_good"]),
        bad_count=len(buckets["ref_bad"]),
        good_centroid=centroid(buckets["ref_good"]),
        bad_centroid=centroid(buckets["ref_bad"]),
    )


def reference_preference_adjustment(
    features: dict[str, float],
    model: ReferencePreferenceModel,
) -> float:
    if not model.active:
        return 0.0
    assert model.good_centroid is not None
    assert model.bad_centroid is not None

    def similarity(centroid: dict[str, float]) -> float:
        squared = [
            (features[key] - centroid[key]) ** 2
            for key in REFERENCE_PREFERENCE_FEATURES
        ]
        distance = (sum(squared) / len(squared)) ** 0.5
        return _bounded(1.0 - distance)

    delta = similarity(model.good_centroid) - similarity(model.bad_centroid)
    return round(max(-0.12, min(0.12, delta * 0.18)), 4)


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
    reference_preference_score: float = 0.0,
    variant_matching_required: bool = False,
) -> ReferenceDecision:
    ai_risk = (
        document.ai_risk_score
        if effective_ai_risk_score is None
        else _bounded(effective_ai_risk_score)
    )
    variant_mismatch = bool(
        variant_matching_required
        and document.existing_headwear
        and (
            not document.matched_variant_id
            or document.color_match_score < 0.45
            or document.product_shape_score < 0.50
        )
    )
    variant_priority_adjustment = 0.0
    if variant_matching_required and document.existing_headwear:
        if document.matched_variant_id:
            variant_match = _bounded(
                0.55 * document.product_shape_score
                + 0.45 * document.color_match_score
            )
            variant_priority_adjustment = 0.12 * (variant_match - 0.50)
        else:
            variant_priority_adjustment = -0.08

    final_score = round(_bounded(
        0.30 * document.phone_authenticity_score
        + 0.20 * document.mobile_ugc_score
        + 0.15 * document.product_fit_score
        + 0.10 * document.quality_score
        + 0.15 * (1.0 - ai_risk)
        + 0.10 * (1.0 - document.artistic_editorial_risk)
        + variant_priority_adjustment
        + max(-0.12, min(0.12, reference_preference_score))
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
    if (
        document.phone_authenticity_score < policy.min_phone_authenticity_score
        and document.artistic_editorial_risk > 0.55
    ):
        return ReferenceDecision("rejected_context", "PHONE_AUTHENTICITY_LOW", final_score)
    if document.artistic_editorial_risk > policy.max_artistic_editorial_risk:
        return ReferenceDecision("rejected_context", "ARTISTIC_EDITORIAL_HIGH", final_score)
    if document.mobile_ugc_score < policy.min_ugc_score:
        return ReferenceDecision("rejected_context", "UGC_SCORE_LOW", final_score)
    if document.smile_score < policy.min_smile_score:
        return ReferenceDecision("rejected_expression", "SMILE_SCORE_LOW", final_score)
    if document.product_fit_score < policy.min_product_fit_score:
        return ReferenceDecision("rejected_context", "PRODUCT_FIT_LOW", final_score)
    if variant_mismatch:
        # A visually useful candid should not be discarded solely because the
        # current hat color is outside the selected product variants. Keep it
        # reviewable, but prevent it from outranking a strong color/shape match.
        return ReferenceDecision(
            "needs_review",
            "PRODUCT_VARIANT_MISMATCH",
            final_score,
        )

    # Passing the hard gates means the image is usable, but only high-confidence
    # smartphone-like references should flow straight into automation. Borderline
    # references stay visible for human review instead of being silently accepted.
    unresolved_ai_risk = (
        ai_risk > AUTO_APPROVE_MAX_AI_RISK
        and (ai_evidence_count is None or ai_evidence_count > 0 or bool(ai_risk_confirmed))
    )
    if manual_ai_label != "real" and (
        final_score < AUTO_APPROVE_MIN_FINAL_SCORE
        or document.phone_authenticity_score < AUTO_APPROVE_MIN_PHONE_AUTHENTICITY
        or unresolved_ai_risk
    ):
        return ReferenceDecision("needs_review", "AUTO_APPROVE_UNCERTAIN", final_score)
    return ReferenceDecision("approved", None, final_score)


def analysis_prompt(product_context: dict | None = None) -> str:
    base = """
You are evaluating a Pinterest lifestyle reference for a quality-first real-photo workflow.
Return exactly one JSON object and no prose.

The workflow strongly prefers authentic smartphone-style personal photos over artistic/editorial
photography: ordinary selfies, mirror selfies, car selfies, cafe/home/outdoor snapshots, family
moments, and casual candid images with natural ambient light and imperfect everyday framing.
A person may be smiling or neutral, looking at camera or away, sitting, drinking, or looking down.
Do not reward studio polish, cinematic lighting, fashion/editorial posing, heavy art direction, or
professional portrait aesthetics just because they look technically beautiful.

Synthetic-image detection is uncertain, so do not call an image AI-generated from a vague polished
look alone. Score separate visible evidence categories independently. Compression, portrait-mode
blur, HDR, beauty filters, phone sharpening, JPEG artifacts, shallow depth of field, and
professional lighting can all occur in real photographs and are NOT sufficient evidence by
themselves.

Do not identify the person and do not infer protected or demographic attributes.
Only evaluate visible composition and image suitability.

Definitions:
- people_count: number of visibly present people.
- primary_head_ratio: height of the primary visible head bounding region divided by full image height, in 0..1. Use null when there is no usable visible head.
- smile_score: 0..1 strength of a clearly positive/smiling visible expression. Treat this as descriptive only: a neutral, serious, looking-down, sipping, or candid expression can still be an excellent reference.
- head_visible: true when the primary head/hat region is sufficiently readable to preserve or naturally replace headwear. Existing caps are valid references. A cap brim partly covering the forehead or eyes does not make head_visible false when the overall head pose and hat placement remain understandable.
- existing_headwear: true when the primary person is already wearing any hat/cap/helmet/head covering. Existing casual caps are useful positive examples for this workflow, not a defect by themselves.
- head_occlusion: 0..1 severity of external obstruction that prevents understanding the head pose or hat placement. Do NOT count the subject's own normal cap/cap brim as occlusion merely because it covers hair, forehead, or part of the eyes. Count hands, crops, other people, objects, or extreme pose only when they materially block the usable head/hat region.
- mobile_ugc_score: 0..1 likelihood the composition feels casual, candid, everyday and UGC-like rather than a planned advertising/editorial shoot.
- phone_authenticity_score: 0..1 likelihood the image visually behaves like an ordinary smartphone/personal photo. Raise the score for front-camera or arm-length selfie perspective, mirror selfie framing, casual car/home/cafe/outdoor snapshots, natural ambient light, imperfect or slightly off-center framing, ordinary background clutter, typical phone HDR/sharpening/noise, and spontaneous social-photo composition. This is a style score, not EXIF/device identification.
- artistic_editorial_risk: 0..1 likelihood the image is too intentionally art-directed for this workflow. Raise it for studio/fashion/editorial posing, cinematic or dramatic lighting design, elaborate set styling, highly controlled professional portrait composition, strong campaign-like color grading, conspicuous lens/bokeh aesthetics, or magazine/branding-shoot presentation. Do NOT raise it merely because a real phone photo is attractive, sharp, well lit, or uses portrait mode.
- quality_score: 0..1 technical usefulness: enough resolution, focus, lighting, and visible facial/head details. Do not reward artistic polish by itself.
- ai_risk_score: overall 0..1 synthetic-image suspicion before the separate evidence categories below.
- ai_anatomy_risk: malformed or internally inconsistent hands, fingers, teeth, ears, eyes, limbs, or body joins.
- ai_text_symbol_risk: malformed, semantically unstable, fused, duplicated, or impossible text/logos/symbols.
- ai_geometry_risk: impossible object structure, perspective, edges, repeated objects, or intersections.
- ai_texture_risk: repeated/smeared textures, waxy skin, fused hair, fabric, jewelry, or material transitions.
- ai_lighting_reflection_risk: physically inconsistent shadows, highlights, mirrors, reflections, or light direction.
- ai_background_consistency_risk: duplicated people/objects, melted details, impossible depth, bokeh, or background transitions.
- ai_detector_confidence: 0..1 confidence that the visible evidence is sufficient to judge authenticity. Use LOW confidence when resolution/crop/compression hides evidence.
- product_fit_score: 0..1 suitability for preserving the candid photo while adding a cap to a bare head or replacing existing casual headwear. Score existing baseball/corduroy caps highly when the crown, brim direction, head angle, and overall placement are readable enough for a natural replacement. Wide full-body/lifestyle frames where the primary head is too small to retain useful headwear detail should score lower even if the scene is otherwise attractive.
- matched_variant_id: when existing_headwear is true and a bound product variant is visibly the closest match, return exactly that variant's supplied internal id. Otherwise null. Never invent an id.
- matched_variant_name: visible closest bound variant name, or null when no confident variant match exists.
- matched_color: concise visible headwear color/colorway, or null when headwear is absent or unreadable.
- color_match_score: 0..1 similarity between visible existing headwear colorway and the selected bound variant. Use 0 when no readable existing headwear is present.
- product_shape_score: 0..1 similarity of visible existing headwear silhouette/construction to the bound product family (crown profile, panel structure, brim/visor, proportions). For bare heads, score general suitability for receiving the bound product without claiming an existing-product match.
- scene_type: short lowercase setting category: home, car, cafe, outdoor, street, mirror, studio, event, workplace, or other.
- framing_type: short lowercase framing category: selfie_close, portrait_close, half_body, full_body, group, mirror_selfie, or other.
- camera_angle: short lowercase angle category: eye_level, high_angle, low_angle, side_angle, mirror, or other.
- pose_type: short lowercase pose category: selfie, seated, standing, walking, candid_activity, looking_away, group, or other.
- summary: concise factual explanation of the visible composition and main suitability issue, max 2 sentences.

Required JSON keys:
people_count, primary_head_ratio, smile_score, head_visible, existing_headwear,
head_occlusion, mobile_ugc_score, phone_authenticity_score, artistic_editorial_risk,
quality_score, ai_risk_score,
ai_anatomy_risk, ai_text_symbol_risk, ai_geometry_risk, ai_texture_risk,
ai_lighting_reflection_risk, ai_background_consistency_risk, ai_detector_confidence,
product_fit_score, matched_variant_id, matched_variant_name, matched_color,
color_match_score, product_shape_score,
scene_type, framing_type, camera_angle, pose_type, summary.
""".strip()
    product = dict(product_context or {})
    variants = list(product.get("variants") or [])[:30]
    family_parts = [
        ("name", product.get("name")),
        ("type", product.get("product_type")),
        ("brand", product.get("brand")),
        ("category", product.get("source_category")),
        ("material", product.get("material")),
        ("crown profile", product.get("crown_profile")),
        ("brim style", product.get("brim_style")),
        ("fit notes", product.get("fit_notes")),
        ("description", product.get("source_description")),
    ]
    family = "\n".join(
        f"- {label}: {str(value).strip()[:700]}"
        for label, value in family_parts
        if str(value or "").strip()
    )
    family_block = (
        "\n\nBOUND PRODUCT FAMILY (use this for product_shape_score):\n" + family
        if family
        else ""
    )
    if not variants:
        return base + family_block + (
            "\n\nNo product variant catalog is bound. Return matched_variant_id, "
            "matched_variant_name and matched_color as null; use color_match_score=0."
        )
    lines = []
    for item in variants:
        variant_id = str(item.get("id") or "").strip()
        if not variant_id:
            continue
        label = str(item.get("name") or item.get("color") or "variant").strip()
        color = str(item.get("color") or "").strip()
        size = str(item.get("size") or "").strip()
        lines.append(
            "- id=" + variant_id
            + " | name=" + label
            + (" | color=" + color if color else "")
            + (" | size=" + size if size else "")
        )
    return base + family_block + (
        "\n\nBOUND PRODUCT VARIANT CATALOG (only these internal ids are valid):\n"
        + "\n".join(lines)
        + "\nIf existing headwear is visible, compare it against this catalog. "
          "Choose a matched_variant_id only when the visible colorway and product shape "
          "support that choice. If uncertain, return null rather than guessing."
    )


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
    product_context: dict | None = None,
) -> tuple[ReferenceAnalysisDocument, str, str | None]:
    result = await provider.analyze_single(
        AiMetadataAnalysisInput(
            tenant_id=tenant_id,
            asset_id=candidate_id,
            prompt=analysis_prompt(product_context),
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
