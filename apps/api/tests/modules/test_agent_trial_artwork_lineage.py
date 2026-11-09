"""3C.3B2C1: independently read Stage1 metadata + remote byte integrity.

No QA review is written and no candidate is promoted. A valid Creative Brief
receipt alone is not a verified generated-artwork outcome.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.domain.providers.contracts import StoredAssetReadStream
from app.modules.agent_harness.model import (
    AgentTrialExposureModel, AgentTrialQaReviewModel,
)
from app.modules.agent_harness.trial_artwork_bytes import verify_stored_artwork_bytes
from app.modules.agent_harness.trial_artwork_lineage import (
    MAX_ARTWORK_BYTES, TrialArtworkLineageError, artwork_lineage, public_lineage_status,
)
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.creative_brief import validate_brief
from app.modules.realistic_review_ugc.creative_v3_generation import SCHEMA
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcKeywordImageJobModel,
)
from tests.modules.realistic_review_ugc.test_creative_v3_generation import brief as sample_brief
from tests.modules.test_agent_holdout_registration import STUDY, case
from tests.modules.test_agent_trial_verified_control_capture import _setup, _record, _count


def _png() -> bytes:
    image = Image.new("RGBA", (256, 128), (0, 0, 0, 0))
    memory = BytesIO()
    image.save(memory, format="PNG")
    return memory.getvalue()


class FakeStorage:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls = []
        self.closed = 0

    async def open_asset(self, input):
        self.calls.append(input)
        async def chunks():
            for position in range(0, len(self.payload), 117):
                yield self.payload[position:position + 117]
        async def close():
            self.closed += 1
        return StoredAssetReadStream(
            body=chunks(), close=close,
            content_type="image/png", size_bytes=len(self.payload),
        )


def _ready(case, monkeypatch):
    client, db, principal, flags = case
    db, brief_task, creative_brief, keyword, settings = _setup(case, monkeypatch)
    now = datetime.now(timezone.utc)
    creative_brief.brief_json = sample_brief(keyword.keyword)
    creative_brief.completed_at = now - timedelta(minutes=2)
    db.commit()
    assert _record(db, brief_task, keyword, settings)
    brief_task.status = "completed"
    db.commit()

    payload = creative_brief.brief_json
    normalized = validate_brief(payload, keyword.keyword).model_dump(mode="json")
    import json
    source_sha = hashlib.sha256(json.dumps(
        normalized, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    keyword_sha = hashlib.sha256(keyword.keyword.encode("utf-8")).hexdigest()
    fingerprint = hashlib.sha256(b"pinned-creative-plan").hexdigest()
    image_job = RrugcKeywordImageJobModel(
        id=str(uuid4()), tenant_id="boostgame",
        keyword_id=keyword.id, keyword_text=keyword.keyword,
        skill_source="local", skill_name="creative-v3",
        prompt_text="pinned", status="completed", created_by_user_id="operator",
    )
    db.add(image_job)
    db.flush()
    task = ProcessingJobModel(
        id=str(uuid4()), tenant_id="boostgame",
        job_type="rrugc_keyword_image_generate",
        entity_type="rrugc_keyword_image_job", entity_id=image_job.id,
        idempotency_key=str(uuid4()), priority=20,
        payload_json={"expected_output_count": 9, "creative_v3": {
            "brief_id": creative_brief.id, "source_sha256": source_sha,
            "fingerprint": fingerprint, "keyword_sha256": keyword_sha,
            "schema_version": SCHEMA, "provider": "codex",
        }},
        status="completed", max_attempts=1,
    )
    db.add(task)
    db.flush()
    image_job.processing_job_id = task.id
    png = _png()
    output = RrugcImageOutputVersionModel(
        id=str(uuid4()), tenant_id="boostgame", stage="stage1",
        job_id=image_job.id, processing_job_id=task.id,
        version=1, remote_file_id="drive-test-" + str(uuid4()),
        content_type="image/png", size_bytes=len(png),
        width=256, height=128, output_name="design_01.png",
        font_info_json={
            "engine": "creative-v3", "source": "creative-brief-v1",
            "brief_sha256": source_sha, "plan_fingerprint": fingerprint,
            "content_sha256": hashlib.sha256(png).hexdigest(),
            "technical_qa_passed": True,
            "human_review_required": True, "image_spelling_verified": False,
        },
        created_at=now + timedelta(minutes=1),
    )
    db.add(output)
    db.commit()
    return client, db, flags, keyword, creative_brief, image_job, task, output, png


def _claim(db, output):
    db.expire_all()
    return artwork_lineage(db, tenant_id="boostgame", study_id=STUDY,
                           output_version_id=output.id)


def test_db_lineage_is_not_claimed_as_verified_image_bytes(case, monkeypatch):
    client, db, flags, keyword, brief, job, task, output, png = _ready(case, monkeypatch)
    claim = _claim(db, output)
    assert claim.image_job_id == job.id
    assert claim.image_processing_job_id == task.id
    assert claim.brief_processing_job_id != task.id
    assert claim.sha256 == hashlib.sha256(png).hexdigest()
    response = public_lineage_status(claim)
    assert response["lineage_status"] == "database_linked"
    assert not response["storage_bytes_verified"]
    assert not response["independent_qa_verified"]
    assert not response["eligible_for_a_b_evaluation"]
    assert "stored_artwork_bytes_not_verified" in response["evaluation_blockers"]
    assert keyword.keyword not in str(response) and keyword.id not in str(response)
    assert _count(db, AgentTrialQaReviewModel) == 0


def test_authorized_lineage_endpoint_and_bytes_feature_gate(case, monkeypatch):
    client, db, flags, keyword, brief, job, task, output, png = _ready(case, monkeypatch)
    url = ("/api/v1/admin/agent-harness/boostgame/trials/stage1/"
           + STUDY + "/artwork/" + output.id)
    get = client.get(url + "/lineage")
    assert get.status_code == 200, get.text
    assert get.json()["storage_bytes_verified"] is False
    assert client.post(url + "/verify-bytes").status_code == 404
    assert client.get(url.replace("boostgame", "another") + "/lineage").status_code == 403
    assert client.get(url.replace(STUDY, "study-unrelated") + "/lineage").status_code == 409
    assert _count(db, AgentTrialQaReviewModel) == 0


def test_stream_verification_checks_actual_png_sha_and_closes(case, monkeypatch):
    client, db, flags, keyword, brief, job, task, output, png = _ready(case, monkeypatch)
    storage = FakeStorage(png)
    verified = asyncio.run(verify_stored_artwork_bytes(storage, _claim(db, output)))
    assert verified["storage_bytes_verified"] is True
    assert verified["independent_qa_verified"] is False
    assert verified["eligible_for_a_b_evaluation"] is False
    assert storage.closed == 1 and len(storage.calls) == 1
    assert storage.calls[0].remote_file_id == output.remote_file_id
    assert _count(db, AgentTrialQaReviewModel) == 0


@pytest.mark.parametrize("corrupt", ["hash", "oversized", "not_png", "png_crc", "dimensions"])
def test_remote_storage_integrity_fails_closed(case, monkeypatch, corrupt):
    client, db, flags, keyword, brief, job, task, output, png = _ready(case, monkeypatch)
    claim = _claim(db, output)
    if corrupt == "hash":
        storage = FakeStorage(bytes(bytearray(png)[:-2]) + b"XX")
    elif corrupt == "oversized":
        storage = FakeStorage(png + b"\x00" * (MAX_ARTWORK_BYTES + 1))
    elif corrupt == "not_png":
        body = b"\x00" * len(png)
        from dataclasses import replace
        claim = replace(claim, sha256=hashlib.sha256(body).hexdigest())
        storage = FakeStorage(body)
    elif corrupt == "png_crc":
        # Corrupt a byte inside IDAT while preserving the original CRC.
        # The digest is then updated to prove PNG integrity is independent.
        body = png[:60] + bytes([png[60] ^ 0x10]) + png[61:]
        from dataclasses import replace
        claim = replace(claim, sha256=hashlib.sha256(body).hexdigest())
        storage = FakeStorage(body)
    else:
        from dataclasses import replace
        claim = replace(claim, width=999)
        storage = FakeStorage(png)
    with pytest.raises(TrialArtworkLineageError):
        asyncio.run(verify_stored_artwork_bytes(storage, claim))
    assert storage.closed == 1
    assert _count(db, AgentTrialQaReviewModel) == 0


@pytest.mark.parametrize("tamper", [
    "other_tenant", "different_study", "wrong_processing", "other_source",
    "wrong_brief", "missing_exposure", "bad_metadata_sha",
    "bad_image_status", "stale_brief", "job_payload_mutated",
    "wrong_image_type", "missing_remote", "wrong_brief_job", "cancelled_study",
])
def test_lineage_rejects_stale_forged_cross_tenant_or_unlinked_art(case, monkeypatch, tamper):
    client, db, flags, keyword, brief, job, task, output, png = _ready(case, monkeypatch)
    if tamper == "other_tenant":
        output.tenant_id = "other"
    elif tamper == "different_study":
        pass
    elif tamper == "wrong_processing":
        output.processing_job_id = str(uuid4())
    elif tamper == "other_source":
        output.font_info_json = {**output.font_info_json, "brief_sha256": "a" * 64}
    elif tamper == "wrong_brief":
        brief.brief_json = sample_brief("CHANGED WORDS")
    elif tamper == "missing_exposure":
        db.delete(db.scalar(select(AgentTrialExposureModel)))
    elif tamper == "bad_metadata_sha":
        output.font_info_json = {**output.font_info_json, "content_sha256": "missing"}
    elif tamper == "bad_image_status":
        job.status = "failed"
    elif tamper == "stale_brief":
        brief.processing_job_id = str(uuid4())
    elif tamper == "job_payload_mutated":
        task.payload_json = {**task.payload_json, "creative_v3": {
            **task.payload_json["creative_v3"], "source_sha256": "b" * 64,
        }}
    elif tamper == "wrong_image_type":
        output.stage = "stage2"
    elif tamper == "missing_remote":
        output.remote_file_id = ""
    elif tamper == "wrong_brief_job":
        exposure = db.scalar(select(AgentTrialExposureModel))
        db.get(ProcessingJobModel, exposure.processing_job_id).status = "pending"
    elif tamper == "cancelled_study":
        from app.modules.agent_harness.model import AgentHoldoutStudyModel
        db.get(AgentHoldoutStudyModel, ("boostgame", STUDY)).status = "cancelled"
    db.commit()
    with pytest.raises(TrialArtworkLineageError):
        artwork_lineage(
            db, tenant_id="boostgame",
            study_id=("other-study" if tamper == "different_study" else STUDY),
            output_version_id=output.id,
        )
    assert _count(db, AgentTrialQaReviewModel) == 0


def test_verify_requires_explicit_flag_and_respects_emergency_stop(case, monkeypatch):
    client, db, flags, keyword, brief, job, task, output, png = _ready(case, monkeypatch)
    flags.AGENT_HARNESS_TRIAL_ARTWORK_VERIFY_ENABLED = True
    flags.AI_EMERGENCY_STOP_ENABLED = True
    url = ("/api/v1/admin/agent-harness/boostgame/trials/stage1/"
           + STUDY + "/artwork/" + output.id + "/verify-bytes")
    from app.modules.storage import provider_factory
    opens = []
    monkeypatch.setattr(
        provider_factory, "build_managed_storage_provider",
        lambda _settings: opens.append(True),
    )
    assert client.post(url).status_code == 404
    assert opens == []


def test_artwork_changed_during_drive_read_is_never_verified(case, monkeypatch):
    client, db, flags, keyword, brief, job, task, output, png = _ready(case, monkeypatch)
    flags.AGENT_HARNESS_TRIAL_ARTWORK_VERIFY_ENABLED = True
    from app.modules.storage import provider_factory

    class ChangedDuringRead(FakeStorage):
        async def open_asset(self, input):
            stream = await super().open_asset(input)
            output.remote_file_id = "changed-after-preflight"
            db.commit()
            return stream

    storage = ChangedDuringRead(png)
    monkeypatch.setattr(provider_factory, "build_managed_storage_provider", lambda _settings: storage)
    url = ("/api/v1/admin/agent-harness/boostgame/trials/stage1/"
           + STUDY + "/artwork/" + output.id + "/verify-bytes")
    response = client.post(url)
    assert response.status_code == 409
    assert response.json()["detail"] == "trial_artifact_lineage_changed"
    assert storage.closed == 1
    assert _count(db, AgentTrialQaReviewModel) == 0


def test_eligible_route_verifies_current_remote_bytes_but_not_qa(case, monkeypatch):
    client, db, flags, keyword, brief, job, task, output, png = _ready(case, monkeypatch)
    flags.AGENT_HARNESS_TRIAL_ARTWORK_VERIFY_ENABLED = True
    from app.modules.storage import provider_factory
    storage = FakeStorage(png)
    monkeypatch.setattr(provider_factory, "build_managed_storage_provider", lambda _settings: storage)
    url = ("/api/v1/admin/agent-harness/boostgame/trials/stage1/"
           + STUDY + "/artwork/" + output.id + "/verify-bytes")
    res = client.post(url)
    assert res.status_code == 200, res.text
    assert res.json()["storage_bytes_verified"] is True
    assert res.json()["independent_qa_verified"] is False
    assert res.json()["eligible_for_a_b_evaluation"] is False
    assert len(storage.calls) == 1 and storage.closed == 1
    assert keyword.keyword not in res.text
    assert _count(db, AgentTrialQaReviewModel) == 0
