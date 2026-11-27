"""Route-level tests using only SQLite memory and mocked Google calls."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.core.database import Base, get_db
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.etsy_spy.model import EtsySpyPicked
from app.modules.etsy_spy.router import router
from app.modules.etsy_spy import sms_video_share as service
from app.modules.muse_bridge.model import CamMuseJob

LISTING = "4380517795"
TENANT = "test-tenant"
FILE = "exampleFile123"
SHA = "b" * 64
URL = "/api/v1/etsy-spy/listings/" + LISTING + "/sms-video-share"
BODY = dict(action="prepare", platform="TiktokShop", aspect="9:16",
            expected_file_id=FILE, expected_sha256=SHA, confirm=True)


@pytest.fixture
def scenario(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[x.__table__ for x in
                            (EtsySpyPicked, CamMuseJob, AuthAuditEventModel)])
    db = Session(engine)
    now = datetime.now(timezone.utc)
    db.add(EtsySpyPicked(tenant_id=TENANT, listing_key=LISTING, picked_at=now))
    db.add(CamMuseJob(tenant_id=TENANT, listing_key=LISTING, picked_at=now,
        title="Test", source_state="ready", status="submitted", artwork_sha256="a"*64,
        output_receipts={"drive_uploads":{"9:16":{"storageVerified":True,
        "storage_provider":"google_drive_managed","contentType":"video/mp4",
        "drive_file_id":FILE,"drive_folder_id":"exampleFolder123","sha256":SHA}}}))
    db.commit()
    principal = SimpleNamespace(user_id="actor-test", active_tenant_id=TENANT,
        effective_permissions=frozenset({"etsy_spy.manage"}), platform_admin=False)
    mock = AsyncMock(return_value={"state":"viewer_link_verified"})
    monkeypatch.setattr(service, "live", mock)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_authenticated_principal] = lambda: principal
    with TestClient(app) as client:
        yield client, db, principal, mock
    db.close()
    engine.dispose()


def test_confirmed_request_audited(scenario):
    client, db, principal, mock = scenario
    r = client.post(URL, json=BODY)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "viewer_link_verified"
    assert r.headers["cache-control"] == "no-store"
    assert mock.await_count == 1
    assert mock.await_args.args[0].file == FILE
    assert mock.await_args.args[0].tenant == TENANT
    events = db.scalars(select(AuthAuditEventModel).order_by(AuthAuditEventModel.occurred_at)).all()
    assert [e.action for e in events] == ["sms_video_share_attempt", "sms_video_share_complete"]
    assert all(e.actor_id == principal.user_id and e.tenant_id == TENANT for e in events)


def test_manage_required(scenario):
    client, db, principal, mock = scenario
    principal.effective_permissions = frozenset({"etsy_spy.read"})
    assert client.post(URL, json=BODY).status_code == 403
    mock.assert_not_awaited()


def test_other_tenant(scenario):
    client, db, principal, mock = scenario
    principal.active_tenant_id = "other-tenant"
    assert client.post(URL, json=BODY).status_code == 404
    mock.assert_not_awaited()


@pytest.mark.parametrize("body,status", [
    ({**BODY,"confirm":False},422),
    ({**BODY,"expected_sha256":"c"*64},409),
    ({**BODY,"expected_file_id":"differentFile123"},409),
    ({**BODY,"aspect":"16:9"},422),
])
def test_invalid_proof(scenario, body, status):
    client, db, principal, mock = scenario
    assert client.post(URL,json=body).status_code == status
    mock.assert_not_awaited()


def test_browser_cross_site_rejected(scenario):
    client, db, principal, mock = scenario
    assert client.post(URL,json=BODY,headers={"Sec-Fetch-Site":"cross-site"}).status_code == 415
    assert client.post(URL,content=json.dumps(BODY),headers={"Content-Type":"text/plain"}).status_code == 415
    mock.assert_not_awaited()


def test_failed_provider_audited(scenario):
    client, db, principal, mock = scenario
    mock.side_effect = service.ShareError("SMS_VIDEO_VIEW_PAGE_UNAVAILABLE",502)
    assert client.post(URL,json=BODY).status_code == 502
    events = db.scalars(select(AuthAuditEventModel).order_by(AuthAuditEventModel.occurred_at)).all()
    assert [e.action for e in events] == ["sms_video_share_attempt","sms_video_share_failed"]
    assert events[-1].detail_json["code"] == "SMS_VIDEO_VIEW_PAGE_UNAVAILABLE"
