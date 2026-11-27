import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException
from app.modules.etsy_spy import imagegen_drive as drive
from app.modules.etsy_spy import imagegen_root_sharing as operator

ROOT = drive.LINK_VIEWER_ROOT_ID
CHILD = "exampleChildFolder123"
OTHER = "exampleOtherFolder123"
EMAIL = "owner@example.test"
OWNER = {"id":"owner123", "type":"user", "role":"owner"}
NAMED = {"id":"named123", "type":"user", "role":"writer"}
VIEW = {"id":"anyoneWithLink", "type":"anyone", "role":"reader", "allowFileDiscovery":False}


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setenv("CAM_ETSY_IMAGEGEN_DRIVE_FOLDER_ID", ROOT)
    monkeypatch.setenv("CAM_ETSY_IMAGEGEN_DRIVE_OWNER_EMAIL", EMAIL)


def metadata(folder=ROOT, permissions=None, parents=None):
    return {"id":folder, "mimeType":"application/vnd.google-apps.folder",
            "trashed":False, "ownedByMe":True,
            "owners":[{"emailAddress":EMAIL}],
            "capabilities":{"canAddChildren":True,"canShare":True},
            "parents":parents or [],
            "permissions":copy.deepcopy(permissions if permissions is not None else [OWNER,NAMED])}


class VerificationClient:
    def __init__(self, payload, account=EMAIL):
        self.payload=payload
        self.account=account
        self.deleted=[]
    async def __aenter__(self):
        return self
    async def __aexit__(self,*args):
        return False
    async def get(self,url,**kwargs):
        if url.endswith("/about"):
            data={"user":{"emailAddress":self.account}}
        else:
            assert "allowFileDiscovery" in kwargs["params"]["fields"]
            data=self.payload
        return SimpleNamespace(status_code=200,json=lambda:data)
    async def delete(self,url,**kwargs):
        self.deleted.append(url)
        raise AssertionError("No mutation allowed")


def verify(payload, folder=ROOT, account=EMAIL, **kw):
    client=VerificationClient(payload,account)
    with patch.object(drive.httpx,"AsyncClient",return_value=client):
        return asyncio.run(drive.verify_imagegen_oauth_token("test",folder,**kw))


def test_exact_root_restricted_or_viewer():
    assert verify(metadata(),return_sharing_status=True) == "restricted"
    assert verify(metadata(permissions=[OWNER,NAMED,VIEW]),return_sharing_status=True) == "link_viewer"


def test_oauth_reconnect_preserves_viewer():
    assert verify(metadata(permissions=[OWNER,NAMED,VIEW]),
                  restrict_public_sharing=True, allow_public_sharing=True) is True


@pytest.mark.parametrize("permission",[
    {**VIEW,"role":"writer"},{**VIEW,"role":"commenter"},
    {**VIEW,"allowFileDiscovery":True},{**VIEW,"allowFileDiscovery":None},
    {**VIEW,"type":"domain"},
])
def test_unsafe_root_acl_blocked(permission):
    with pytest.raises(HTTPException,match="Viewer"):
        verify(metadata(permissions=[OWNER,permission]))
    assert verify(metadata(permissions=[OWNER,permission]),
                  restrict_public_sharing=True,allow_public_sharing=True) is False


def test_wrong_owner_or_write_capability_blocked():
    for payload,account in [(metadata(permissions=[OWNER,VIEW]),"other@example.test"),
                            ({**metadata(permissions=[OWNER,VIEW]),"owners":[{"emailAddress":"other@example.test"}]},EMAIL),
                            ({**metadata(permissions=[OWNER,VIEW]),"capabilities":{"canAddChildren":False}},EMAIL)]:
        with pytest.raises(HTTPException):
            verify(payload,account=account)


def test_other_root_not_allowed(monkeypatch):
    monkeypatch.setenv("CAM_ETSY_IMAGEGEN_DRIVE_FOLDER_ID",OTHER)
    with pytest.raises(HTTPException,match="Restricted"):
        verify(metadata(OTHER,[OWNER,VIEW]),folder=OTHER)
    with pytest.raises(HTTPException,match="Restricted"):
        verify(metadata(ROOT,[OWNER,VIEW]),folder=ROOT)


def test_child_requires_explicit_verified_parent():
    data=metadata(CHILD,[OWNER,VIEW],parents=[ROOT])
    assert verify(data,folder=CHILD,verified_parent=ROOT,return_sharing_status=True)=="link_viewer"
    with pytest.raises(HTTPException,match="Restricted"):
        verify(data,folder=CHILD)
    with pytest.raises(HTTPException,match="Restricted"):
        verify({**data,"parents":[OTHER]},folder=CHILD,verified_parent=ROOT)


class RootClient:
    def __init__(self,data):
        self.data=copy.deepcopy(data)
        self.posts=[]
    async def get(self,url,**kwargs):
        assert url==operator.API+"/"+ROOT
        return httpx.Response(200,json=self.data)
    async def post(self,url,**kwargs):
        assert url==operator.API+"/"+ROOT+"/permissions"
        assert kwargs["json"]=={"type":"anyone","role":"reader","allowFileDiscovery":False}
        self.posts.append(url)
        self.data["permissions"].append(dict(VIEW))
        return httpx.Response(200,json={"id":VIEW["id"]})


def test_operator_check_then_grant_idempotent():
    async def run():
        client=RootClient(metadata())
        assert (await operator.ensure_root_viewer(client,ROOT))["state"]=="restricted"
        assert not client.posts
        result=await operator.ensure_root_viewer(client,ROOT,apply=True)
        assert result["changed"] and result["state"]=="link_viewer"
        assert client.data["permissions"]==[OWNER,NAMED,VIEW]
        result=await operator.ensure_root_viewer(client,ROOT,apply=True)
        assert result["changed"] is False and len(client.posts)==1
    asyncio.run(run())


def test_operator_rejects_foreign_root_and_unsafe_permissions():
    async def run():
        client=RootClient(metadata())
        with pytest.raises(HTTPException):
            await operator.ensure_root_viewer(client,OTHER,apply=True)
        client=RootClient(metadata(permissions=[OWNER,{**VIEW,"role":"writer"}]))
        with pytest.raises(HTTPException):
            await operator.ensure_root_viewer(client,ROOT,apply=True)
        assert not client.posts
    asyncio.run(run())


def test_operator_requires_can_share():
    async def run():
        data=metadata()
        data["capabilities"]["canShare"]=False
        client=RootClient(data)
        with pytest.raises(HTTPException):
            await operator.ensure_root_viewer(client,ROOT,apply=True)
        assert not client.posts
    asyncio.run(run())
