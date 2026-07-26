import type { AssetProcessingStatus } from "../types";

const statusContent: Record<
  AssetProcessingStatus,
  { label: string; description: string }
> = {
  discovered: {
    label: "Discovered",
    description: "The source item is known and waiting for ingestion.",
  },
  stored: {
    label: "Stored",
    description: "The managed asset content has been stored.",
  },
  analyzing: {
    label: "Analyzing",
    description: "AI metadata analysis is in progress.",
  },
  metadata_ready: {
    label: "Metadata ready",
    description: "AI metadata is available for this asset.",
  },
  search_pending: {
    label: "Search pending",
    description: "Metadata is ready and waiting to be indexed for search.",
  },
  indexing: {
    label: "Indexing",
    description: "The asset is being added to the search index.",
  },
  indexed: {
    label: "Indexed",
    description: "The asset is available in the current search index.",
  },
  duplicate: {
    label: "Duplicate",
    description: "This source item reuses existing tenant content.",
  },
  search_failed: {
    label: "Search failed",
    description: "Search indexing failed and can be retried.",
  },
  failed: {
    label: "Failed",
    description: "The latest processing stage failed and may need retry.",
  },
};

export function AssetStatusBadge({
  status,
}: {
  status: AssetProcessingStatus;
}) {
  const content = statusContent[status];

  return <span
    className={"processing-status " + status}
    data-status={status}
    title={content.description}
    aria-label={"Processing status: " + content.label + ". " + content.description}
  >
    <i aria-hidden="true" />
    {content.label}
  </span>;
}
