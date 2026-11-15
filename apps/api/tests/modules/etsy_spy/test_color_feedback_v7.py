"""V7 color preference API: explicit, tenant-bound, SHA-verified decisions."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.modules.etsy_spy.router import (
    save_design_color_feedback, _color_feedback_asset,
    list_design_color_feedback,
)

TENANT = "creative-assets-prod"
JOB = "11111111-1111-4111-8111-111111111111"
ASSET = "a" * 24
SHA = "b" * 64
LISTING = "123456789"


class FakeDb:
    def __init__(self, *, picked=True, digest=SHA):
        self.picked = picked
        self.job = SimpleNamespace(payload_json={"design_assets": [
            {"id": ASSET, "output_sha256": digest},
        ]})
        self.added = []
        self.commits = 0

    def get(self, cls, key):
        return SimpleNamespace() if self.picked and key == (TENANT, LISTING) else None

    def scalar(self, query):
        return self.job

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1

    def scalars(self, query):
        return SimpleNamespace(all=lambda: self.added)


def make_request(body: dict | list):
    raw = json.dumps(body).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}
    return Request({
        "type": "http", "method": "POST",
        "headers": [(b"content-length", str(len(raw)).encode())],
        "path": "/api/v1/etsy-spy/listings/" + LISTING + "/design-color-feedback",
    }, receive=receive)


def body(**extra):
    return {
        "job_id": JOB, "asset_id": ASSET, "output_sha256": SHA,
        "action": "selected", "color_id": "ivory",
        "scope": "design", "print_method": "dtg", **extra,
    }


def principal():
    return SimpleNamespace(active_tenant_id=TENANT)


def test_save_explicit_color_feedback_stores_only_bounded_event():
    db = FakeDb()
    result = asyncio.run(save_design_color_feedback(
        LISTING, make_request(body()), principal(), db,
    ))
    assert result == {"recorded": True, "action": "selected", "color_id": "ivory"}
    assert db.commits == 1 and len(db.added) == 1
    row = db.added[0]
    assert (row.tenant_id, row.listing_key, row.output_sha256) == (TENANT, LISTING, SHA)
    assert (row.color_id, row.scope, row.action, row.print_method) == (
        "ivory", "design", "selected", "dtg",
    )
    assert "title" not in row.__dict__ and "image_bytes" not in row.__dict__


@pytest.mark.parametrize("change", [
    {"color_id": "not-a-sms-color"},
    {"color_id": {"unexpected": "object"}},
    {"print_method": "untrusted"},
    {"action": "page_view"},
    {"job_id": "bad"},
    {"output_sha256": "0" * 64},
    {"scope": "any"},
])
def test_rejects_malformed_or_forged_feedback(change):
    db = FakeDb()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(save_design_color_feedback(
            LISTING, make_request(body(**change)), principal(), db,
        ))
    assert exc.value.status_code in (404, 422)
    assert not db.added


def test_cannot_record_feedback_for_unpicked_listing():
    db = FakeDb(picked=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(save_design_color_feedback(
            LISTING, make_request(body()), principal(), db,
        ))
    assert exc.value.status_code == 404


def test_sha_change_invalidates_old_design_feedback():
    db = FakeDb(digest="f" * 64)
    assert not _color_feedback_asset(db, TENANT, LISTING, JOB, ASSET, SHA)
    with pytest.raises(HTTPException):
        asyncio.run(save_design_color_feedback(
            LISTING, make_request(body()), principal(), db,
        ))


def test_list_projects_only_color_and_last_action():
    db = FakeDb()
    asyncio.run(save_design_color_feedback(
        LISTING, make_request(body()), principal(), db,
    ))
    result = list_design_color_feedback(
        LISTING, job_id=JOB, asset_id=ASSET,
        output_sha256=SHA, principal=principal(), db=db,
    )
    assert result == {
        "latest": {"ivory": "selected"},
        "counts": {"ivory": {"selected": 1, "rejected": 0}},
        "sample_limit": 200,
    }
