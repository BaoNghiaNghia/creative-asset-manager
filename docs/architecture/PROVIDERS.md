# Provider Architecture

## Boundary

Creative Asset Manager defines three independent provider contracts:

- `AssetSourceProvider` discovers source changes, resolves one source asset, and opens a download stream.
- `AssetStorageProvider` stores managed content and optional metadata sidecar exports.
- `AiMetadataProvider` analyzes one asset and returns a dynamic metadata document.

The authoritative Python contracts live in `apps/api/app/domain/providers/contracts.py`. Transport-neutral TypeScript interfaces live in `packages/types/src/providers.ts` for future SDK/API consumers.

Domain and application services depend on contracts. Google Drive, Microsoft Graph, storage, and AI implementation details stay in `apps/api/app/providers`.

## Source identity

Every `ExternalAssetCandidate` carries:

- `source_type`: `google_drive`, `sharepoint`, or `external_api`.
- `source_id`: the future external source connection identity.
- `external_asset_id`: the provider's stable item identity.

Filename, MIME type, size, timestamps, provider checksum/version, and provider metadata are observations. They are not permanent content identity.

## Existing explorer compatibility

The current UI needs folder browsing before unified ingestion exists. `ExplorerSourceProvider` extends the source contract with `get_node` and `list_children` in `app/modules/explorer/provider_contract.py`.

`GoogleDriveSourceAdapter` and `SharePointSourceAdapter` implement that compatibility surface by delegating to the existing HTTP clients. `ExplorerService` receives a `SourceProviderFactory`; it no longer imports concrete Google or Microsoft clients.

The compatibility methods are not part of the long-term ingestion contract and must not be used by new domain ingestion services.

## Composition

`app/providers/source_factory.py` is the current composition root for explorer source adapters. Controllers and indexing orchestration pass this factory into `ExplorerService`. Provider selection is therefore outside the service.

Future steps may replace the factory with a container or FastAPI dependency without changing the domain contracts.

## Step 06 incremental behavior

- Google Drive uses Changes start/page tokens and can perform a full files reconciliation.
- SharePoint uses opaque Microsoft Graph drive Delta next/delta links.
- Provider pages expose only normalized `SourceChangePage` values to the sync service.
- Existing explorer compatibility and authenticated media streaming remain unchanged.
- Storage and AI skeletons remain unconfigured and all rollout flags remain disabled by default.

## Step 08 managed Google Drive storage

Google Drive as a source and Google Drive as managed storage are separate
provider roles. GoogleDriveAssetStorage receives a dedicated write-capable
credential and configured managed root; it never consumes the explorer source
session.

The adapter uploads canonical internal asset bytes through AssetStorageProvider.
Its stable remote identity is tenant plus internal asset ID, recorded in Drive
appProperties and persisted in asset_storage_objects. Content hash controls the
deterministic filename. A pre-upload lookup recovers uploads whose remote write
succeeded before the local transaction completed.

The adapter records remote file ID, folder ID, web URL, size and provider name.
Metadata sidecar behavior remains deliberately unavailable until Step 19.

## Step 19 Google Drive metadata sidecars

GoogleDriveAssetStorage serializes a PostgreSQL-derived, canonical JSON export.
Tenant, asset, and analysis appProperties form the deterministic remote lookup.
If the file exists, Drive media update replaces its bytes; otherwise the adapter
creates one resumable JSON upload. Sidecars are explicitly non-authoritative and
contain neither provider credentials nor raw authentication data.


## Step 28 AI batch provider capability

`AiMetadataProvider` now advertises batch capability and exposes provider-neutral
submit, status, streamed-result and cancel operations. The Gemini adapter owns
all Batch API REST shapes, bounded inline request conversion, stable display-name
recovery and provider state normalization. Unconfigured or unsupported adapters
fail closed. Batch consumers use stable custom item IDs and never depend on
provider result order.
