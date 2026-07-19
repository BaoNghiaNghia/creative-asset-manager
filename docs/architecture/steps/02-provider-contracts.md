# Step 02 — Provider Contracts

## Objective

Separate source, storage, and AI capabilities from external provider implementations while preserving the current explorer behavior and API responses.

## Deliverables

- Provider-neutral Python protocols and data contracts.
- TypeScript interfaces matching the public contract vocabulary.
- Google Drive and SharePoint source adapters around existing clients.
- Explorer compatibility contract and injected source-provider factory.
- Unconfigured storage and AI adapter skeletons.
- Contract, adapter, provider-boundary, and existing-flow tests.
- Updated roadmap and review.

## Constraints

- No database or migration changes.
- Unified ingestion remains disabled.
- Incremental change retrieval remains a Step 06 skeleton.
- Managed storage and AI analysis are not wired into runtime.
- Existing routes, response models, OAuth, browse, search, thumbnail, and media behavior remain unchanged.

## Acceptance criteria

- `ExplorerService` imports no concrete Google Drive or SharePoint client.
- Google Drive and SharePoint adapters satisfy `AssetSourceProvider` and are unit tested without network access.
- Source, storage, and AI contracts do not import external SDKs.
- Current FastAPI health and explorer response models continue to pass tests.
- No migration or worker file changes.
