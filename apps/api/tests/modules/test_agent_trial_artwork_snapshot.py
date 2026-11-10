"""3C.3B2C2A: bounded copy, immutable lineage, human-only opt-in and rollback.

No generation, QA verdict or A/B promotion is ever performed by these tests.
"""
from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.domain.providers.contracts import StoredAsset, StoredAssetReadStream
from app.modules.agent_harness.model import (
    AgentTrialArtworkSnapshotModel, AgentTrialQaReviewModel,
)
from app.modules.agent_harness.trial_artwork_lineage import artwork_lineage
from app.modules.agent_harness.trial_artwork_snapshot import (
    TrialArtworkSnapshotError, capture_snapshot, get_snapshot,
)
from app.modules.realistic_review_ugc.model import RrugcImageOutputVersionModel
from tests.modules.test_agent_trial_artwork_lineage import _ready, _claim
from tests.modules.test_agent_holdout_registration import STUDY, case


class FakeCopyStorage:
    def __init__(self, source_id: str, original: bytes, *, corrupt_copy: bool = False):
        self.objects = {source_id: original}
        self.uploads = 0
        self.opened = 0
        self.closed = 0
        self.corrupt_copy = corrupt_copy

    async def open_asset(self, data):
        self.opened += 1
        payload = self.objects[data.remote_file_id]
        async def body():
            for offset in range(0, len(payload), 197):
                yield payload[offset:offset + 197]
        async def close():
            self.closed += 1
        return StoredAssetReadStream(
            body=body(), close=close, content_type="image/png",
            size_bytes=len(payload),
        )

    async def store_asset(self, data):
        self.uploads += 1
        content = b"".join([piece async for piece in data.body])
        assert content == self.objects[next(iter(self.objects))]
        assert data.content_hash == hashlib.sha256(content).hexdigest()
        assert data.asset_id.startswith("agent-trial-qa-snapshot:")
        assert data.tenant_id == "boostgame"
        target = "stored-qa-copy-001"
        self.objects[target] = (
            content[:-2] + b"XX" if self.corrupt_copy else content
        )
        return StoredAsset(
            storage_key="google-drive:" + target,
            content_hash=data.content_hash,
            remote_file_id=target,
            size_bytes=len(content),
        )


def _setup_snapshot(case, monkeypatch):
    client, db, flags, keyword, brief, job, task, output, image = _ready(case, monkeypatch)
    AgentTrialArtworkSnapshotModel.__table__.create(bind=db.bind, checkfirst=True)
    return client, db, flags, output, image


def test_full_copy_is_independent_and_survives_output_regenerate_delete(case, monkeypatch):
    client, db, flags, output, image = _setup_snapshot(case, monkeypatch)
    claim = _claim(db, output)
    storage = FakeCopyStorage(output.remote_file_id, image)
    result = asyncio.run(capture_snapshot(
        db, storage, claim=claim, actor_id="qa-capture-operator",
    ))
    assert result["snapshot_status"] == "verified"
    assert result["stored_copy_verified"] is True
    assert result["independent_qa_verified"] is False
    assert result["eligible_for_a_b_evaluation"] is False
    assert result["automatic_promotion_enabled"] is False
    assert storage.opened == storage.closed == 2 and storage.uploads == 1
    assert db.scalar(select(func.count()).select_from(AgentTrialQaReviewModel)) == 0

    repeated = asyncio.run(capture_snapshot(
        db, storage, claim=claim, actor_id="qa-capture-operator",
    ))
    assert repeated["created"] is False and storage.uploads == 1
    assert repeated["snapshot_id"] == result["snapshot_id"]

    # Output History can be destroyed by Regenerate without a FK from the
    # independently copied snapshot. It is still auditable by exact ID.
    db.delete(output)
    db.commit()
    assert db.get(RrugcImageOutputVersionModel, claim.output_version_id) is None
    still = get_snapshot(
        db, tenant_id="boostgame", study_id=STUDY,
        output_version_id=claim.output_version_id,
    )
    assert still.status == "verified"
    assert still.artifact_sha256 == hashlib.sha256(image).hexdigest()
    assert still.snapshot_remote_file_id == "stored-qa-copy-001"
    assert get_snapshot(
        db, tenant_id="another", study_id=STUDY,
        output_version_id=claim.output_version_id,
    ) is None


def test_corrupt_copied_bytes_leave_reserved_and_never_auto_retry(case, monkeypatch):
    client, db, flags, output, image = _setup_snapshot(case, monkeypatch)
    claim = _claim(db, output)
    storage = FakeCopyStorage(output.remote_file_id, image, corrupt_copy=True)
    with pytest.raises(ValueError):
        asyncio.run(capture_snapshot(
            db, storage, claim=claim, actor_id="capture-operator",
        ))
    db.rollback()
    row = get_snapshot(
        db, tenant_id="boostgame", study_id=STUDY,
        output_version_id=claim.output_version_id,
    )
    assert row is not None and row.status == "reserved"
    assert row.snapshot_remote_file_id is None
    assert storage.uploads == 1
    with pytest.raises(TrialArtworkSnapshotError, match="manual_recovery"):
        asyncio.run(capture_snapshot(
            db, storage, claim=claim, actor_id="capture-operator",
        ))
    assert storage.uploads == 1
    assert db.scalar(select(func.count()).select_from(AgentTrialQaReviewModel)) == 0


def test_snapshot_is_opt_in_and_read_endpoint_is_privacy_scoped(case, monkeypatch):
    client, db, flags, output, image = _setup_snapshot(case, monkeypatch)
    base = (
        "/api/v1/admin/agent-harness/boostgame/trials/stage1/"
        + STUDY + "/artwork/" + output.id + "/snapshot"
    )
    response = client.get(base)
    assert response.status_code == 200
    assert response.json()["snapshot_status"] == "not_captured"
    assert client.get(base.replace("boostgame","another")).status_code == 403
    assert client.post(base, json={
        "dry_run": True, "reason": "Review original image provenance",
    }).status_code == 404
    flags.AGENT_HARNESS_TRIAL_ARTWORK_VERIFY_ENABLED = True
    flags.AGENT_HARNESS_TRIAL_ARTWORK_SNAPSHOT_ENABLED = True
    response = client.post(base, json={
        "dry_run": True, "reason": "Review original image provenance",
    })
    assert response.status_code == 200, response.text
    assert response.json()["snapshot_status"] == "ready_for_explicit_capture"
    assert response.json()["storage_bytes_verified"] is False
    assert response.json()["automatic_promotion_enabled"] is False
    assert db.scalar(select(func.count()).select_from(AgentTrialArtworkSnapshotModel)) == 0
    for bad in [
        {"dry_run": "false", "reason": "Review original image provenance"},
        {"dry_run": False, "reason": "bad"},
        {"dry_run": False, "reason": "Review\noriginal provenance"},
        {"dry_run": False, "reason": "Review original image provenance", "arm": "challenger"},
    ]:
        assert client.post(base, json=bad).status_code == 422


def test_real_mutating_route_copies_only_user_requested_png(case, monkeypatch):
    client, db, flags, output, image = _setup_snapshot(case, monkeypatch)
    flags.AGENT_HARNESS_TRIAL_ARTWORK_VERIFY_ENABLED = True
    flags.AGENT_HARNESS_TRIAL_ARTWORK_SNAPSHOT_ENABLED = True
    storage = FakeCopyStorage(output.remote_file_id, image)
    from app.modules.storage import provider_factory
    monkeypatch.setattr(provider_factory, "build_managed_storage_provider", lambda settings: storage)
    base = (
        "/api/v1/admin/agent-harness/boostgame/trials/stage1/"
        + STUDY + "/artwork/" + output.id + "/snapshot"
    )
    response = client.post(base, json={
        "dry_run": False, "reason": "Capture independent preserved PNG",
    })
    assert response.status_code == 200, response.text
    assert response.json()["snapshot_status"] == "verified"
    assert storage.uploads == 1
    replay = client.post(base, json={
        "dry_run": False, "reason": "Capture independent preserved PNG",
    })
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert storage.uploads == 1
