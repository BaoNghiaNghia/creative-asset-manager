"""G3 opt-in typography visual analyzer. Never registers jobs or starts a worker.

One bounded image from tenant-scoped Managed Storage -> one existing provider
JSON analysis -> strict versioned DNA validation -> lease-fenced database commit.
Provider/model credentials are supplied by the existing CAM registry; no public
URL, model response, image bytes or source lettering are written to logs.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from time import monotonic
from typing import Callable, Protocol

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.providers.contracts import (
    AiMetadataAnalysisInput, AiProviderError, OpenStoredAssetInput, StorageProviderError,
)
from app.modules.realistic_review_ugc.model import RrugcReferenceAssetModel as Asset
from app.modules.realistic_review_ugc.typography_analysis_queue import (
    TypographyAnalysisQueue, VERIFIED_RIGHTS,
)
from app.modules.realistic_review_ugc.typography_dna_contract import (
    SCHEMA_VERSION, TypographyDnaContractError, validate_typography_dna,
)
from app.modules.realistic_review_ugc.typography_model import (
    RrugcTypographyReferenceModel as Reference,
)
from app.modules.realistic_review_ugc.typography_similarity import typography_dhash

MAX_SOURCE_BYTES = 12 * 1024 * 1024
MAX_SOURCE_PIXELS = 20_000_000
MAX_VISION_SIDE = 1280
SOURCE_TIMEOUT_SECONDS = 30
PROVIDER_TIMEOUT_SECONDS = 90
HEARTBEAT_SECONDS = 35
MIN_CONFIDENCE = 0.65
SUPPORTED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class TypographyImageUnavailable(ValueError):
    """Invalid or inaccessible reference; not safe for automated analysis."""


class Region(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float
    y: float
    w: float
    h: float


class FontMood(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: str
    secondary: str | None = None
    weight: str
    contrast: str
    proportions: str


class LayoutNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    role: str
    size_ratio: float
    path_type: str
    align: str


class LayoutEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_: str = Field(alias="from")
    to: str
    relation: str
    strength: float


class LayoutGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: list[LayoutNode]
    edges: list[LayoutEdge]


class OverlapRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: str
    b: str
    region: str
    reading_priority: str
    max_occlusion_ratio: float


class StyleDnaVisionDocument(BaseModel):
    """The provider cannot set trusted schema/version/hash/tenant fields."""
    model_config = ConfigDict(extra="forbid")
    typography_region: Region
    style_family: str
    font_mood: FontMood
    layout_graph: LayoutGraph
    overlap_rules: list[OverlapRule]
    confidence: float


PROMPT = """Analyze ONLY the TYPOGRAPHY STRUCTURE in this user-authorized reference
image. Ignore all product/hat/fabric/lighting/background/persons and pictorial
objects. This is a structural style analysis, not text extraction or copying.

Return a tight normalized [0,1] region enclosing visible LETTERING only, and
describe generalized font mood, up to 12 relative hierarchy nodes, relations
and overlap rules. Roles: primary/secondary; paths: straight/arch/curve;
alignment: left/center/right; edges: above/below/overlap/inside.
Nodes MUST NOT contain the original words, exact font names, brand names,
trademark identifiers or text content. Keep geometry relative rather than
copying exact letter outlines, positions or a complete design template.
Prefer zero overlap rules if legibility would be compromised.
Set low confidence when text is unreadable or typography is unclear.
Do NOT invent lettering. Provide only strict JSON matching the schema."""


def tenant_can_analyze(tenant_id: str) -> bool:
    """Always consult trusted server settings, not browser-provided flags."""
    settings = get_settings()
    tenants = {item.strip() for item in settings.TYPOGRAPHY_V2_CANARY_TENANT_IDS.split(",") if item.strip()}
    return bool(tenant_id and settings.TYPOGRAPHY_V2_ENABLED and tenant_id in tenants)


class VisionProvider(Protocol):
    async def analyze_single(self, data: AiMetadataAnalysisInput): ...


class ManagedImageStore(Protocol):
    async def open_asset(self, data: OpenStoredAssetInput): ...


@dataclass(frozen=True)
class AnalysisClaim:
    tenant_id: str
    job_id: str
    token: str
    reference_id: str
    image_hash: str
    analyzer_version: str
    asset_id: str
    remote_file_id: str
    size_bytes: int | None
    content_type: str


@dataclass(frozen=True)
class AnalysisTick:
    status: str
    provider_calls: int = 0
    latency_ms: int = 0
    error_code: str | None = None


def prepare_vision_image(raw: bytes) -> tuple[bytes, str]:
    """Decode bounded image, respect EXIF, shrink without retaining source bytes.

    Preserve PNG alpha for flat artwork; optimized JPEG for opaque photographs.
    Image operations are all local and never generate product imagery.
    """
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        raise TypographyImageUnavailable("source_size_invalid")
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in SUPPORTED_FORMATS:
                raise TypographyImageUnavailable("source_format_invalid")
            if source.width < 8 or source.height < 8 or source.width * source.height > MAX_SOURCE_PIXELS:
                raise TypographyImageUnavailable("source_dimensions_invalid")
            image = ImageOps.exif_transpose(source)
            image.thumbnail((MAX_VISION_SIDE, MAX_VISION_SIDE), Image.Resampling.LANCZOS)
            out = BytesIO()
            if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                image.convert("RGBA").save(out, "PNG", optimize=True)
                media_type = "image/png"
            else:
                image.convert("RGB").save(out, "JPEG", quality=82, optimize=True)
                media_type = "image/jpeg"
            content = out.getvalue()
            if len(content) > MAX_SOURCE_BYTES:
                raise TypographyImageUnavailable("prepared_image_too_large")
            return content, media_type
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        if isinstance(exc, TypographyImageUnavailable):
            raise
        raise TypographyImageUnavailable("source_decode_failed") from exc


class TypographyVisualAnalysisRunner:
    """One explicit tick, one provider request at most; not a service or loop.

    Session transactions are committed BEFORE any storage/provider I/O. The
    heartbeat uses new short sessions and loses the ability to commit if the
    tenant flag, reference rights, hash or lease token is no longer valid.
    """

    def __init__(
        self, session_factory: Callable[[], Session],
        storage: ManagedImageStore,
        provider: VisionProvider,
        *,
        enabled_for_tenant: Callable[[str], bool] = tenant_can_analyze,
        source_timeout: float = SOURCE_TIMEOUT_SECONDS,
        provider_timeout: float = PROVIDER_TIMEOUT_SECONDS,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ):
        if min(source_timeout, provider_timeout, heartbeat_seconds) <= 0:
            raise ValueError("Time budgets must be positive")
        self.session_factory = session_factory
        self.storage = storage
        self.provider = provider
        self.enabled_for_tenant = enabled_for_tenant
        self.source_timeout = source_timeout
        self.provider_timeout = provider_timeout
        self.heartbeat_seconds = heartbeat_seconds

    def _claim(self, tenant_id: str, owner: str, concurrency: int) -> tuple[AnalysisClaim | None, str]:
        if not self.enabled_for_tenant(tenant_id):
            return None, "feature_disabled"
        with self.session_factory() as session:
            q = TypographyAnalysisQueue(session)
            job = q.claim(tenant_id=tenant_id, owner=owner,
                          enabled=True, tenant_allowed=True, concurrency=concurrency)
            if job is None:
                session.commit()
                return None, "no_eligible_job"
            reference = session.scalar(select(Reference).where(
                Reference.tenant_id == tenant_id, Reference.id == job.reference_id,
                Reference.content_hash == job.image_hash,
                Reference.source_rights_status.in_(VERIFIED_RIGHTS),
                Reference.editorial_status.notin_(("archived", "rejected")),
            ))
            asset = session.scalar(select(Asset).where(
                Asset.tenant_id == tenant_id,
                Asset.id == reference.asset_id,
                Asset.status == "ready",
                Asset.content_hash == job.image_hash,
            )) if reference is not None else None
            media_type = SUPPORTED_FORMATS.get(asset.image_format or "") if asset is not None else None
            if (not asset or not asset.remote_file_id or not media_type
                    or (asset.size_bytes is not None and asset.size_bytes > MAX_SOURCE_BYTES)):
                q.fail(tenant_id=tenant_id, job_id=job.id, token=job.lease_token,
                       code="reference_unavailable", retryable=False)
                session.commit()
                return None, "needs_review"
            snapshot = AnalysisClaim(
                tenant_id=tenant_id, job_id=job.id, token=job.lease_token,
                reference_id=job.reference_id, image_hash=job.image_hash,
                analyzer_version=job.analyzer_version, asset_id=asset.id,
                remote_file_id=asset.remote_file_id, size_bytes=asset.size_bytes,
                content_type=media_type,
            )
            session.commit()
            return snapshot, "claimed"

    def _authorized(self, claim: AnalysisClaim) -> bool:
        if not self.enabled_for_tenant(claim.tenant_id):
            return False
        with self.session_factory() as session:
            ref = session.scalar(select(Reference).where(
                Reference.tenant_id == claim.tenant_id,
                Reference.id == claim.reference_id,
                Reference.content_hash == claim.image_hash,
                Reference.source_rights_status.in_(VERIFIED_RIGHTS),
                Reference.editorial_status.notin_(("archived", "rejected")),
            ))
            return ref is not None

    async def _read_image(self, claim: AnalysisClaim) -> tuple[bytes, str]:
        stream = await self.storage.open_asset(OpenStoredAssetInput(
            tenant_id=claim.tenant_id, asset_id=claim.asset_id,
            remote_file_id=claim.remote_file_id,
            content_type=claim.content_type, size_bytes=claim.size_bytes,
        ))
        try:
            chunks: list[bytes] = []
            count = 0
            async for block in stream.body:
                count += len(block)
                if count > MAX_SOURCE_BYTES:
                    raise TypographyImageUnavailable("source_size_invalid")
                chunks.append(block)
            raw = b"".join(chunks)
        finally:
            await stream.close()
        if sha256(raw).hexdigest() != claim.image_hash:
            raise TypographyImageUnavailable("source_hash_mismatch")
        return prepare_vision_image(raw)

    def _heartbeat(self, claim: AnalysisClaim) -> bool:
        if not self.enabled_for_tenant(claim.tenant_id):
            return False
        with self.session_factory() as session:
            result = TypographyAnalysisQueue(session).heartbeat(
                tenant_id=claim.tenant_id, job_id=claim.job_id,
                token=claim.token, enabled=True, tenant_allowed=True,
            )
            session.commit()
            return result

    async def _heartbeat_until_stopped(self, claim: AnalysisClaim, lost: asyncio.Event) -> None:
        while not lost.is_set():
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                renewed = self._heartbeat(claim)
            except Exception:
                # A failed DB renewal must fence this worker, not leave it
                # eligible to persist DNA under an unverified lease.
                renewed = False
            if not renewed:
                lost.set()
                return

    def _fail(self, claim: AnalysisClaim, code: str, retryable: bool) -> str:
        if not self.enabled_for_tenant(claim.tenant_id):
            return "feature_disabled"
        with self.session_factory() as session:
            status = TypographyAnalysisQueue(session).fail(
                tenant_id=claim.tenant_id, job_id=claim.job_id,
                token=claim.token, code=code, retryable=retryable,
            )
            session.commit()
            return status

    def _complete(self, claim: AnalysisClaim, payload: dict,
                  perceptual_hash: str | None = None) -> str:
        if not self.enabled_for_tenant(claim.tenant_id):
            return "feature_disabled"
        with self.session_factory() as session:
            status = TypographyAnalysisQueue(session).complete(
                tenant_id=claim.tenant_id, job_id=claim.job_id,
                token=claim.token, payload=payload, enabled=True, tenant_allowed=True,
                perceptual_hash=perceptual_hash,
            )
            session.commit()
            return status

    async def tick(self, *, tenant_id: str, worker_id: str, concurrency: int = 1) -> AnalysisTick:
        if not self.enabled_for_tenant(tenant_id):
            return AnalysisTick("feature_disabled")
        if self.provider is None or self.storage is None:
            return AnalysisTick("provider_unavailable")
        start = monotonic()
        claim, outcome = self._claim(tenant_id, worker_id, concurrency)
        if claim is None:
            return AnalysisTick(outcome)
        lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat_until_stopped(claim, lost))
        provider_calls = 0
        try:
            image_bytes, mime = await asyncio.wait_for(
                self._read_image(claim), timeout=self.source_timeout,
            )
            if lost.is_set():
                return AnalysisTick("stale_lease")
            if not self.enabled_for_tenant(tenant_id):
                return AnalysisTick("feature_disabled")
            if not self._authorized(claim):
                status = self._fail(claim, "reference_changed", retryable=False)
                return AnalysisTick(status, error_code="reference_changed")
            provider_calls = 1
            result = await asyncio.wait_for(
                self.provider.analyze_single(AiMetadataAnalysisInput(
                    tenant_id=tenant_id, asset_id=claim.asset_id,
                    prompt=PROMPT, image_bytes=image_bytes, image_mime_type=mime,
                    metadata_profile="rrugc_typography_style_dna",
                    metadata_profile_version=claim.analyzer_version,
                    analysis_id=claim.job_id,
                    json_schema=StyleDnaVisionDocument.model_json_schema(),
                    is_cancelled=lost.is_set,
                )), timeout=self.provider_timeout,
            )
            document = StyleDnaVisionDocument.model_validate(dict(result.metadata))
            # Only typed structural values survive; source ownership/version are
            # always stamped from the trusted database claim, never from AI.
            payload = {
                "schema_version": SCHEMA_VERSION,
                "analyzer_version": claim.analyzer_version,
                "image_hash": claim.image_hash,
                **document.model_dump(by_alias=True),
            }
            dna = validate_typography_dna(payload, source_sha256=claim.image_hash)
            if dna["confidence"] < MIN_CONFIDENCE:
                status = self._fail(claim, "low_visual_confidence", retryable=False)
                return AnalysisTick(status, provider_calls, int((monotonic() - start) * 1000),
                                    "low_visual_confidence")
            if lost.is_set():
                return AnalysisTick("stale_lease", provider_calls)
            if not self.enabled_for_tenant(tenant_id):
                return AnalysisTick("feature_disabled", provider_calls)
            if not self._authorized(claim):
                status = self._fail(claim, "reference_changed", retryable=False)
                return AnalysisTick(status, provider_calls, error_code="reference_changed")
            # Local typography-only ROI hash. Advisory metadata only: an
            # unsupported crop must not discard otherwise valid Style DNA.
            try:
                perceptual_hash = typography_dhash(image_bytes, dna["typography_region"])
            except ValueError:
                perceptual_hash = None
            status = self._complete(claim, dna, perceptual_hash=perceptual_hash)
            return AnalysisTick(status, provider_calls, int((monotonic() - start) * 1000))
        except TypographyImageUnavailable as exc:
            status = self._fail(claim, "image_unavailable", retryable=False)
            return AnalysisTick(status, provider_calls, int((monotonic() - start) * 1000), str(exc))
        except (TypographyDnaContractError, ValidationError, TypeError, ValueError):
            status = self._fail(claim, "invalid_style_dna", retryable=False)
            return AnalysisTick(status, provider_calls, int((monotonic() - start) * 1000),
                                "invalid_style_dna")
        except (StorageProviderError, AiProviderError) as exc:
            status = self._fail(claim, "analysis_provider_error", retryable=bool(exc.retryable))
            return AnalysisTick(status, provider_calls, int((monotonic() - start) * 1000),
                                "analysis_provider_error")
        except TimeoutError:
            status = self._fail(claim, "analysis_timeout", retryable=True)
            return AnalysisTick(status, provider_calls, int((monotonic() - start) * 1000),
                                "analysis_timeout")
        except Exception:
            # Fail boundedly; never log private bytes, model text, URLs or secrets.
            status = self._fail(claim, "analysis_failed", retryable=True)
            return AnalysisTick(status, provider_calls, int((monotonic() - start) * 1000),
                                "analysis_failed")
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
