"""The preflight preserves verified facts without granting a claim."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from app.domain.providers.contracts import AiProviderError,AiMetadataAnalysisResult
from app.domain.providers.registry import AiProviderUnavailableError
from app.modules.image_orchestrator import live_preflight as f,policy as p

SHA="a"*64
SRC="b"*64
ITEM={"listingId":"123456789","artworkSha256":SHA,"sourceSha256":SRC,
      "sourceJobId":"fixture-job","sourceAssetId":"fixture-asset",
      "originalBytesVerified":True,"reason":"NEW_PACK_AWAITING_SOURCE_PLAN_AND_FENCE"}

@pytest.fixture
def setup(monkeypatch):
    from app.modules.image_orchestrator import candidate_discovery
    from app.modules.etsy_spy import imagegen_drive
    from app.providers.ai import factory
    db=Mock()
    db.scalar.return_value="Fixture shirt"
    monkeypatch.setattr(f,"load_original",AsyncMock(return_value=(
        SimpleNamespace(body=b"fixture"),dict(ITEM))))
    monkeypatch.setattr(candidate_discovery,"inspect_listing",lambda *a:dict(ITEM))
    monkeypatch.setattr(f,"source_board",lambda *a:(
        b"board",(1,2,101,202),["black","navy"],{"palette":"fixture"}))
    monkeypatch.setattr(imagegen_drive,"provider_and_folder",lambda:("provider","folder"))
    drive=AsyncMock(return_value="restricted")
    monkeypatch.setattr(imagegen_drive,"verify_private_drive_destination",drive)
    provider=SimpleNamespace(analyze_single=AsyncMock(return_value=AiMetadataAnalysisResult(
        {k:True for k in f.SourceChecks.model_fields},"gemini","fixture-model","fixture-id")))
    registry=SimpleNamespace(require=Mock(return_value=provider),aclose=AsyncMock())
    monkeypatch.setattr(factory,"build_ai_provider_registry",lambda *a,**kw:registry)
    return db,drive,provider,registry

def execute(setup):
    return asyncio.run(f.preflight(setup[0],"fixture",ITEM["listingId"],SHA))

def test_complete_preflight_preserves_actual_evidence(setup):
    r=execute(setup)
    assert r["sourceAnalysisVerified"] and r["driveDestinationVerified"]
    assert r["sourceVisualQaVerified"] and not r["claimAllowed"]
    assert r["inkBounds"]==[1,2,101,202]
    assert r["inkAspectRatio"]==0.5
    assert r["eligibleColorIds"]==["black","navy"]
    assert p.valid_sha(r["sourceVisualReviewSha256"])

def test_provider_failure_preserves_completed_checks(setup):
    setup[2].analyze_single.side_effect=AiProviderError(
        "private diagnostic message",code="gemini_transport_error",retryable=True,status_code=503)
    with pytest.raises(f.PreflightIncomplete) as err:
        execute(setup)
    detail=err.value.detail
    assert detail["failedStage"]=="source_reviewer"
    assert detail["failureCode"]=="gemini_transport_error"
    assert detail["upstreamStatus"]==503
    assert detail["proof"]["driveDestinationVerified"]
    assert detail["proof"]["originalBytesVerified"]
    assert not detail["proof"]["sourceVisualQaVerified"]
    assert not detail["proof"]["claimAllowed"]
    assert "private diagnostic message" not in repr(detail)
    setup[3].aclose.assert_awaited_once()

def test_drive_failure_does_not_spend_ai_quota(setup):
    from fastapi import HTTPException
    setup[1].side_effect=HTTPException(503,"private Drive detail")
    with pytest.raises(f.PreflightIncomplete) as err:
        execute(setup)
    detail=err.value.detail
    assert detail["failedStage"]=="drive_destination"
    assert detail["proof"]["sourceAnalysisVerified"]
    assert not detail["proof"]["driveDestinationVerified"]
    assert "private Drive detail" not in repr(detail)
    setup[2].analyze_single.assert_not_called()

def test_unconfigured_reviewer_distinct_from_provider_outage(setup):
    setup[3].require.side_effect=AiProviderUnavailableError("gemini")
    with pytest.raises(f.PreflightIncomplete) as err:
        execute(setup)
    assert err.value.detail["failedStage"]=="reviewer_registry"
    assert err.value.detail["failureCode"]=="ai_provider_not_configured"

def test_malformed_model_response_is_not_approval(setup):
    setup[2].analyze_single.return_value=AiMetadataAnalysisResult(
        {"inkOnly":"true"},"gemini","fixture")
    with pytest.raises(f.PreflightIncomplete) as err:
        execute(setup)
    assert err.value.detail["failedStage"]=="reviewer_response"
    assert err.value.detail["failureCode"]=="reviewer_response_schema_invalid"
    assert not err.value.detail["proof"]["sourceVisualQaVerified"]

def test_quota_deferral_exposes_actual_provider_retry_without_secret(setup):
    from datetime import datetime,timezone,timedelta
    from app.providers.ai.gemini import GeminiPoolTemporarilyUnavailable,GeminiModelUnavailable
    at=datetime.now(timezone.utc)+timedelta(minutes=10)
    setup[2].analyze_single.side_effect=GeminiPoolTemporarilyUnavailable(
        attempted_models=[],reasons_by_model={
            "fixture-model-1":GeminiModelUnavailable("fixture-model-1","project_rpd_exhausted",at),
            "fixture-model-2":GeminiModelUnavailable("fixture-model-2","rpd_exhausted",at)},
        earliest_retry_at=at)
    with pytest.raises(f.PreflightIncomplete) as err:
        execute(setup)
    d=err.value.detail
    assert d["retryAtUtc"]==at.isoformat()
    assert d["retryable"] is True
    assert d["poolReasonCounts"]=={"project_rpd_exhausted":1,"rpd_exhausted":1}
    assert d["proof"]["driveDestinationVerified"]
    assert not d["proof"]["sourceVisualQaVerified"]
    assert "fixture-model" not in repr(d)
    headers=f.retry_headers(d)
    assert 590<=int(headers["Retry-After"])<=600
    assert headers["Cache-Control"]=="private, no-store"
    assert "Retry-After" not in f.retry_headers({"retryAtUtc":"not-a-time"})
