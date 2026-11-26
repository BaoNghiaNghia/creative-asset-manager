"""P2D bootstrap has no central schema, no write routes and no render permission."""
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.modules.image_orchestrator import read_api as api, policy as p
from app.modules.image_orchestrator.identity import ImageIdentity, image_identity

PREFIX="/api/v1/muse-bridge"


def fixture():
    app=FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[image_identity]=lambda:ImageIdentity("fixture","muse-01","muse")
    return app


def test_authenticated_discovery_is_explicitly_non_authoritative(monkeypatch):
    monkeypatch.delenv("CAM_IMAGE_READ_API_ENABLED",raising=False)
    with TestClient(fixture()) as client:
        for path in ("skill-registry","image-commands","image-work"):
            r=client.get(PREFIX+"/"+path)
            assert r.status_code==200,r.text
            data=r.json()
            assert r.headers["Cache-Control"]=="private, no-store"
            assert data["protocol"]==p.PROTOCOL
            assert data["status"]=="PREPARED_DISABLED"
            assert not data["claimEnabled"] and not data["imageClaimAllowed"]
            assert not data["rendererAllowed"] and not data["centralSchemaReady"]
        registry=client.get(PREFIX+"/skill-registry").json()
        assert registry["skillSha256"]==p.SKILL_SHA256 and len(registry["files"])==19
        assert registry["neckTagRole"]==4 and registry["publicationMinimumPx"]==2000
        assert registry["printBoxAuthority"]=="CAM_PRINT_BOX_SINGLE_AUTHORITY_V1"
        work=client.get(PREFIX+"/image-work").json()
        assert work["items"]==[] and work["queueAuthoritative"] is False
        assert work["centralPackCount"] is None and work["centralRoleCounts"] is None
        assert work["remainingWithoutImages"] is None
        assert client.get(PREFIX+"/image-work?limit=21").status_code==422


def test_no_auth_no_access_and_registry_failure(monkeypatch):
    monkeypatch.setenv("CAM_IMAGE_READ_API_ENABLED","true")
    app=fixture()
    app.dependency_overrides.pop(image_identity)
    with TestClient(app) as client:
        for route in ("skill-registry","image-commands","image-work"):
            assert client.get(PREFIX+"/"+route).status_code==401
    with TestClient(fixture()) as client,patch.object(api,"REGISTRY",api.REGISTRY.with_name("missing.json")):
        assert client.get(PREFIX+"/skill-registry").status_code==503


def test_no_pack_or_qa_without_migration(monkeypatch):
    monkeypatch.setenv("CAM_IMAGE_READ_API_ENABLED","true")
    params={"packId":"a"*64,"sku":"C1717"}
    with TestClient(fixture()) as client:
        for name in ("image-status","image-contract"):
            r=client.get(PREFIX+"/jobs/123456789/"+name,params=params)
            assert r.status_code==503 and r.json()["detail"]=="image_central_schema_not_migrated"
        r=client.get(PREFIX+"/jobs/123456789/images/01/qa-status",params={
            **params,"attemptId":"fixture","imageSha256":"b"*64})
        assert r.status_code==503
        for path in ("image-commands/fixture/claim","jobs/123456789/images/claim",
                     "jobs/123456789/images/01/start","jobs/123456789/images/01",
                     "image-commands/fixture/heartbeat","image-commands/fixture/complete"):
            assert client.post(PREFIX+"/"+path,json={}).status_code==404


def test_explicit_discovery_rollback(monkeypatch):
    monkeypatch.setenv("CAM_IMAGE_READ_API_ENABLED","false")
    with TestClient(fixture()) as client:
        for path in ("skill-registry","image-commands","image-work"):
            assert client.get(PREFIX+"/"+path).status_code==404
