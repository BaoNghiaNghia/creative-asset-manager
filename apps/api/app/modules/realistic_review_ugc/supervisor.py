from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.providers.contracts import AiMetadataAnalysisInput, AiMetadataProvider
from app.modules.processing.repository import ProcessingRepository
from app.modules.realistic_review_ugc.model import (
    RrugcGenerationAttemptModel,
    RrugcSupervisorResultModel,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.service import RrugcError


SUPERVISOR_SKILL_VERSION = "supervisor-hat-v1"
SUPERVISOR_ANALYZER_VERSION = "rrugc-supervisor-v1"
MAX_GENERATION_ATTEMPTS = 3


class SupervisorAnalysisDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_visual_similarity: float = Field(ge=0.0, le=1.0)
    product_color_similarity: float = Field(ge=0.0, le=1.0)
    logo_fidelity: float = Field(ge=0.0, le=1.0)
    placement_score: float = Field(ge=0.0, le=1.0)
    scale_score: float = Field(ge=0.0, le=1.0)
    person_scene_preservation: float = Field(ge=0.0, le=1.0)
    photorealism_score: float = Field(ge=0.0, le=1.0)
    artifact_risk: float = Field(ge=0.0, le=1.0)
    hat_head_width_ratio: float | None = Field(default=None, ge=0.0, le=3.0)
    summary: str = Field(min_length=1, max_length=500)


@dataclass(frozen=True, slots=True)
class SupervisorPolicy:
    min_product_visual_similarity: float = 0.78
    min_product_color_similarity: float = 0.82
    min_logo_fidelity: float = 0.72
    min_placement_score: float = 0.76
    min_scale_score: float = 0.76
    min_person_scene_preservation: float = 0.88
    min_photorealism_score: float = 0.76
    max_artifact_risk: float = 0.24
    min_hat_head_width_ratio: float = 0.85
    max_hat_head_width_ratio: float = 1.25


@dataclass(frozen=True, slots=True)
class SupervisorDecision:
    status: str
    reason: str | None
    expected: dict[str, Any]
    correction: dict[str, Any] | None

    @property
    def passed(self) -> bool:
        return self.status == "pass"


def _correction(kind: str, **values: Any) -> dict[str, Any]:
    return {"kind": kind, **values}


def evaluate_supervisor(
    document: SupervisorAnalysisDocument,
    policy: SupervisorPolicy | None = None,
) -> SupervisorDecision:
    p = policy or SupervisorPolicy()
    checks = (
        (
            document.person_scene_preservation < p.min_person_scene_preservation,
            "PERSON_SCENE_CHANGED",
            {"min": p.min_person_scene_preservation},
            _correction(
                "preserve_person_scene",
                strength="high",
                preserve_identity=True,
                preserve_background=True,
                preserve_pose=True,
            ),
        ),
        (
            document.product_visual_similarity < p.min_product_visual_similarity,
            "PRODUCT_VISUAL_MISMATCH",
            {"min": p.min_product_visual_similarity},
            _correction("increase_product_fidelity", strength="high"),
        ),
        (
            document.product_color_similarity < p.min_product_color_similarity,
            "PRODUCT_COLOR_MISMATCH",
            {"min": p.min_product_color_similarity},
            _correction("match_reference_color", strength="high"),
        ),
        (
            document.logo_fidelity < p.min_logo_fidelity,
            "LOGO_FIDELITY_LOW",
            {"min": p.min_logo_fidelity},
            _correction("preserve_logo_geometry", strength="high"),
        ),
        (
            document.placement_score < p.min_placement_score,
            "PRODUCT_PLACEMENT_INVALID",
            {"min": p.min_placement_score},
            _correction("fix_product_placement", preserve_perspective=True),
        ),
        (
            document.scale_score < p.min_scale_score,
            "PRODUCT_SCALE_INVALID",
            {"min": p.min_scale_score},
            _correction("fix_product_scale", preserve_perspective=True),
        ),
        (
            (
                document.hat_head_width_ratio is not None
                and document.hat_head_width_ratio < p.min_hat_head_width_ratio
            ),
            "HAT_TOO_SMALL",
            {"min": p.min_hat_head_width_ratio},
            _correction("scale_product", direction="up", preserve_brim_angle=True),
        ),
        (
            (
                document.hat_head_width_ratio is not None
                and document.hat_head_width_ratio > p.max_hat_head_width_ratio
            ),
            "HAT_TOO_LARGE",
            {"max": p.max_hat_head_width_ratio},
            _correction("scale_product", direction="down", preserve_brim_angle=True),
        ),
        (
            document.photorealism_score < p.min_photorealism_score,
            "PHOTOREALISM_LOW",
            {"min": p.min_photorealism_score},
            _correction("restore_photorealism", preserve_scene=True),
        ),
        (
            document.artifact_risk > p.max_artifact_risk,
            "ARTIFACT_RISK_HIGH",
            {"max": p.max_artifact_risk},
            _correction("remove_generation_artifacts", preserve_scene=True),
        ),
    )
    for failed, reason, expected, correction in checks:
        if failed:
            return SupervisorDecision("fail", reason, expected, correction)
    return SupervisorDecision("pass", None, {}, None)


def supervisor_prompt(reference_labels: list[str]) -> str:
    labels = ", ".join(reference_labels) if reference_labels else "product reference"
    return f"""
You are the structured QA supervisor for a product-on-person image generation workflow.
Return exactly one JSON object and no prose.

The comparison sheet contains one panel labeled GENERATED, one panel labeled
SOURCE_PERSON, followed by frozen product reference panels labeled {labels}. Evaluate only
visible image properties. SOURCE_PERSON is the exact person/scene input used for the edit.

Do not identify the person. Do not infer age, ethnicity, nationality, religion,
health, disability, sexual orientation, gender identity, or any other protected or
demographic attribute. Compare whether the generated scene preserves the same visible
person/pose/background without naming or classifying the person.

Score each field from 0 to 1:
- product_visual_similarity: same SKU silhouette, construction, material details and geometry.
- product_color_similarity: visible product color/material tone match.
- logo_fidelity: logo/embroidery shape, scale and placement match; use 1.0 when no visible logo
  exists in either generated image or references and there is no conflicting mark.
- placement_score: product sits in the physically correct location/perspective and intersects
  naturally with the head/hair.
- scale_score: product scale is plausible and consistent with the reference geometry.
- person_scene_preservation: generated image preserves the visible person, expression, pose,
  clothing, background, crop, lighting and camera feel from the source edit rather than
  redesigning the scene.
- photorealism_score: realistic lighting, texture, edges, shadows, hair interaction and UGC feel.
- artifact_risk: probability/severity of visible generation artifacts such as warped anatomy,
  duplicate product, floating edges, broken logo or implausible occlusion.
- hat_head_width_ratio: estimated visible hat width divided by visible head width. Use null
  when it cannot be estimated reliably.
- summary: max two concise factual sentences about the most important QA observation.

Required JSON keys:
product_visual_similarity, product_color_similarity, logo_fidelity, placement_score,
scale_score, person_scene_preservation, photorealism_score, artifact_risk,
hat_head_width_ratio, summary.
""".strip()


def build_comparison_sheet(
    generated_image: bytes,
    product_references: list[tuple[str, bytes]],
    *,
    person_source: bytes | None = None,
) -> tuple[bytes, int, int, list[str]]:
    panels: list[tuple[str, Image.Image]] = []
    with Image.open(BytesIO(generated_image)) as opened:
        panels.append(("GENERATED", opened.convert("RGB").copy()))
    if person_source:
        with Image.open(BytesIO(person_source)) as opened:
            panels.append(("SOURCE_PERSON", opened.convert("RGB").copy()))
    for label, content in product_references[:4]:
        with Image.open(BytesIO(content)) as opened:
            panels.append((label.upper(), opened.convert("RGB").copy()))
    if len(panels) < 2:
        raise ValueError("At least one product reference is required for supervisor QA.")

    panel_size = 640
    header = 34
    prepared: list[tuple[str, Image.Image]] = []
    for label, image in panels:
        image.thumbnail((panel_size, panel_size - header), Image.Resampling.LANCZOS)
        tile = Image.new("RGB", (panel_size, panel_size), "white")
        x = (panel_size - image.width) // 2
        y = header + (panel_size - header - image.height) // 2
        tile.paste(image, (x, y))
        draw = ImageDraw.Draw(tile)
        draw.text((12, 10), label, fill="black")
        prepared.append((label, tile))

    columns = 2
    rows = (len(prepared) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * panel_size, rows * panel_size), "white")
    for index, (_, tile) in enumerate(prepared):
        sheet.paste(tile, ((index % columns) * panel_size, (index // columns) * panel_size))

    output = BytesIO()
    sheet.save(output, format="JPEG", quality=88, optimize=True)
    labels = [
        label for label, _ in prepared
        if label not in {"GENERATED", "SOURCE_PERSON"}
    ]
    return output.getvalue(), sheet.width, sheet.height, labels


async def analyze_supervisor_sheet(
    *,
    provider: AiMetadataProvider,
    tenant_id: str,
    result_id: str,
    image_bytes: bytes,
    width: int,
    height: int,
    reference_labels: list[str],
) -> tuple[SupervisorAnalysisDocument, str, str | None]:
    result = await provider.analyze_single(
        AiMetadataAnalysisInput(
            tenant_id=tenant_id,
            asset_id=result_id,
            prompt=supervisor_prompt(reference_labels),
            image_bytes=image_bytes,
            image_mime_type="image/jpeg",
            metadata_profile="rrugc_supervisor",
            metadata_profile_version=SUPERVISOR_ANALYZER_VERSION,
            image_width=width,
            image_height=height,
            json_schema=SupervisorAnalysisDocument.model_json_schema(),
            analysis_id=result_id,
        )
    )
    return (
        SupervisorAnalysisDocument.model_validate(dict(result.metadata)),
        result.provider,
        result.model,
    )


def supervisor_metrics(document: SupervisorAnalysisDocument) -> dict[str, Any]:
    data = document.model_dump()
    data.pop("summary", None)
    return data


def build_correction_prompt(correction: dict[str, Any]) -> str:
    compact = json.dumps(correction, sort_keys=True, separators=(",", ":"))
    return (
        "\n\nSUPERVISOR CORRECTION FOR THIS RETRY:\n"
        + compact
        + "\nApply only this correction while preserving every property that already matches "
          "the person reference and frozen product references."
    )


class RrugcSupervisorService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = RrugcRepository(session)

    def enqueue(
        self,
        *,
        attempt: RrugcGenerationAttemptModel,
        supervisor_skill_version: str = SUPERVISOR_SKILL_VERSION,
    ) -> tuple[RrugcSupervisorResultModel, bool]:
        if attempt.status != "completed" or not attempt.output_remote_file_id:
            raise RrugcError(
                "supervisor_output_not_ready",
                "Supervisor QA requires a completed stored generation output.",
                status_code=409,
            )
        existing = self.repository.supervisor_result_for_attempt(
            attempt.tenant_id,
            attempt.id,
            supervisor_skill_version,
        )
        if existing is not None:
            return existing, False

        row = RrugcSupervisorResultModel(
            tenant_id=attempt.tenant_id,
            campaign_id=attempt.campaign_id,
            generation_attempt_id=attempt.id,
            candidate_id=attempt.candidate_id,
            product_id=attempt.product_id,
            supervisor_skill_version=supervisor_skill_version,
            status="queued",
        )
        try:
            self.session.add(row)
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            existing = self.repository.supervisor_result_for_attempt(
                attempt.tenant_id,
                attempt.id,
                supervisor_skill_version,
            )
            if existing is not None:
                return existing, False
            raise

        job, created = ProcessingRepository(self.session).create_job_once(
            tenant_id=attempt.tenant_id,
            job_type="rrugc_supervisor_qa",
            entity_type="rrugc_supervisor_result",
            entity_id=row.id,
            idempotency_key=f"rrugc-supervisor:{row.id}",
            payload={
                "supervisor_result_id": row.id,
                "generation_attempt_id": attempt.id,
                "campaign_id": attempt.campaign_id,
            },
            priority=55,
            max_attempts=5,
            provider_key="gemini",
            provider_scope="ai_metadata",
        )
        row.processing_job_id = job.id
        self.session.commit()
        self.session.refresh(row)
        return row, created

    def prepare_correction(
        self,
        *,
        result: RrugcSupervisorResultModel,
        user_id: str,
    ) -> tuple[RrugcGenerationAttemptModel, bool]:
        if result.status != "fail" or not result.correction_json:
            raise RrugcError(
                "supervisor_correction_unavailable",
                "Only a failed Supervisor result with a correction can prepare a retry.",
                status_code=409,
            )
        existing = self.repository.correction_attempt_for_supervisor(
            result.tenant_id,
            result.id,
        )
        if existing is not None:
            return existing, False

        source = self.repository.get_generation_attempt(
            result.tenant_id, result.generation_attempt_id
        )
        if source is None:
            raise RrugcError(
                "generation_attempt_not_found",
                "Source generation attempt not found.",
                status_code=404,
            )
        attempts = self.repository.list_generation_attempts(
            result.tenant_id,
            source.campaign_id,
            candidate_id=source.candidate_id,
            limit=100,
        )
        if len(attempts) >= MAX_GENERATION_ATTEMPTS:
            raise RrugcError(
                "generation_attempt_budget_exhausted",
                "Generation retry budget is exhausted; human review is required.",
                status_code=409,
            )
        next_variant = max((row.generation_variant for row in attempts), default=0) + 1
        raw_key = f"{source.id}:{result.id}:{next_variant}:{source.worker_skill_version}"
        idempotency_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        row = RrugcGenerationAttemptModel(
            tenant_id=source.tenant_id,
            campaign_id=source.campaign_id,
            candidate_id=source.candidate_id,
            product_id=source.product_id,
            product_revision=source.product_revision,
            product_snapshot_json=dict(source.product_snapshot_json or {}),
            product_reference_snapshot_json=list(source.product_reference_snapshot_json or []),
            candidate_snapshot_json=dict(source.candidate_snapshot_json or {}),
            generation_variant=next_variant,
            worker_skill_version=source.worker_skill_version,
            status="prepared",
            idempotency_key=idempotency_key,
            created_by_user_id=user_id,
            parent_attempt_id=source.id,
            correction_supervisor_result_id=result.id,
            supervisor_correction_json=dict(result.correction_json),
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row, True
