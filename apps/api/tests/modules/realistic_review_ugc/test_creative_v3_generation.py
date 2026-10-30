"""Creative Brief V1 -> real Stage 1 ImageGen gates and 6-slot contracts."""
import asyncio
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.creative_brief_service import PROMPT_VERSION
from app.modules.realistic_review_ugc.creative_v3_generation import (
    SCHEMA, SKILL, CONTRACT, CreativeV3Error, _brief_plan, enabled,
    prompt_for_slot, resolve_plan, snapshot_for_job, verify_snapshot,
)
from app.modules.realistic_review_ugc.creative_v3_runner import _checked_png
from app.modules.realistic_review_ugc.model import RrugcKeywordCreativeBriefModel, RrugcKeywordVolumeModel


def brief(keyword="CREATIVE DICTATOR"):
    return {
        "keyword_exact": keyword,
        "strategy": "A cheeky contrast between imagination and control.",
        "meaning_emotion": "Playful, independent, self-aware humor",
        "target_audience": "Designers, musicians, artists",
        "purchase_motivation": "Express identity through sharp humor",
        "creative_hook": "Dynamic CREATE collides with rigid control",
        "concepts": [
            {
                "name": "Route " + mode, "mode": mode,
                "familiar_format": "Simple editorial lettering",
                "creative_twist": "Letterplay based on the original meaning",
                "description": "Visual direction about control",
                "creative_hook": "Hook " + mode,
                "typography_plan": "Keep long quote immediately legible",
                "embroidery_plan": "Dense satin in opposing stitch directions",
                "clarity_assessment": "Needs image verification",
                "embroidery_fit": "Requires physical stitch QA",
                "risks": ["Avoid thin lines"],
            } for mode in ("safe", "creative_twist", "explore")
        ],
    }


def plan(word="CREATIVE DICTATOR"):
    return _brief_plan(keyword=word, brief_json=brief(word),
                       prompt_version=PROMPT_VERSION, brief_id="brief-a")


def settings(on=True):
    return SimpleNamespace(
        CREATIVE_ENGINE_V3_ENABLED=on,
        CREATIVE_ENGINE_V3_IMAGEGEN_ENABLED=on,
        CREATIVE_ENGINE_V3_CANARY_TENANT_IDS="tenant-a",
        IMAGE_GENERATION_ENABLED=True,
        CODEX_IMAGE_GENERATION_ENABLED=False,
        OPENAI_API_KEY="test-api-key",
        CREATIVE_ENGINE_V3_IMAGE_PROVIDER="openai",
        CREATIVE_ENGINE_V3_OPENAI_MODEL="gpt-image-2.5-flare",
        CREATIVE_ENGINE_V3_OPENAI_QUALITY="medium",
        CREATIVE_ENGINE_V3_OPENAI_TIMEOUT_SECONDS=120,
    )


def test_mode_to_slot_contract_and_real_prompt_directions():
    data = plan()
    assert [s["mode"] for s in data["slots"]] == [
        "safe", "safe", "creative_twist", "creative_twist", "explore", "explore",
    ]
    assert [s["variant"] for s in data["slots"]] == ["A","B","A","B","A","B"]
    assert data["fingerprint"] == plan()["fingerprint"]
    assert all("CREATIVE DICTATOR" in prompt_for_slot(data, i) for i in range(1, 7))
    for i in range(1, 7):
        prompt = prompt_for_slot(data, i)
        assert '"creative_hook": "Hook ' + data["slots"][i-1]["mode"] in prompt
        assert "authentic" not in prompt or "satin" in prompt
        assert "native" not in prompt or "transparent" in prompt
    assert prompt_for_slot(data, 1) != prompt_for_slot(data, 2)
    assert prompt_for_slot(data, 1) != prompt_for_slot(data, 3)
    assert "image_spelling_verified" in data and not data["image_spelling_verified"]
    assert not data["stage2_handoff_performed"]


@pytest.mark.parametrize("word", (
    "SUPPORT LIVE MUSIC HIRE LIVE MUSICIANS",
    "IDK WHAT'S GOING ON BUT IT'S A LOT",
    "Cà phê, ĐẸP!  Tôi  YÊU!",
))
def test_exact_unicode_punctuation_preserved(word):
    data = plan(word)
    assert data["keyword_exact"] == word
    assert all(word in prompt_for_slot(data, i) for i in range(1, 7))


def test_snapshot_changes_with_source_and_keyword_and_cannot_be_forged():
    data = plan()
    snapshot = snapshot_for_job(data)
    verify_snapshot(data, snapshot)
    changed = brief()
    changed["concepts"][1]["creative_hook"] = "Totally different visual twist"
    newer = _brief_plan(keyword="CREATIVE DICTATOR", brief_json=changed,
                        prompt_version=PROMPT_VERSION, brief_id="brief-a")
    with pytest.raises(CreativeV3Error, match="changed"):
        verify_snapshot(newer, snapshot)
    with pytest.raises(CreativeV3Error):
        verify_snapshot(plan("CREATIVE DICTATORS"), snapshot)
    with pytest.raises(CreativeV3Error):
        verify_snapshot(data, {**snapshot, "source_sha256": "0" * 64})


def test_feature_gate_requires_every_explicit_condition():
    assert enabled(settings(), "tenant-a")
    assert not enabled(settings(), "tenant-b")
    assert not enabled(settings(False), "tenant-a")
    for field in ("IMAGE_GENERATION_ENABLED", "CREATIVE_ENGINE_V3_IMAGEGEN_ENABLED",
                  "CREATIVE_ENGINE_V3_ENABLED", "OPENAI_API_KEY"):
        disabled = SimpleNamespace(**vars(settings()))
        setattr(disabled, field, False)
        assert not enabled(disabled, "tenant-a")


def test_stale_or_unpicked_brief_cannot_generate():
    engine = create_engine("sqlite://")
    RrugcKeywordVolumeModel.__table__.create(engine)
    RrugcKeywordCreativeBriefModel.__table__.create(engine)
    try:
        with Session(engine) as session:
            word = "CREATIVE DICTATOR"
            kw = RrugcKeywordVolumeModel(id=str(uuid4()), tenant_id="tenant-a",
                 keyword=word, keyword_normalized=word.lower(), picked=True, search_volume=20)
            row = RrugcKeywordCreativeBriefModel(
                id=str(uuid4()), tenant_id="tenant-a", keyword_id=kw.id,
                keyword_sha256=sha256(word.encode()).hexdigest(),
                prompt_version=PROMPT_VERSION, status="completed", brief_json=brief(word),
            )
            session.add_all([kw, row]); session.commit()
            assert resolve_plan(session, tenant_id="tenant-a", keyword_id=kw.id)["fingerprint"]
            with pytest.raises(CreativeV3Error):
                resolve_plan(session, tenant_id="tenant-b", keyword_id=kw.id)
            kw.picked = False; session.commit()
            with pytest.raises(CreativeV3Error):
                resolve_plan(session, tenant_id="tenant-a", keyword_id=kw.id)
            kw.picked = True; row.keyword_sha256 = "0"*64; session.commit()
            with pytest.raises(CreativeV3Error):
                resolve_plan(session, tenant_id="tenant-a", keyword_id=kw.id)
    finally:
        engine.dispose()


def _png(color):
    image = Image.new("RGBA", (2048, 1024), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((400, 300, 1000, 650), fill=color)
    f = BytesIO()
    image.save(f, format="PNG")
    return f.getvalue()


def test_g5_accepts_independent_rgba_masters_rejects_identical_bytes():
    data = _png((10, 15, 130, 255))
    digest, check = _checked_png(data, set())
    assert len(digest) == 64 and check["technical_pass"]
    with pytest.raises(Exception, match="identical"):
        _checked_png(data, {digest})
    with pytest.raises(Exception, match="QA"):
        _checked_png(b"not-a-png", set())


def test_v3_generated_image_contract_cannot_be_misrepresented_as_png_spelling_proof():
    data = plan()
    assert data["requires_image_review"] is True
    assert data["image_spelling_verified"] is False
    assert data["stage2_handoff_performed"] is False

def test_native_six_slot_worker_creates_six_unique_g5_finals(tmp_path, monkeypatch):
    import asyncio
    from contextlib import contextmanager
    from app.modules.image_generation.providers import GeneratedImageResult
    from app.modules.realistic_review_ugc.creative_v3_runner import generate_six

    @contextmanager
    def account(*_args, **_kwargs):
        yield "account-1", str(tmp_path / "codex-home")

    calls = []
    async def native_generate(_self, *, attempt_id, person, references, prompt, **_kwargs):
        calls.append((attempt_id, prompt))
        pixels = _png(((len(calls) * 35) % 255, 34, 100, 255))
        digest = sha256(pixels).hexdigest()
        return GeneratedImageResult(provider="codex", model="imagegen",
            image_bytes=pixels, mime_type="image/png",
            provider_metadata={"typography_v2_native_verified": True, "native_sha256": digest})

    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.creative_v3_runner.acquire_worker_profile", account,
    )
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.creative_v3_runner.CodexImageGenRunner.generate_from_references",
        native_generate,
    )
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.creative_v3_runner.CodexImageGenRunner.cleanup_attempt",
        lambda _self, _attempt: None,
    )
    plan_data = plan()
    source = snapshot_for_job(plan_data)
    fake_job = SimpleNamespace(id=str(uuid4()), entity_id=str(uuid4()), tenant_id="tenant-a")
    fake_context = SimpleNamespace(
        job=fake_job, is_cancelled=False,
        logger=SimpleNamespace(info=lambda *_args, **_kwargs: None),
        dependencies=SimpleNamespace(session_factory=lambda: SimpleNamespace(
            __enter__=lambda self: self,
            __exit__=lambda self, *args: None,
        )),
    )
    class SessionStub:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def commit(self): pass
    fake_context.dependencies.session_factory = SessionStub
    config = SimpleNamespace(**vars(settings()),
        IMAGE_GENERATION_STAGING_ROOT=str(tmp_path), CODEX_IMAGE_HOME=str(tmp_path),
        CODEX_IMAGE_BINARY="codex", CODEX_IMAGE_TIMEOUT_SECONDS=60, CODEX_IMAGE_MODEL="")
    result = asyncio.run(generate_six(context=fake_context, settings=config,
        plan=plan_data, snapshot=source, revalidate=lambda: plan_data))
    assert len(calls) == 6
    assert len(result.output_files) == 6
    assert all(file.filename == f"output/final/design_{i:02}.png"
               for i, file in enumerate(result.output_files, start=1))
    assert result.provider_metadata["image_spelling_verified"] is False
    assert all(__import__("pathlib").Path(file.path).is_file() for file in result.output_files)
    # Re-entry with the same verified checkpoints must never issue paid calls.
    result_again = asyncio.run(generate_six(context=fake_context, settings=config,
        plan=plan_data, snapshot=source, revalidate=lambda: plan_data))
    assert len(result_again.output_files) == 6 and len(calls) == 6


def test_queue_creative_v3_preserves_existing_drive_history_without_rerunning_v1(tmp_path, monkeypatch):
    from app.modules.processing.model import ProcessingJobModel
    from app.modules.processing.repository import ProcessingRepository
    from app.modules.realistic_review_ugc.keyword_images import (
        KeywordImageError, KeywordImageService,
    )
    from app.modules.realistic_review_ugc.model import (
        RrugcKeywordImageJobModel, RrugcImageOutputVersionModel,
    )
    engine = create_engine("sqlite://")
    for table in (RrugcKeywordVolumeModel.__table__, RrugcKeywordCreativeBriefModel.__table__,
                  RrugcKeywordImageJobModel.__table__, ProcessingJobModel.__table__,
                  RrugcImageOutputVersionModel.__table__):
        table.create(engine)
    try:
        with Session(engine) as session:
            word = "CREATIVE DICTATOR"
            kw = RrugcKeywordVolumeModel(
                id=str(uuid4()), tenant_id="tenant-a", keyword=word,
                keyword_normalized=word.lower(), picked=True, search_volume=1)
            brief_row = RrugcKeywordCreativeBriefModel(
                id=str(uuid4()), tenant_id="tenant-a", keyword_id=kw.id,
                keyword_sha256=sha256(word.encode()).hexdigest(),
                prompt_version=PROMPT_VERSION, status="completed", brief_json=brief(word),
            )
            session.add_all([kw, brief_row]);session.commit()
            def create_job(repo, *, tenant_id, job_type, entity_type, entity_id,
                           idempotency_key, payload, **kwargs):
                new = ProcessingJobModel(
                    id=str(uuid4()), tenant_id=tenant_id, job_type=job_type,
                    entity_type=entity_type, entity_id=entity_id,
                    idempotency_key=idempotency_key, payload_json=payload,
                    priority=kwargs.get("priority", 0), max_attempts=kwargs.get("max_attempts",1),
                    provider_key=kwargs.get("provider_key"),
                    provider_scope=kwargs.get("provider_scope"),
                    status="pending",
                )
                session.add(new);session.flush();return new
            monkeypatch.setattr(ProcessingRepository, "create_job", create_job)
            monkeypatch.setattr(
                "app.modules.realistic_review_ugc.keyword_images.load_codex_skill_manifest",
                lambda *_args: SimpleNamespace(workflows=(CONTRACT,)),
            )
            monkeypatch.setattr(
                "app.modules.realistic_review_ugc.keyword_images.installed_stage2_skill_sha256",
                lambda *_args, **_kwargs: "a"*64,
            )
            opts = SimpleNamespace(**vars(settings()), CODEX_IMAGE_HOME=str(tmp_path))
            svc = KeywordImageService(session, opts)
            with pytest.raises(KeywordImageError):
                svc.queue_creative_v3(
                    tenant_id="tenant-b", user_id="u", keyword_id=kw.id,
                    expected_brief_updated_at=brief_row.updated_at.isoformat())
            with pytest.raises(KeywordImageError):
                svc.queue_creative_v3(
                    tenant_id="tenant-a", user_id="u", keyword_id=kw.id,
                    expected_brief_updated_at="2026-01-01T00:00:00Z")
            result = svc.queue_creative_v3(
                tenant_id="tenant-a", user_id="u", keyword_id=kw.id,
                expected_brief_updated_at=brief_row.updated_at.isoformat())
            processing = session.get(ProcessingJobModel, result.processing_job_id)
            assert result.status == "queued"
            assert processing.payload_json["expected_output_count"] == 6
            assert processing.payload_json["creative_v3"]["provider"] == "openai"
            assert processing.provider_key == "openai"
            assert processing.payload_json["creative_v3"]["source_sha256"]
            with pytest.raises(KeywordImageError):
                svc.queue_creative_v3(
                    tenant_id="tenant-a", user_id="u", keyword_id=kw.id,
                    expected_brief_updated_at=brief_row.updated_at.isoformat())
            # A completed older output and its Drive pointer must not be
            # deleted or hidden when the user explicitly starts V3 later.
            result.status = "completed"
            result.output_remote_file_id = "prior-stage1-drive-image"
            processing.status = "completed"
            session.commit()
            next_job = svc.queue_creative_v3(
                tenant_id="tenant-a", user_id="u", keyword_id=kw.id,
                expected_brief_updated_at=brief_row.updated_at.isoformat())
            assert next_job.id == result.id
            assert next_job.output_remote_file_id == "prior-stage1-drive-image"
            assert next_job.processing_job_id != processing.id
            fresh = session.get(ProcessingJobModel, next_job.processing_job_id)
            assert fresh.payload_json["creative_v3"]["fingerprint"]
            assert fresh.payload_json.get("replace_outputs") is None
    finally:
        engine.dispose()

def test_native_v3_worker_creates_six_verified_distinct_private_files(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from app.modules.realistic_review_ugc import creative_v3_runner as worker

    generated_prompts = []
    cleaned = []

    @contextmanager
    def profile(*args, **kwargs):
        yield "account-1", str(tmp_path / "home")

    class FakeNativeRunner:
        def __init__(self, config):
            assert config.skill_name == SKILL
            assert config.output_contract == CONTRACT
        async def generate_from_references(self, *, attempt_id, person, references, prompt):
            assert person is None and references == []
            generated_prompts.append(prompt)
            image = _png((10 + len(generated_prompts), 25, 130, 255))
            return SimpleNamespace(
                image_bytes=image,
                provider_metadata={
                    "typography_v2_native_verified": True,
                    "native_sha256": sha256(image).hexdigest(),
                },
            )
        def cleanup_attempt(self, attempt_id):
            cleaned.append(attempt_id)

    @contextmanager
    def session_factory():
        yield SimpleNamespace(commit=lambda: None)

    class Log:
        def info(self, *args, **kwargs):
            pass

    monkeypatch.setattr(worker, "acquire_worker_profile", profile)
    monkeypatch.setattr(worker, "CodexImageGenRunner", FakeNativeRunner)
    src = plan()
    processing_id = str(uuid4())
    cfg = SimpleNamespace(**vars(settings()),
         IMAGE_GENERATION_STAGING_ROOT=str(tmp_path / "stage1"),
         CODEX_IMAGE_HOME=str(tmp_path / "home"),
         CODEX_IMAGE_BINARY="codex", CODEX_IMAGE_TIMEOUT_SECONDS=600,
         CODEX_IMAGE_MODEL="")
    ctx = SimpleNamespace(
        job=SimpleNamespace(tenant_id="tenant-a", id=processing_id),
        dependencies=SimpleNamespace(session_factory=session_factory),
        is_cancelled=False, logger=Log(),
    )
    result = asyncio.run(worker.generate_six(
        context=ctx, settings=cfg, plan=src, snapshot=snapshot_for_job(src),
        revalidate=lambda: src,
    ))
    assert len(result.output_files) == 6
    assert len(generated_prompts) == 6
    assert len(set(generated_prompts)) == 6
    assert len(cleaned) == 6
    assert all(sha256(open(item.path, "rb").read()).hexdigest()
               for item in result.output_files)
    assert [item.filename for item in result.output_files] == [
        f"output/final/design_{number:02}.png" for number in range(1, 7)
    ]
    assert result.provider_metadata["image_spelling_verified"] is False
    # A later regenerate uses a different ProcessingJob ID and cannot reuse
    # any stale image from the previous run.
    other_id = str(uuid4())
    assert worker._job_directory(cfg, tenant_id="tenant-a", processing_id=other_id) != (
        worker._job_directory(cfg, tenant_id="tenant-a", processing_id=processing_id)
    )
    worker.cleanup_job(settings=cfg, tenant_id="tenant-a", processing_id=processing_id)
    assert not worker._job_directory(cfg, tenant_id="tenant-a", processing_id=processing_id).exists()


def test_native_v3_rejects_unverified_pixels_without_writing(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from app.modules.realistic_review_ugc import creative_v3_runner as worker
    @contextmanager
    def profile(*args, **kwargs):
        yield "account-1", str(tmp_path)

    class ForgedRunner:
        def __init__(self, config):
            pass
        async def generate_from_references(self, **kwargs):
            return SimpleNamespace(image_bytes=_png((20, 30, 130, 255)),
                                   provider_metadata={"typography_v2_native_verified": False})
        def cleanup_attempt(self, attempt_id):
            pass
    @contextmanager
    def session():
        yield SimpleNamespace(commit=lambda: None)
    monkeypatch.setattr(worker, "acquire_worker_profile", profile)
    monkeypatch.setattr(worker, "CodexImageGenRunner", ForgedRunner)
    cfg = SimpleNamespace(**vars(settings()),
         IMAGE_GENERATION_STAGING_ROOT=str(tmp_path),
         CODEX_IMAGE_HOME=str(tmp_path), CODEX_IMAGE_BINARY="codex",
         CODEX_IMAGE_TIMEOUT_SECONDS=90, CODEX_IMAGE_MODEL="")
    processing_id = str(uuid4())
    ctx = SimpleNamespace(job=SimpleNamespace(tenant_id="tenant-a", id=processing_id),
                          dependencies=SimpleNamespace(session_factory=session),
                          is_cancelled=False, logger=SimpleNamespace(info=lambda *args,**kwargs: None))
    with pytest.raises(Exception, match="cannot be verified"):
        asyncio.run(worker.generate_six(
            context=ctx, settings=cfg, plan=plan(), snapshot=snapshot_for_job(plan()),
            revalidate=plan,
        ))
    root = worker._job_directory(cfg, tenant_id="tenant-a", processing_id=processing_id)
    assert not list(root.glob("output/final/*.png"))

def test_v3_openai_gate_does_not_depend_on_codex_tool_access():
    from app.modules.realistic_review_ugc.creative_v3_generation import api_config_signature
    valid = settings()
    assert enabled(valid, "tenant-a")
    assert len(api_config_signature(valid)) == 64
    wrong_model = SimpleNamespace(**vars(valid))
    wrong_model.CREATIVE_ENGINE_V3_OPENAI_MODEL = "gpt-image-1"
    assert not enabled(wrong_model, "tenant-a")
    no_key = SimpleNamespace(**vars(valid))
    no_key.OPENAI_API_KEY = None
    assert not enabled(no_key, "tenant-a")
    no_canary = SimpleNamespace(**vars(valid))
    no_canary.CREATIVE_ENGINE_V3_CANARY_TENANT_IDS = ""
    assert not enabled(no_canary, "tenant-a")


def test_v3_openai_api_six_slot_contract_and_resume(tmp_path, monkeypatch):
    import asyncio
    import base64
    from app.modules.realistic_review_ugc import creative_v3_openai_runner as worker
    from app.modules.realistic_review_ugc.creative_v3_generation import api_config_signature

    calls = []
    class FakeImages:
        async def generate(self, **kwargs):
            calls.append(kwargs)
            assert kwargs["model"] == "gpt-image-2.5-flare"
            assert kwargs["background"] == "transparent"
            assert kwargs["output_format"] == "png"
            assert kwargs["size"] == "1536x1024"
            assert kwargs["n"] == 1
            assert kwargs["extra_body"]["safety_identifier"] != "tenant-a"
            data = _png((len(calls)*24, 25, 120, 255))
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(data).decode("ascii"))])

    class FakeClient:
        def __init__(self, **options):
            assert options["max_retries"] == 0
            assert options["api_key"] == "test-api-key"
            self.images = FakeImages()
        async def close(self):
            return None

    monkeypatch.setattr(worker, "AsyncOpenAI", FakeClient)
    cfg = SimpleNamespace(
        **vars(settings()), IMAGE_GENERATION_STAGING_ROOT=str(tmp_path),
        OPENAI_ORGANIZATION=None, OPENAI_PROJECT=None,
    )
    src = plan()
    processing_id = str(uuid4())
    snapshot = {
        **snapshot_for_job(src), "provider": "openai",
        "provider_sha256": api_config_signature(cfg),
    }
    ctx = SimpleNamespace(
        job=SimpleNamespace(tenant_id="tenant-a", id=processing_id),
        is_cancelled=False,
        logger=SimpleNamespace(info=lambda *_args, **_kwargs: None),
    )
    first = asyncio.run(worker.generate_six(
        context=ctx, settings=cfg, plan=src, snapshot=snapshot,
        revalidate=lambda: src,
    ))
    assert len(first.output_files) == 6
    assert len(calls) == 6
    assert len({call["prompt"] for call in calls}) == 6
    assert all(__import__("pathlib").Path(x.path).is_file() for x in first.output_files)
    assert first.provider_metadata["native_api_verified"] is True
    assert first.provider_metadata["image_spelling_verified"] is False
    again = asyncio.run(worker.generate_six(
        context=ctx, settings=cfg, plan=src, snapshot=snapshot,
        revalidate=lambda: src,
    ))
    assert len(again.output_files) == 6 and len(calls) == 6
    assert all(call["extra_body"]["safety_identifier"] == calls[0]["extra_body"]["safety_identifier"]
               for call in calls)


def test_v3_openai_api_rejects_invalid_image_and_does_not_fabricate(tmp_path, monkeypatch):
    import asyncio
    from app.modules.realistic_review_ugc import creative_v3_openai_runner as worker
    from app.modules.realistic_review_ugc.creative_v3_generation import api_config_signature

    class FakeImages:
        async def generate(self, **kwargs):
            return SimpleNamespace(data=[SimpleNamespace(b64_json="not!base64")])
    class FakeClient:
        def __init__(self, **options):
            self.images = FakeImages()
        async def close(self):
            return None

    monkeypatch.setattr(worker, "AsyncOpenAI", FakeClient)
    cfg = SimpleNamespace(
        **vars(settings()), IMAGE_GENERATION_STAGING_ROOT=str(tmp_path),
        OPENAI_ORGANIZATION=None, OPENAI_PROJECT=None,
    )
    src = plan()
    processing_id = str(uuid4())
    ctx = SimpleNamespace(
        job=SimpleNamespace(tenant_id="tenant-a", id=processing_id),
        is_cancelled=False,
        logger=SimpleNamespace(info=lambda *_args, **_kwargs: None),
    )
    snapshot = {**snapshot_for_job(src), "provider": "openai",
                "provider_sha256": api_config_signature(cfg)}
    with pytest.raises(Exception, match="invalid encoded pixels"):
        asyncio.run(worker.generate_six(
            context=ctx, settings=cfg, plan=src, snapshot=snapshot, revalidate=lambda: src,
        ))
    from app.modules.realistic_review_ugc.creative_v3_runner import _job_directory
    assert not list(_job_directory(cfg, tenant_id="tenant-a", processing_id=processing_id).glob("output/final/*.png"))
