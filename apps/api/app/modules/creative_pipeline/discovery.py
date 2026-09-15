from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.assets.model import ExternalSourceModel
from app.modules.assets.source_credentials import source_credential_contract
from app.modules.creative_pipeline.constants import ListingTaskStatus, PipelineRunStatus, PipelineTriggerType
from app.modules.creative_pipeline.model import ListingTaskModel, PipelineRunModel, SourceGroupModel
from app.modules.creative_pipeline.parser import parse_listing_folder_name, parse_source_group_name
from app.modules.creative_pipeline.provider import ExplorerFolderListingGateway, FolderEntry, FolderListingGateway
from app.modules.creative_pipeline.repository import CreativePipelineRepository
from app.modules.explorer.tenant_source import TenantSourceResolver


@dataclass(frozen=True, slots=True)
class DiscoveryError:
    code: str
    external_source_id: str
    folder_id: str | None = None


@dataclass(slots=True)
class CreativePipelineDiscoveryResult:
    groups_seen: int = 0
    groups_created: int = 0
    groups_updated: int = 0
    groups_missing: int = 0
    listings_seen: int = 0
    listings_created: int = 0
    listings_updated: int = 0
    listings_missing: int = 0
    pipeline_runs_created: int = 0
    ignored_folders: int = 0
    errors: list[DiscoveryError] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _error_code(exc: Exception) -> str:
    name = exc.__class__.__name__.lower()
    if "permission" in name or "auth" in name:
        return "provider_authorization_failed"
    if "timeout" in name:
        return "provider_timeout"
    return "provider_listing_failed"


class CreativePipelineDiscoveryScanner:
    """Bounded, read-only provider discovery and tenant-scoped reconciliation."""

    def __init__(self, session: Session, *, page_size: int = 100):
        self.session = session
        self.page_size = page_size
        self.repository = CreativePipelineRepository(session)

    async def scan(
        self,
        *,
        tenant_id: str,
        external_source_id: str,
        root_folder_id: str,
        gateway: FolderListingGateway,
        source_provider: str | None = None,
    ) -> CreativePipelineDiscoveryResult:
        source = self.session.scalar(select(ExternalSourceModel).where(
            ExternalSourceModel.tenant_id == tenant_id,
            ExternalSourceModel.id == external_source_id,
        ))
        if source is None:
            raise ValueError("source_not_found")
        if source.status != "active":
            raise ValueError("source_unavailable")
        provider_name = source_provider or source.source_type

        result = CreativePipelineDiscoveryResult()
        # Complete root enumeration is required before deactivating absent groups.
        try:
            root_entries = await gateway.list_all_child_folders(root_folder_id, page_size=self.page_size)
        except Exception as exc:
            result.errors.append(DiscoveryError(_error_code(exc), external_source_id, root_folder_id))
            return result

        groups: list[tuple[FolderEntry, object]] = []
        seen_group_ids: set[str] = set()
        for entry in root_entries:
            parsed = parse_source_group_name(entry.name)
            if parsed is None or entry.id in seen_group_ids:
                result.ignored_folders += 1
                continue
            seen_group_ids.add(entry.id)
            groups.append((entry, parsed))
        result.groups_seen = len(groups)

        listing_entries: dict[str, tuple[FolderEntry, list[FolderEntry] | None]] = {}
        for entry, _parsed in groups:
            try:
                listing_entries[entry.id] = (
                    entry,
                    await gateway.list_all_child_folders(entry.id, page_size=self.page_size),
                )
            except Exception as exc:
                listing_entries[entry.id] = (entry, None)
                result.errors.append(DiscoveryError(_error_code(exc), external_source_id, entry.id))

        try:
            seen_groups = set()
            for entry, parsed in groups:
                now = _now()
                group = self.session.scalar(select(SourceGroupModel).where(
                    SourceGroupModel.tenant_id == tenant_id,
                    SourceGroupModel.external_source_id == external_source_id,
                    SourceGroupModel.external_folder_id == entry.id,
                ))
                if group is None:
                    group = SourceGroupModel(
                        tenant_id=tenant_id,
                        platform=parsed.platform.value,
                        name=parsed.display_name,
                        source_provider=provider_name,
                        external_source_id=external_source_id,
                        external_folder_id=entry.id,
                        source_path=entry.path,
                        active=True,
                        scan_enabled=True,
                        last_scan_at=now,
                    )
                    self.session.add(group)
                    result.groups_created += 1
                else:
                    group.platform = parsed.platform.value
                    group.name = parsed.display_name
                    group.source_provider = provider_name
                    group.source_path = entry.path
                    group.active = True
                    group.last_scan_at = now
                    result.groups_updated += 1
                seen_groups.add(entry.id)
                listing_page = listing_entries[entry.id][1]
                if listing_page is None:
                    continue
                group.last_successful_scan_at = now
                await self._reconcile_listings(
                    group=group,
                    tenant_id=tenant_id,
                    entries=listing_page,
                    result=result,
                )

            known_groups = list(self.session.scalars(select(SourceGroupModel).where(
                SourceGroupModel.tenant_id == tenant_id,
                SourceGroupModel.external_source_id == external_source_id,
                SourceGroupModel.active.is_(True),
            )))
            for group in known_groups:
                if group.external_folder_id not in seen_groups:
                    group.active = False
                    result.groups_missing += 1
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return result

    async def _reconcile_listings(
        self,
        *,
        group: SourceGroupModel,
        tenant_id: str,
        entries: Iterable[FolderEntry],
        result: CreativePipelineDiscoveryResult,
    ) -> None:
        now = _now()
        observed_ids: set[str] = set()
        for entry in entries:
            parsed = parse_listing_folder_name(entry.name)
            if parsed is None:
                result.ignored_folders += 1
                continue
            if entry.id in observed_ids:
                continue
            observed_ids.add(entry.id)
            result.listings_seen += 1
            listing = self.session.scalar(select(ListingTaskModel).where(
                ListingTaskModel.tenant_id == tenant_id,
                ListingTaskModel.source_group_id == group.id,
                ListingTaskModel.external_folder_id == entry.id,
            ))
            if listing is None:
                listing = ListingTaskModel(
                    tenant_id=tenant_id,
                    source_group_id=group.id,
                    platform=group.platform,
                    listing_key=parsed.listing_key,
                    folder_name=entry.name.strip(),
                    folder_path=entry.path,
                    external_folder_id=entry.id,
                    status=ListingTaskStatus.ACTIVE.value,
                    first_discovered_at=now,
                    last_seen_at=now,
                )
                self.session.add(listing)
                self.session.flush()
                self.session.add(PipelineRunModel(
                    tenant_id=tenant_id,
                    listing_task_id=listing.id,
                    run_number=1,
                    status=PipelineRunStatus.QUEUED.value,
                    trigger_type=PipelineTriggerType.DISCOVERY.value,
                ))
                result.listings_created += 1
                result.pipeline_runs_created += 1
            else:
                listing.platform = group.platform
                listing.listing_key = parsed.listing_key
                listing.folder_name = entry.name.strip()
                listing.folder_path = entry.path
                listing.last_seen_at = now
                listing.status = ListingTaskStatus.ACTIVE.value
                result.listings_updated += 1
        known = list(self.session.scalars(select(ListingTaskModel).where(
            ListingTaskModel.tenant_id == tenant_id,
            ListingTaskModel.source_group_id == group.id,
            ListingTaskModel.status == ListingTaskStatus.ACTIVE.value,
        )))
        for listing in known:
            if listing.external_folder_id not in observed_ids:
                listing.status = ListingTaskStatus.MISSING_SOURCE.value
                result.listings_missing += 1

    async def scan_authorized(
        self,
        *,
        tenant_id: str,
        external_source_id: str,
        root_folder_id: str | None = None,
        resolver: TenantSourceResolver | None = None,
        provider_factory=None,
    ) -> CreativePipelineDiscoveryResult:
        source = self.session.scalar(select(ExternalSourceModel).where(
            ExternalSourceModel.tenant_id == tenant_id,
            ExternalSourceModel.id == external_source_id,
        ))
        if source is None:
            raise ValueError("source_not_found")
        metadata = source.source_metadata if isinstance(source.source_metadata, dict) else {}
        root_id = root_folder_id or metadata.get("root_folder_id") or metadata.get("folder_id") or "root"
        access_resolver = resolver or TenantSourceResolver(self.session)
        access = await access_resolver.resolve(tenant_id=tenant_id, external_source_id=external_source_id)
        contract = source_credential_contract(access.source_type)
        gateway = ExplorerFolderListingGateway(
            contract.adapter_key,
            access.access_token,
            provider_factory=provider_factory or __import__("app.providers.source_factory", fromlist=["create_source_provider"]).create_source_provider,
        )
        async with gateway:
            return await self.scan(
                tenant_id=tenant_id,
                external_source_id=external_source_id,
                root_folder_id=str(root_id),
                gateway=gateway,
                source_provider=source.source_type,
            )
