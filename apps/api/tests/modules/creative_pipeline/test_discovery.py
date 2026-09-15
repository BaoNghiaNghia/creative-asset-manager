import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.modules.creative_pipeline.discovery import CreativePipelineDiscoveryScanner
from app.modules.creative_pipeline.model import ListingTaskModel, PipelineRunModel, SourceGroupModel
from app.modules.creative_pipeline.parser import parse_listing_folder_name, parse_source_group_name
from app.modules.creative_pipeline.provider import FolderEntry
from app.modules.creative_pipeline.constants import ListingTaskStatus
from tests.modules.creative_pipeline.test_domain import _session


class FakeGateway:
    def __init__(self, pages, failures=None):
        self.pages = pages
        self.failures = failures or set()
        self.calls = []

    async def list_all_child_folders(self, parent_id, *, page_size=100):
        self.calls.append(parent_id)
        if parent_id in self.failures:
            raise RuntimeError("provider failed")
        return list(self.pages.get(parent_id, []))


def entry(id, name, path=None, parent_id=None):
    return FolderEntry(id=id, name=name, path=path or name, parent_id=parent_id)


def run_scan(sessions, gateway, **kwargs):
    with sessions() as session:
        result = asyncio.run(CreativePipelineDiscoveryScanner(session).scan(
            tenant_id="tenant-a", external_source_id="source-a", root_folder_id="root",
            gateway=gateway, **kwargs,
        ))
        return result


@pytest.mark.parametrize("name,platform,display", [
    ("Etsy - EmbrolyShop", "etsy", "EmbrolyShop"),
    ("  Amazon - Store A  ", "amazon", "Store A"),
    ("eTsY - Shop A", "etsy", "Shop A"),
])
def test_source_group_parser(name, platform, display):
    parsed = parse_source_group_name(name)
    assert parsed and parsed.platform.value == platform and parsed.display_name == display
    assert parsed.original_name == name


@pytest.mark.parametrize("name", ["Personal", "listing - 123", "Etsy -", "Amazon -", "Backup - Shop A", ""])
def test_invalid_source_group_parser(name):
    assert parse_source_group_name(name) is None


@pytest.mark.parametrize("name,key", [("listing - 4527798886", "4527798886"), (" Listing - B0ABC123 ", "B0ABC123")])
def test_listing_parser_preserves_string_key(name, key):
    parsed = parse_listing_folder_name(name)
    assert parsed and parsed.listing_key == key


@pytest.mark.parametrize("name", ["listing -", "Source", "UGC - Macro Vid", "Pipeline", "Listing backup"])
def test_invalid_listing_parser(name):
    assert parse_listing_folder_name(name) is None


def test_empty_root_and_two_level_discovery():
    engine, sessions = _session()
    try:
        gateway = FakeGateway({"root": []})
        result = run_scan(sessions, gateway)
        assert result.groups_seen == result.listings_seen == result.pipeline_runs_created == 0
        assert gateway.calls == ["root"]
    finally:
        engine.dispose()


def test_group_listings_and_initial_runs_are_idempotent():
    engine, sessions = _session()
    try:
        gateway = FakeGateway({
            "root": [entry("g1", "Etsy - Shop")],
            "g1": [entry("l1", "listing - 4527798886"), entry("l2", "listing - B0ABC123")],
        })
        first = run_scan(sessions, gateway)
        second = run_scan(sessions, gateway)
        with sessions() as session:
            assert session.scalar(select(SourceGroupModel).where(SourceGroupModel.tenant_id == "tenant-a")).name == "Shop"
            assert len(session.scalars(select(ListingTaskModel)).all()) == 2
            assert len(session.scalars(select(PipelineRunModel)).all()) == 2
        assert (first.groups_created, first.listings_created, first.pipeline_runs_created) == (1, 2, 2)
        assert second.groups_created == second.listings_created == second.pipeline_runs_created == 0
    finally:
        engine.dispose()


def test_group_and_listing_rename_keep_identity_and_listing_key_updates():
    engine, sessions = _session()
    try:
        gateway = FakeGateway({"root": [entry("g1", "Etsy - Old")], "g1": [entry("l1", "listing - 123")]})
        run_scan(sessions, gateway)
        gateway.pages = {"root": [entry("g1", " Etsy - New ", "/new")], "g1": [entry("l1", "listing - 456", "/new/listing")]}
        run_scan(sessions, gateway)
        with sessions() as session:
            group = session.scalar(select(SourceGroupModel))
            listing = session.scalar(select(ListingTaskModel))
            assert (group.id, group.name, group.source_path) == (group.id, "New", "/new")
            assert (listing.id, listing.listing_key, listing.folder_path) == (listing.id, "456", "/new/listing")
            assert len(session.scalars(select(PipelineRunModel)).all()) == 1
    finally:
        engine.dispose()


def test_missing_listing_is_reconciled_and_rediscovery_recovers():
    engine, sessions = _session()
    try:
        gateway = FakeGateway({"root": [entry("g1", "Amazon - Store")], "g1": [entry("l1", "listing - B0ABC123"), entry("l2", "listing - B0DEF456")]})
        run_scan(sessions, gateway)
        gateway.pages["g1"] = [entry("l1", "listing - B0ABC123")]
        result = run_scan(sessions, gateway)
        with sessions() as session:
            missing = session.scalar(select(ListingTaskModel).where(ListingTaskModel.external_folder_id == "l2"))
            assert missing.status == ListingTaskStatus.MISSING_SOURCE.value
        assert result.listings_missing == 1
        gateway.pages["g1"].append(entry("l2", "listing - B0DEF456"))
        run_scan(sessions, gateway)
        with sessions() as session:
            assert session.scalar(select(ListingTaskModel).where(ListingTaskModel.external_folder_id == "l2")).status == ListingTaskStatus.ACTIVE.value
    finally:
        engine.dispose()


def test_provider_failures_do_not_mark_missing_or_deactivate():
    engine, sessions = _session()
    try:
        gateway = FakeGateway({"root": [entry("g1", "Etsy - Shop")], "g1": [entry("l1", "listing - 1")]})
        run_scan(sessions, gateway)
        gateway.failures = {"root"}
        failed_root = run_scan(sessions, gateway)
        assert failed_root.errors and failed_root.groups_missing == 0
        with sessions() as session:
            assert session.scalar(select(SourceGroupModel)).active is True
        gateway.failures = {"g1"}
        failed_group = run_scan(sessions, gateway)
        assert failed_group.errors
        with sessions() as session:
            assert session.scalar(select(ListingTaskModel)).status == ListingTaskStatus.ACTIVE.value
    finally:
        engine.dispose()


def test_pagination_gateway_page_two_is_consumed():
    class PagedGateway(FakeGateway):
        async def list_all_child_folders(self, parent_id, *, page_size=100):
            self.calls.append(parent_id)
            if parent_id == "root":
                first = [entry("noise", "Personal")]
                second = [entry("g1", "Etsy - Shop")]
                return first + second
            return [entry("l1", "listing - 999")]
    engine, sessions = _session()
    try:
        result = run_scan(sessions, PagedGateway({"root": []}))
        assert result.groups_seen == 1 and result.listings_seen == 1
    finally:
        engine.dispose()


def test_same_key_across_groups_is_allowed_and_cross_tenant_isolated():
    engine, sessions = _session()
    try:
        gateway = FakeGateway({"root": [entry("g1", "Etsy - A"), entry("g2", "Amazon - B")], "g1": [entry("l1", "listing - same")], "g2": [entry("l2", "listing - same")]})
        run_scan(sessions, gateway)
        with sessions() as session:
            assert len(session.scalars(select(ListingTaskModel)).all()) == 2
        other = FakeGateway({"root": [entry("g3", "Etsy - Other")], "g3": [entry("l3", "listing - x")]})
        with sessions() as session:
            result = asyncio.run(CreativePipelineDiscoveryScanner(session).scan(
                tenant_id="tenant-b", external_source_id="source-b", root_folder_id="root", gateway=other,
            ))
        assert result.listings_created == 1
        with sessions() as session:
            assert len(session.scalars(select(ListingTaskModel).where(ListingTaskModel.tenant_id == "tenant-a")).all()) == 2
            assert len(session.scalars(select(ListingTaskModel).where(ListingTaskModel.tenant_id == "tenant-b")).all()) == 1
    finally:
        engine.dispose()


def test_existing_completed_run_does_not_create_another():
    engine, sessions = _session()
    try:
        gateway = FakeGateway({"root": [entry("g1", "Etsy - Shop")], "g1": [entry("l1", "listing - 1")]})
        run_scan(sessions, gateway)
        with sessions.begin() as session:
            run = session.scalar(select(PipelineRunModel))
            run.status = "completed"
        assert run_scan(sessions, gateway).pipeline_runs_created == 0
    finally:
        engine.dispose()

def test_existing_provider_gateway_consumes_all_pages_without_raw_credentials():
    from app.modules.creative_pipeline.provider import ExplorerFolderListingGateway
    from app.modules.explorer.schema import AssetNode

    class Client:
        def __init__(self):
            self.tokens = []
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def list_children_page(self, parent_id, *, folders_only, page_token, page_size):
            self.tokens.append(page_token)
            if page_token is None:
                return [AssetNode(id="g1", name="Etsy - Shop", kind="folder", mime_type="folder")], "next"
            return [AssetNode(id="g2", name="Amazon - Store", kind="folder", mime_type="folder")], None

    client = Client()
    gateway = ExplorerFolderListingGateway("google-drive", "secret-token", provider_factory=lambda *_: client)
    async def collect():
        async with gateway:
            return await gateway.list_all_child_folders("root", page_size=1)
    folders = asyncio.run(collect())
    assert [folder.id for folder in folders] == ["g1", "g2"]
    assert client.tokens == [None, "next"]
    assert all(not hasattr(folder, "access_token") for folder in folders)
