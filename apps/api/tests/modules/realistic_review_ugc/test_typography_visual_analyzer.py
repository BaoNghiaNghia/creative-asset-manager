"""G3 executable analyzer adapter tests: synthetic image, fake storage/provider.

No AI billing, remote image download, production worker or API endpoint is used.
"""
from __future__ import annotations

import asyncio
from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.providers.contracts import (
    AiMetadataAnalysisResult, AiProviderError, StoredAssetReadStream,
)
from app.modules.realistic_review_ugc.model import RrugcReferenceAssetModel as Asset
from app.modules.realistic_review_ugc.typography_analysis_queue import TypographyAnalysisQueue
from app.modules.realistic_review_ugc.typography_similarity import (
    is_typography_dhash, typography_dhash,
)
from app.modules.realistic_review_ugc.typography_model import (
    RrugcTypographyReferenceModel as Ref,
    RrugcTypographyStyleDnaModel as Dna,
    RrugcTypographyAnalysisJobModel as Job,
)
from app.modules.realistic_review_ugc.typography_visual_analyzer import (
    MAX_VISION_SIDE, TypographyImageUnavailable, TypographyVisualAnalysisRunner,
    prepare_vision_image,
)


def image_data(*, alpha=False) -> bytes:
    output = BytesIO()
    mode, color = ("RGBA", (250, 250, 250, 120)) if alpha else ("RGB", (230, 230, 230))
    Image.new(mode, (128, 96), color).save(output, "PNG")
    return output.getvalue()


def document(*, confidence=0.87):
    return {
        "typography_region": {"x": 0.15, "y": 0.2, "w": 0.65, "h": 0.55},
        "style_family": "curved-bold-serif",
        "font_mood": {
            "primary": "serif", "secondary": None,
            "weight": "heavy", "contrast": "moderate", "proportions": "wide",
        },
        "layout_graph": {
            "nodes": [{"id": "main", "role": "primary", "size_ratio": 1.0,
                       "path_type": "arch", "align": "center"}],
            "edges": [],
        },
        "overlap_rules": [],
        "confidence": confidence,
    }


class FakeStorage:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls = []
        self.closed = 0
        self.hook = None

    async def open_asset(self, request):
        self.calls.append(request)
        if self.hook:
            self.hook()
        async def chunks():
            for index in range(0, len(self.payload), 8192):
                yield self.payload[index:index + 8192]
        async def close():
            self.closed += 1
        return StoredAssetReadStream(body=chunks(), close=close, content_type="image/png")


class FakeProvider:
    def __init__(self, payload=None, *, exception=None, hook=None):
        self.payload = payload if payload is not None else document()
        self.exception = exception
        self.hook = hook
        self.calls = []

    async def analyze_single(self, request):
        self.calls.append(request)
        if self.hook:
            self.hook()
        if self.exception:
            raise self.exception
        return AiMetadataAnalysisResult(
            metadata=self.payload, provider="gemini", model="test-model",
            usage={"input_tokens": 100, "output_tokens": 200},
        )


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def foreign_keys(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")
    for model in (Asset, Ref, Dna, Job):
        model.__table__.create(engine)
    yield engine
    engine.dispose()


def populate(engine, raw=None, *, tenant="tenant-a", rights="owned"):
    raw = raw or image_data()
    digest = sha256(raw).hexdigest()
    with Session(engine) as session:
        session.add(Asset(
            id="image-1", tenant_id=tenant, source_type="upload",
            source_key=digest, content_hash=digest, profile_key="typography-idea",
            reference_type="other", image_format="PNG", status="ready",
            remote_file_id="stored-private-1", size_bytes=len(raw),
            created_by_user_id="owner",
        ))
        session.flush()
        session.add(Ref(
            id="ref-1", tenant_id=tenant, workspace_id=tenant,
            asset_id="image-1", content_hash=digest,
            source_rights_status=rights, privacy="private",
            created_by_user_id="owner",
        ))
        session.flush()
        queue = TypographyAnalysisQueue(session)
        if rights == "owned":
            result = queue.enqueue(
                tenant_id=tenant, reference_id="ref-1", user_id="owner",
                analyzer_version="vision-pilot-1", enabled=True,
                tenant_allowed=True,
            )
            assert result[1] == "created"
        session.commit()
    return raw


def setup_runner(engine, raw, provider=None, *, gate=lambda tenant: tenant == "tenant-a"):
    storage = FakeStorage(raw)
    provider = provider or FakeProvider()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    runner = TypographyVisualAnalysisRunner(
        factory, storage, provider, enabled_for_tenant=gate,
        heartbeat_seconds=0.2,
    )
    return runner, storage, provider


def tick(runner, tenant="tenant-a"):
    return asyncio.run(runner.tick(tenant_id=tenant, worker_id="test-worker"))


def test_success_commits_structural_dna_once_and_preserves_private_source(engine):
    raw = populate(engine)
    runner, store, provider = setup_runner(engine, raw)
    result = tick(runner)
    assert result.status == "completed" and result.provider_calls == 1
    assert store.closed == 1 and len(store.calls) == 1
    assert store.calls[0].tenant_id == "tenant-a"
    assert store.calls[0].asset_id == "image-1"
    assert len(provider.calls) == 1
    prompt = provider.calls[0].prompt
    assert "TYPOGRAPHY STRUCTURE" in prompt and "copying" in prompt
    assert provider.calls[0].metadata_profile_version == "vision-pilot-1"
    assert provider.calls[0].image_mime_type in {"image/jpeg", "image/png"}
    assert provider.calls[0].image_bytes != raw or len(raw) < 5000
    with Session(engine) as session:
        assert session.scalar(select(Job)).status == "completed"
        dna = session.scalar(select(Dna))
        assert dna.analyzer_version == "vision-pilot-1"
        assert dna.image_hash == sha256(raw).hexdigest()
        assert dna.layout_graph_json["nodes"][0]["path_type"] == "arch"
        assert is_typography_dhash(dna.perceptual_hash)
        assert dna.perceptual_hash == typography_dhash(
            provider.calls[0].image_bytes, dna.typography_region_json,
        )
        assert session.scalar(select(Ref)).analysis_status == "ready"
    assert tick(runner).status == "no_eligible_job"
    assert len(provider.calls) == 1


def test_default_off_or_wrong_tenant_never_reads_asset(engine):
    raw = populate(engine)
    runner, storage, provider = setup_runner(engine, raw, gate=lambda _id: False)
    assert tick(runner).status == "feature_disabled"
    assert tick(runner, "tenant-b").status == "feature_disabled"
    assert not storage.calls and not provider.calls
    with Session(engine) as session:
        assert session.scalar(select(Job)).status == "queued"


def test_revoked_rights_or_archived_reference_never_invoke_provider(engine):
    raw = populate(engine)
    with Session(engine) as session:
        session.scalar(select(Ref)).source_rights_status = "blocked"
        session.commit()
    runner, storage, provider = setup_runner(engine, raw)
    assert tick(runner).status == "no_eligible_job"
    assert not storage.calls and not provider.calls


def test_source_hash_mismatch_is_review_required_not_a_provider_call(engine):
    raw = populate(engine)
    runner, store, provider = setup_runner(engine, image_data(alpha=True))
    assert tick(runner).status == "needs_review"
    with Session(engine) as session:
        assert session.scalar(select(Job)).last_error_code == "image_unavailable"
        assert session.scalar(select(Ref)).analysis_status == "needs_review"
        assert session.scalar(select(Dna)) is None
    assert store.closed == 1
    assert not provider.calls


def test_low_confidence_returns_review_status_no_dna(engine):
    raw = populate(engine)
    runner, _, provider = setup_runner(engine, raw, FakeProvider(document(confidence=.4)))
    result = tick(runner)
    assert result.status == "needs_review" and result.provider_calls == 1
    with Session(engine) as session:
        assert session.scalar(select(Dna)) is None
        assert session.scalar(select(Job)).last_error_code == "low_visual_confidence"


def test_provider_cannot_override_trusted_job_identity_or_add_original_words(engine):
    raw = populate(engine)
    malicious = document()
    malicious["image_hash"] = "a" * 64
    malicious["original_text"] = "DO NOT STORE SOURCE LETTERING"
    runner, _, _ = setup_runner(engine, raw, FakeProvider(malicious))
    assert tick(runner).status == "needs_review"
    with Session(engine) as session:
        assert session.scalar(select(Dna)) is None


def test_rights_revoked_while_storage_reads_blocks_billing(engine):
    raw = populate(engine)
    runner, storage, provider = setup_runner(engine, raw)
    def revoke():
        with Session(engine) as session:
            session.scalar(select(Ref)).source_rights_status = "blocked"
            session.commit()
    storage.hook = revoke
    outcome = tick(runner)
    assert outcome.status == "needs_review"
    assert outcome.error_code == "reference_changed"
    assert not provider.calls
    with Session(engine) as session:
        assert session.scalar(select(Dna)) is None


def test_invalid_provider_graph_fails_closed(engine):
    raw = populate(engine)
    bad = document()
    bad["layout_graph"]["nodes"] = []
    runner, _, provider = setup_runner(engine, raw, FakeProvider(bad))
    assert tick(runner).status == "needs_review"
    assert len(provider.calls) == 1
    with Session(engine) as session:
        assert session.scalar(select(Dna)) is None
        assert session.scalar(select(Job)).last_error_code == "invalid_style_dna"


def test_transient_provider_failure_is_budgeted_retry(engine):
    raw = populate(engine)
    provider = FakeProvider(exception=AiProviderError(
        "rate limited", code="provider_busy", retryable=True,
    ))
    runner, _, _ = setup_runner(engine, raw, provider)
    result = tick(runner)
    assert result.status == "queued" and result.provider_calls == 1
    with Session(engine) as session:
        job = session.scalar(select(Job))
        assert job.status == "queued" and job.attempt_count == 1
        assert job.next_attempt_at is not None
        assert session.scalar(select(Dna)) is None


def test_turning_flag_off_during_provider_call_blocks_dna_save(engine):
    raw = populate(engine)
    feature = {"on": True}
    provider = FakeProvider(hook=lambda: feature.update(on=False))
    runner, _, _ = setup_runner(
        engine, raw, provider, gate=lambda tenant: feature["on"] and tenant == "tenant-a",
    )
    assert tick(runner).status == "feature_disabled"
    with Session(engine) as session:
        assert session.scalar(select(Dna)) is None


def test_fenced_worker_cannot_commit_after_lease_token_replaced(engine):
    raw = populate(engine)
    def rotate():
        with Session(engine) as session:
            job = session.scalar(select(Job))
            job.lease_token = "stale-token"
            session.commit()
    runner, _, _ = setup_runner(engine, raw, FakeProvider(hook=rotate))
    assert tick(runner).status == "stale_lease"
    with Session(engine) as session:
        assert session.scalar(select(Dna)) is None


def test_image_preprocessing_bounds_format_and_preserves_transparency():
    original = image_data(alpha=True)
    prepared, mime = prepare_vision_image(original)
    assert mime == "image/png" and prepared.startswith(b"\x89PNG")
    with Image.open(BytesIO(prepared)) as image:
        assert image.mode == "RGBA"
        assert max(image.size) <= MAX_VISION_SIDE
    with pytest.raises(TypographyImageUnavailable):
        prepare_vision_image(b"not an image")
    with pytest.raises(TypographyImageUnavailable):
        prepare_vision_image(b"x" * (12 * 1024 * 1024 + 1))
