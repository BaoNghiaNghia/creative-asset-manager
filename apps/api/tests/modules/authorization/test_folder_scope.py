import asyncio
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.authorization.folder_scope import (
    ViewerFolderAccess,
    ViewerFolderHierarchyCache,
    ViewerFolderScopeModel,
    ViewerFolderScopeService,
    viewer_folder_hierarchy_cache,
)
from app.modules.authorization.folder_scope_cache import (
    ViewerFolderRemoteParentCache,
    viewer_folder_remote_parent_cache,
)
from app.modules.explorer.router import (
    _require_viewer_folder_scope,
    _require_viewer_folder_scope_from_provider,
    _viewer_media_scope_allowed,
    media,
    thumbnail,
)


class ViewerFolderScopeTest(unittest.TestCase):
    def setUp(self):
        viewer_folder_hierarchy_cache.clear()
        asyncio.run(viewer_folder_remote_parent_cache.clear())
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(self.engine, class_=Session, expire_on_commit=False)()
        self.service = ViewerFolderScopeService(self.session)

    def tearDown(self):
        viewer_folder_hierarchy_cache.clear()
        asyncio.run(viewer_folder_remote_parent_cache.clear())
        self.session.close()
        self.engine.dispose()

    def test_selected_folder_allows_descendant_but_not_sibling(self):
        access = ViewerFolderAccess(True, "source-1", frozenset({"folder-a"}))
        self.assertTrue(access.allows(item_id="folder-a"))
        self.assertTrue(access.allows(item_id="file-1", parent_id="folder-a"))
        self.assertTrue(access.allows(item_id="nested-file", ancestor_ids=["root", "folder-a", "nested"]))
        self.assertFalse(access.allows(item_id="file-2", parent_id="folder-b"))

    def test_viewer_access_is_tenant_and_membership_scoped(self):
        first = self.service.replace(
            tenant_id="tenant-1", membership_id="member-1", external_source_id="source-1",
            folders=[{"folder_id": "folder-a", "folder_name": "Allowed"}],
        )
        self.assertEqual([row.folder_external_id for row in first], ["folder-a"])
        self.assertEqual(
            self.service.access(
                tenant_id="tenant-1", membership_id="member-1",
                roles=frozenset({"viewer"}), external_source_id="source-1",
            ).folder_ids,
            frozenset({"folder-a"}),
        )
        self.assertEqual(
            self.service.access(
                tenant_id="tenant-2", membership_id="member-1",
                roles=frozenset({"viewer"}), external_source_id="source-1",
            ).folder_ids,
            frozenset(),
        )

    def test_multiple_selected_folders_persist_and_are_directly_accessible(self):
        rows = self.service.replace(
            tenant_id="tenant-1", membership_id="member-1", external_source_id="source-1",
            folders=[
                {"folder_id": "folder-a", "folder_name": "First allowed folder"},
                {"folder_id": "folder-b", "folder_name": "Second allowed folder"},
            ],
        )

        self.assertEqual(
            {(row.folder_external_id, row.folder_name) for row in rows},
            {
                ("folder-a", "First allowed folder"),
                ("folder-b", "Second allowed folder"),
            },
        )
        access = self.service.access(
            tenant_id="tenant-1", membership_id="member-1",
            roles=frozenset({"viewer"}), external_source_id="source-1",
        )
        self.assertEqual(access.folder_ids, frozenset({"folder-a", "folder-b"}))
        # Neither folder has a synchronized SourceAssetModel. Direct access
        # must not require a parent-map entry.
        self.assertTrue(self.service.allows_external_asset(
            tenant_id="tenant-1", access=access, external_asset_id="folder-a",
        ))
        self.assertTrue(self.service.allows_external_asset(
            tenant_id="tenant-1", access=access, external_asset_id="folder-b",
        ))

    def test_operator_is_not_restricted(self):
        access = self.service.access(
            tenant_id="tenant-1", membership_id="member-1",
            roles=frozenset({"viewer", "operator"}), external_source_id="source-1",
        )
        self.assertFalse(access.restricted)

    def test_scope_resolves_only_live_assets_under_selected_folder(self):
        source = ExternalSourceModel(id="source-1", tenant_id="tenant-1", source_key="g", source_type="google_drive", source_metadata={})
        asset = AssetModel(id="asset-1", tenant_id="tenant-1", content_hash="hash-1", mime_type="image/jpeg")
        source_asset = SourceAssetModel(
            id="source-asset-1", tenant_id="tenant-1", external_source_id="source-1",
            external_asset_id="drive-file-1", source_metadata={"parents": ["folder-a"]},
        )
        self.session.add_all([source, asset, source_asset])
        self.session.flush()
        self.session.add(AssetSourceLinkModel(tenant_id="tenant-1", asset_id="asset-1", source_asset_id="source-asset-1"))
        self.session.flush()
        self.service.replace(
            tenant_id="tenant-1", membership_id="member-1", external_source_id="source-1",
            folders=[{"folder_id": "folder-a", "folder_name": "Allowed"}],
        )
        access = ViewerFolderAccess(True, "source-1", frozenset({"folder-a"}))
        self.assertEqual(self.service.allowed_internal_asset_ids(tenant_id="tenant-1", access=access), {"asset-1"})
        source_asset.deleted_at = source_asset.updated_at
        viewer_folder_hierarchy_cache.invalidate(tenant_id="tenant-1", external_source_id="source-1")
        self.assertEqual(self.service.allowed_internal_asset_ids(tenant_id="tenant-1", access=access), set())

    def test_exact_accessible_source_pair_excludes_duplicate_unassigned_source(self):
        current_source = ExternalSourceModel(
            id="source-1", tenant_id="tenant-1", source_key="current",
            source_type="google_drive", source_metadata={},
        )
        old_source = ExternalSourceModel(
            id="old-source", tenant_id="tenant-1", source_key="old",
            source_type="google_drive", source_metadata={},
        )
        asset = AssetModel(
            id="asset-1", tenant_id="tenant-1",
            content_hash="shared-hash", mime_type="image/jpeg",
        )
        old_source_asset = SourceAssetModel(
            id="old-source-asset", tenant_id="tenant-1", external_source_id="old-source",
            external_asset_id="file-1", source_metadata={"parents": ["old-folder"]},
        )
        current_source_asset = SourceAssetModel(
            id="current-source-asset", tenant_id="tenant-1", external_source_id="source-1",
            external_asset_id="file-1", source_metadata={"parents": ["folder-a"]},
        )
        self.session.add_all([current_source, old_source, asset, old_source_asset, current_source_asset])
        self.session.flush()
        self.session.add_all([
            AssetSourceLinkModel(
                tenant_id="tenant-1", asset_id="asset-1", source_asset_id="old-source-asset",
            ),
            AssetSourceLinkModel(
                tenant_id="tenant-1", asset_id="asset-1", source_asset_id="current-source-asset",
            ),
        ])
        self.service.replace(
            tenant_id="tenant-1", membership_id="member-1", external_source_id="source-1",
            folders=[{"folder_id": "folder-a", "folder_name": "Allowed"}],
        )
        self.session.flush()

        allowed = self.service.allowed_asset_source_pairs_for_membership(
            tenant_id="tenant-1", membership_id="member-1",
        )

        self.assertEqual(allowed, {("asset-1", "current-source-asset")})

    def test_selected_root_folder_allows_nested_descendants(self):
        root = SourceAssetModel(
            id="folder-source", tenant_id="tenant-1", external_source_id="source-1",
            external_asset_id="folder-a", source_metadata={"parents": []},
        )
        child = SourceAssetModel(
            id="child-folder-source", tenant_id="tenant-1", external_source_id="source-1",
            external_asset_id="folder-b", source_metadata={"parent_id": "folder-a"},
        )
        asset = AssetModel(id="nested-asset", tenant_id="tenant-1", content_hash="nested", mime_type="image/jpeg")
        source_asset = SourceAssetModel(
            id="nested-source", tenant_id="tenant-1", external_source_id="source-1",
            external_asset_id="file-1", source_metadata={"parent_id": "folder-b"},
        )
        self.session.add_all([root, child, asset, source_asset])
        self.session.flush()
        self.session.add(AssetSourceLinkModel(tenant_id="tenant-1", asset_id="nested-asset", source_asset_id="nested-source"))
        self.session.flush()
        access = ViewerFolderAccess(True, "source-1", frozenset({"folder-a"}))
        self.assertEqual(self.service.allowed_internal_asset_ids(tenant_id="tenant-1", access=access), {"nested-asset"})

    def test_new_upload_uses_remote_parent_for_media_scope(self):
        access = ViewerFolderAccess(True, "source-1", frozenset({"folder-a"}))

        class ScopeService:
            def allows_external_asset(self, *, external_asset_id, **_kwargs):
                return False

        class Provider:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get_node(self, _item_id):
                return SimpleNamespace(parent_id="folder-a")

        with patch("app.modules.explorer.router.create_source_provider", return_value=Provider()):
            allowed = asyncio.run(_viewer_media_scope_allowed(
                ScopeService(), tenant_id="tenant-1", access=access,
                provider="google-drive", token="test-token", item_id="new-file",
            ))
        self.assertTrue(allowed)

    def test_remote_media_scope_walks_nested_drive_ancestry(self):
        access = ViewerFolderAccess(True, "source-1", frozenset({"folder-a"}))

        class ScopeService:
            def allows_external_asset(self, **_kwargs):
                return False

        class Provider:
            parents = {
                "new-file": "nested-folder",
                "nested-folder": "folder-a",
            }

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get_node(self, item_id):
                return SimpleNamespace(parent_id=self.parents.get(item_id))

        with patch("app.modules.explorer.router.create_source_provider", return_value=Provider()):
            allowed = asyncio.run(_viewer_media_scope_allowed(
                ScopeService(), tenant_id="tenant-1", access=access,
                provider="google-drive", token="test-token", item_id="new-file",
            ))

        self.assertTrue(allowed)

    def test_concurrent_remote_media_scope_requests_share_parent_lookups(self):
        access = ViewerFolderAccess(True, "source-1", frozenset({"folder-a"}))

        class ScopeService:
            def allows_external_asset(self, **_kwargs):
                return False

        class Provider:
            parents = {"file-1": "nested-folder", "nested-folder": "folder-a"}

            def __init__(self):
                self.get_node_calls = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get_node(self, item_id):
                self.get_node_calls += 1
                await asyncio.sleep(0)
                return SimpleNamespace(parent_id=self.parents.get(item_id))

        provider = Provider()

        async def request_scope():
            return await _viewer_media_scope_allowed(
                ScopeService(), tenant_id="tenant-1", access=access,
                provider="google-drive", token="test-token", item_id="file-1",
            )

        async def concurrent_requests():
            return await asyncio.gather(*(request_scope() for _ in range(8)))

        with patch("app.modules.explorer.router.create_source_provider", return_value=provider) as create_provider:
            results = asyncio.run(concurrent_requests())

        self.assertEqual(results, [True] * 8)
        self.assertEqual(provider.get_node_calls, 2)
        self.assertEqual(create_provider.call_count, 1)

    def test_remote_scope_allows_nested_folders_and_files_under_each_selected_root(self):
        access = ViewerFolderAccess(True, "source-1", frozenset({"folder-a", "folder-b"}))

        class ScopeService:
            def allows_external_asset(self, **_kwargs):
                return False

        class Provider:
            parents = {
                "folder-a1": "folder-a",
                "file-a": "folder-a1",
                "folder-b1": "folder-b",
                "file-b": "folder-b1",
                "outside-folder": "unselected-root",
            }

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get_node(self, item_id):
                return SimpleNamespace(parent_id=self.parents.get(item_id))

        async def require(item_id):
            await _require_viewer_folder_scope_from_provider(
                ScopeService(),
                tenant_id="tenant-1",
                access=access,
                provider="google-drive",
                token="test-token",
                folder_id=item_id,
            )

        with patch("app.modules.explorer.router.create_source_provider", return_value=Provider()):
            asyncio.run(require("folder-a1"))
            asyncio.run(require("file-a"))
            asyncio.run(require("folder-b1"))
            asyncio.run(require("file-b"))
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(require("outside-folder"))

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail["code"], "viewer_folder_scope_denied")

    def test_viewer_upload_scope_denies_drive_root_but_allows_assigned_folder(self):
        access = ViewerFolderAccess(True, "source-1", frozenset({"folder-a"}))

        class ScopeService:
            def allows_external_asset(self, *, external_asset_id, **_kwargs):
                return external_asset_id == "folder-a"

        _require_viewer_folder_scope(
            ScopeService(), tenant_id="tenant-1", access=access, folder_id="folder-a", allow_root=False,
        )
        with self.assertRaises(HTTPException) as raised:
            _require_viewer_folder_scope(
                ScopeService(), tenant_id="tenant-1", access=access, folder_id="root", allow_root=False,
            )
        self.assertEqual(raised.exception.status_code, 403)

    def test_thumbnail_proxy_checks_viewer_scope_before_google(self):
        request = SimpleNamespace(headers={})
        principal = SimpleNamespace(
            active_tenant_id="tenant-1",
            membership_id="member-1",
            effective_roles=frozenset({"viewer"}),
        )
        denied = HTTPException(
            status_code=403,
            detail={
                "code": "viewer_folder_scope_denied",
                "message": "File is outside the viewer folder scope.",
            },
        )

        with patch(
            "app.modules.explorer.router._authorized_file_context",
            new=AsyncMock(side_effect=denied),
        ), patch(
            "app.modules.explorer.router.open_google_thumbnail",
            new=AsyncMock(),
        ) as open_thumbnail:
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    thumbnail(
                        request=request,
                        item_id="file-outside-scope",
                        provider="google-drive",
                        session=self.session,
                        principal=principal,
                        external_source_id="source-1",
                    )
                )

        self.assertEqual(raised.exception.status_code, 403)
        open_thumbnail.assert_not_awaited()
    def test_media_proxy_checks_viewer_scope_before_google(self):
        request = SimpleNamespace(headers={})
        principal = SimpleNamespace(
            active_tenant_id="tenant-1",
            membership_id="member-1",
            effective_roles=frozenset({"viewer"}),
        )
        denied = HTTPException(status_code=403, detail={"code": "viewer_folder_scope_denied"})

        with patch(
            "app.modules.explorer.router._authorized_file_context",
            new=AsyncMock(side_effect=denied),
        ), patch(
            "app.modules.explorer.router.open_google_media",
            new=AsyncMock(),
        ) as open_media:
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    media(
                        request=request,
                        item_id="file-outside-scope",
                        provider="google-drive",
                        session=self.session,
                        principal=principal,
                        external_source_id="source-1",
                    )
                )

        self.assertEqual(raised.exception.status_code, 403)
        open_media.assert_not_awaited()



    def test_cached_parent_map_allows_descendant_and_denies_sibling(self):
        source = ExternalSourceModel(id="source-1", tenant_id="tenant-1", source_key="g", source_type="google_drive", source_metadata={})
        self.session.add_all([
            source,
            SourceAssetModel(id="allowed-folder", tenant_id="tenant-1", external_source_id="source-1", external_asset_id="folder-a", source_metadata={"parents": []}),
            SourceAssetModel(id="allowed-file", tenant_id="tenant-1", external_source_id="source-1", external_asset_id="file-allowed", source_metadata={"parents": ["folder-a"]}),
            SourceAssetModel(id="sibling-file", tenant_id="tenant-1", external_source_id="source-1", external_asset_id="file-sibling", source_metadata={"parents": ["folder-b"]}),
        ])
        self.session.commit()
        access = ViewerFolderAccess(True, "source-1", frozenset({"folder-a"}))
        self.assertTrue(self.service.allows_external_asset(tenant_id="tenant-1", access=access, external_asset_id="file-allowed"))
        self.assertFalse(self.service.allows_external_asset(tenant_id="tenant-1", access=access, external_asset_id="file-sibling"))


class ViewerFolderHierarchyCacheTest(unittest.TestCase):
    def test_reuses_entries_by_tenant_and_source_then_reloads_after_expiry_and_invalidation(self):
        now = [0.0]
        cache = ViewerFolderHierarchyCache(max_entries=2, ttl_seconds=60, clock=lambda: now[0])
        calls: list[str] = []

        def loader(label: str):
            def load():
                calls.append(label)
                return {"file": (f"{label}-parent",)}
            return load

        self.assertEqual(cache.get_or_load(tenant_id="tenant-1", external_source_id="source-1", loader=loader("first"))["file"], ("first-parent",))
        self.assertEqual(cache.get_or_load(tenant_id="tenant-1", external_source_id="source-1", loader=loader("reused"))["file"], ("first-parent",))
        cache.get_or_load(tenant_id="tenant-2", external_source_id="source-1", loader=loader("other-tenant"))
        cache.get_or_load(tenant_id="tenant-1", external_source_id="source-2", loader=loader("other-source"))
        self.assertEqual(calls, ["first", "other-tenant", "other-source"])

        now[0] = 61.0
        cache.get_or_load(tenant_id="tenant-1", external_source_id="source-1", loader=loader("expired"))
        cache.invalidate(tenant_id="tenant-1", external_source_id="source-1")
        cache.get_or_load(tenant_id="tenant-1", external_source_id="source-1", loader=loader("invalidated"))
        self.assertEqual(calls, ["first", "other-tenant", "other-source", "expired", "invalidated"])

    def test_concurrent_requests_coalesce_one_hierarchy_load(self):
        cache = ViewerFolderHierarchyCache(max_entries=4, ttl_seconds=60)
        loader_started = threading.Event()
        release_loader = threading.Event()
        calls = [0]
        calls_lock = threading.Lock()

        def loader():
            with calls_lock:
                calls[0] += 1
            loader_started.set()
            self.assertTrue(release_loader.wait(timeout=2))
            return {"file": ("folder-a",)}

        def load():
            return cache.get_or_load(tenant_id="tenant-1", external_source_id="source-1", loader=loader)

        with ThreadPoolExecutor(max_workers=8) as executor:
            first = executor.submit(load)
            self.assertTrue(loader_started.wait(timeout=2))
            waiting = [executor.submit(load) for _ in range(7)]
            release_loader.set()
            results = [first.result(timeout=2), *(future.result(timeout=2) for future in waiting)]

        self.assertEqual(calls[0], 1)
        self.assertTrue(all(result and result["file"] == ("folder-a",) for result in results))

    def test_loader_failure_fails_closed(self):
        cache = ViewerFolderHierarchyCache(max_entries=2, ttl_seconds=60)
        self.assertIsNone(cache.get_or_load(tenant_id="tenant-1", external_source_id="source-1", loader=lambda: (_ for _ in ()).throw(RuntimeError("database unavailable"))))
        self.assertIsNone(cache.get_or_load(tenant_id="tenant-1", external_source_id="source-2", loader=lambda: None))


class ViewerFolderRemoteParentCacheTest(unittest.TestCase):
    def test_reuses_parent_and_invalidates_by_tenant_and_source(self):
        now = [0.0]
        cache = ViewerFolderRemoteParentCache(max_entries=2, ttl_seconds=60, clock=lambda: now[0])
        calls = [0]

        async def load(parent_id):
            calls[0] += 1
            return parent_id

        async def exercise():
            first = await cache.get_or_load(
                tenant_id="tenant-1", external_source_id="source-1", item_id="file-1",
                loader=lambda: load("folder-a"),
            )
            reused = await cache.get_or_load(
                tenant_id="tenant-1", external_source_id="source-1", item_id="file-1",
                loader=lambda: load("wrong-parent"),
            )
            cache.invalidate(tenant_id="tenant-1", external_source_id="source-1")
            refreshed = await cache.get_or_load(
                tenant_id="tenant-1", external_source_id="source-1", item_id="file-1",
                loader=lambda: load("folder-b"),
            )
            await cache.clear()
            return first, reused, refreshed

        self.assertEqual(asyncio.run(exercise()), ("folder-a", "folder-a", "folder-b"))
        self.assertEqual(calls[0], 2)


if __name__ == "__main__":
    unittest.main()
