from __future__ import annotations

import asyncio
import hashlib
import io
from contextlib import asynccontextmanager
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.main import app
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3RequestError
from PIL import Image

from app.modules.visual_search.contracts import VisualEmbedding
from app.modules.visual_search.elasticsearch import VisualSearchHit


class _Index:
    descriptor = __import__("app.modules.visual_search.encoder", fromlist=["SiglipVisualEncoder"]).SiglipVisualEncoder.descriptor
    calls = []

    def __init__(self, *_args, **_kwargs): pass
    async def get_document(self, _document_id):
        return {"_source": {"visual_embedding": [0.0] * self.descriptor.dimension}}
    async def search(self, embedding, **kwargs):
        self.calls.append((embedding, kwargs))
        return [
            VisualSearchHit("hit-a", "tenant-a", "other-a", 0.9, "b" * 64, "source-a"),
            VisualSearchHit("hit-b", "tenant-b", "other-b", 0.8, "c" * 64, "source-b"),
        ]
    async def aclose(self): pass


class _PendingIndex(_Index):
    async def get_document(self, _document_id):
        raise ElasticsearchV3RequestError("missing", status_code=404)


class _UploadEncoder:
    descriptor = _Index.descriptor

    def encode_image(self, _image):
        return VisualEmbedding(self.descriptor, tuple(0.0 for _ in range(self.descriptor.dimension)))


class _UploadEncoderClient:
    def get_encoder(self):
        return _UploadEncoder()


class _MemoryResolver:
    def __init__(self, content: bytes):
        self.content = content

    @asynccontextmanager
    async def open(self, **_kwargs):
        async def body():
            yield self.content
        yield SimpleNamespace(body=body())


def _png_bytes(size: int = 16) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (size, size), "white").save(output, format="PNG")
    return output.getvalue()


class VisualByAssetApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        with self.factory() as session:
            session.add(AssetModel(id="asset-a", tenant_id="tenant-a", content_hash="a" * 64))
            session.add(ExternalSourceModel(id="source-a", tenant_id="tenant-a", source_key="source-a", source_type="google_drive"))
            session.commit()
        self.client = TestClient(app)
        app.dependency_overrides[require_authenticated_principal] = lambda: CurrentPrincipal(
            user_id="user-a", active_tenant_id="tenant-a", membership_id="",
            external_identity=None, effective_roles=frozenset({"operator"}),
            effective_permissions=frozenset({"search.read"}), platform_admin=False,
            session_id="session", authorization_source="tenant_rbac",
        )
        _Index.calls.clear()

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()
        self.engine.dispose()

    def _settings(self, **changes):
        values = dict(VISUAL_SEARCH_ENABLED=True, ELASTICSEARCH_URL="http://elasticsearch.test")
        values.update(changes)
        return Settings(**values)

    def _post(self, payload):
        with patch("app.modules.visual_search.router.SessionLocal", self.factory),              patch("app.modules.visual_search.router.get_settings", return_value=self._settings()),              patch("app.modules.visual_search.router.VisualSearchElasticsearchIndex", _Index),              patch("app.modules.visual_search.router._hydrate_search_hits", return_value=[{"internal_asset_id": "other-a"}]):
            return self.client.post("/api/v1/search/visual/by-asset", json=payload)

    def _upload(self, *, payload=None, settings=None):
        app.state.visual_encoder_client = _UploadEncoderClient()
        try:
            with patch("app.modules.visual_search.router.SessionLocal", self.factory),                  patch("app.modules.visual_search.router.get_settings", return_value=settings or self._settings(VISUAL_SEARCH_UPLOAD_ENABLED=True)),                  patch("app.modules.visual_search.router.VisualSearchElasticsearchIndex", _Index),                  patch("app.modules.visual_search.router._hydrate_search_hits", return_value=[{"internal_asset_id": "other-a", "score": 0.9}]):
                return self.client.post(
                    "/api/v1/search/visual/upload",
                    params=payload or {},
                    files={"file": ("query.txt", _png_bytes(), "application/octet-stream")},
                )
        finally:
            app.state.visual_encoder_client = None

    def test_disabled_and_cross_tenant_asset_are_rejected(self):
        with patch("app.modules.visual_search.router.get_settings", return_value=self._settings(VISUAL_SEARCH_ENABLED=False)):
            response = self.client.post("/api/v1/search/visual/by-asset", json={"asset_id": "asset-a"})
        self.assertEqual(response.status_code, 503)
        response = self._post({"asset_id": "tenant-b-asset"})
        self.assertEqual(response.status_code, 404)
        with patch("app.modules.visual_search.router.SessionLocal", self.factory), \
             patch("app.modules.visual_search.router.get_settings", return_value=self._settings(VISUAL_SEARCH_CROP_ENABLED=True)):
            response = self.client.post(
                "/api/v1/search/visual/by-asset",
                json={"asset_id": "tenant-b-asset", "crop": {"x": 0, "y": 0, "width": 1, "height": 1}},
            )
        self.assertEqual(response.status_code, 404)

    def test_reuses_stored_embedding_excludes_query_and_preserves_filters(self):
        response = self._post({"asset_id": "asset-a", "external_source_id": "source-a", "filters": {"mime_type": ["image/jpeg"]}, "limit": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [{"internal_asset_id": "other-a"}])
        _embedding, kwargs = _Index.calls[-1]
        self.assertEqual(kwargs["exclude_asset_id"], "asset-a")
        self.assertIn({"terms": {"mime_type": ["image/jpeg"]}}, kwargs["scope"].access_filters)

    def test_viewer_cannot_use_query_asset_outside_folder_scope(self):
        app.dependency_overrides[require_authenticated_principal] = lambda: CurrentPrincipal(
            user_id="viewer-a", active_tenant_id="tenant-a", membership_id="membership-a",
            external_identity=None, effective_roles=frozenset({"viewer"}),
            effective_permissions=frozenset({"search.read"}), platform_admin=False,
            session_id="session", authorization_source="tenant_rbac",
        )
        response = self._post({"asset_id": "asset-a", "external_source_id": "source-a"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "visual_query_asset_not_found")

    def test_embedding_pending_and_elasticsearch_unavailable_are_controlled(self):
        with patch("app.modules.visual_search.router.SessionLocal", self.factory), \
             patch("app.modules.visual_search.router.get_settings", return_value=self._settings()), \
             patch("app.modules.visual_search.router.VisualSearchElasticsearchIndex", _PendingIndex):
            response = self.client.post("/api/v1/search/visual/by-asset", json={"asset_id": "asset-a"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "visual_query_embedding_pending")
        with patch("app.modules.visual_search.router.get_settings", return_value=self._settings(ELASTICSEARCH_URL="")):
            response = self.client.post("/api/v1/search/visual/by-asset", json={"asset_id": "asset-a"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "visual_search_unavailable")

    def test_upload_uses_safe_decode_encoder_and_existing_search_contract(self):
        response = self._upload(payload={"filters": '{"mime_type":["image/jpeg"]}', "limit": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["query_kind"], "upload")
        self.assertEqual(response.json()["items"], [{"internal_asset_id": "other-a"}])
        _embedding, kwargs = _Index.calls[-1]
        self.assertIn({"terms": {"mime_type": ["image/jpeg"]}}, kwargs["scope"].access_filters)
        with self.factory() as session:
            self.assertEqual(session.query(AssetModel).count(), 1)

    def test_crop_search_uses_post_orientation_pixels_for_upload_and_asset(self):
        content = _png_bytes(64)
        with self.factory() as session:
            session.add(AssetModel(
                id="crop-a", tenant_id="tenant-a",
                content_hash=hashlib.sha256(content).hexdigest(),
            ))
            session.add(SourceAssetModel(
                id="source-asset-crop", tenant_id="tenant-a",
                external_source_id="source-a", external_asset_id="provider-crop-a",
                filename="crop.png", mime_type="image/png",
            ))
            session.add(AssetSourceLinkModel(
                id="link-crop", tenant_id="tenant-a",
                asset_id="crop-a", source_asset_id="source-asset-crop",
            ))
            session.commit()
        app.state.visual_encoder_client = _UploadEncoderClient()
        app.state.visual_content_resolver = _MemoryResolver(content)
        crop = {"x": 0, "y": 0, "width": 1, "height": 1}
        try:
            with patch("app.modules.visual_search.router.SessionLocal", self.factory),                  patch("app.modules.visual_search.router.get_settings", return_value=self._settings(VISUAL_SEARCH_CROP_ENABLED=True)),                  patch("app.modules.visual_search.router.VisualSearchElasticsearchIndex", _Index),                  patch("app.modules.visual_search.router._hydrate_search_hits", return_value=[]):
                response = self.client.post(
                    "/api/v1/search/visual/by-asset",
                    json={"asset_id": "crop-a", "crop": crop},
                )
                self.assertEqual(response.status_code, 200)
                response = self.client.post(
                    "/api/v1/search/visual/upload",
                    params={"crop": '{"x":0,"y":0,"width":1,"height":1}'},
                    files={"file": ("query.png", content, "image/png")},
                )
        finally:
            app.state.visual_encoder_client = None
            app.state.visual_content_resolver = None
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["query_kind"], "upload")
        response = self._upload(
            payload={"crop": '{"x":0,"y":0,"width":0.1,"height":0.1}'},
            settings=self._settings(VISUAL_SEARCH_CROP_ENABLED=True),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "visual_crop_too_small")
        response = self._upload(
            payload={"crop": '{"x":0.9,"y":0,"width":0.2,"height":1}'},
            settings=self._settings(VISUAL_SEARCH_CROP_ENABLED=True),
        )
        self.assertEqual(response.status_code, 422)

    def test_upload_disabled_invalid_and_capacity_are_controlled(self):
        response = self._upload(settings=self._settings(VISUAL_SEARCH_UPLOAD_ENABLED=False))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "visual_search_operation_disabled")
        response = self._upload(payload={"filters": "not-json"})
        self.assertEqual(response.status_code, 422)
        with patch("app.modules.visual_search.router._ENCODER_CAPACITY", asyncio.Semaphore(0)):
            response = self._upload()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "visual_encoder_capacity")
        app.state.visual_encoder_client = _UploadEncoderClient()
        try:
            with patch("app.modules.visual_search.router.SessionLocal", self.factory),                  patch("app.modules.visual_search.router.get_settings", return_value=self._settings(VISUAL_SEARCH_UPLOAD_ENABLED=True)),                  patch("app.modules.visual_search.router.VisualSearchElasticsearchIndex", _Index):
                response = self.client.post(
                    "/api/v1/search/visual/upload",
                    files={"file": ("query.png", b"not-an-image", "image/png")},
                )
        finally:
            app.state.visual_encoder_client = None
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "visual_image_invalid")

    def test_cursor_is_tenant_request_bound(self):
        response = self._post({"asset_id": "asset-a", "limit": 1})
        self.assertEqual(response.status_code, 200)
        cursor = response.json()["next_cursor"]
        self.assertIsNotNone(cursor)
        response = self._post({"asset_id": "asset-a", "filters": {"extension": ["png"]}, "cursor": cursor, "limit": 1})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
