import pipelineOverviewIcon from "../../assets/navigation/pipeline-overview.svg";
import aiAnalysisIcon from "../../assets/navigation/ai-analysis.svg";
import processingIcon from "../../assets/navigation/processing.svg";
import visualSearchIcon from "../../assets/navigation/visual-search.svg";
import creativePipelineIcon from "../../assets/navigation/creative-pipeline.svg";
import inventoryDailyIcon from "../../assets/navigation/inventory-daily.svg";
import costUsageIcon from "../../assets/navigation/cost-usage.svg";
import providersIcon from "../../assets/navigation/providers.svg";
import configurationIcon from "../../assets/navigation/configuration.svg";

export type AiOpsTab =
  | "pipeline"
  | "overview"
  | "processing"
  | "visual-search"
  | "creative-pipeline"
  | "inventory"
  | "cost"
  | "providers"
  | "configuration";

export type AiOperationsNavigationItem = {
  id: AiOpsTab;
  label: string;
  iconSrc: string;
};

export const AI_OPERATIONS_TABS: readonly AiOperationsNavigationItem[] = [
  { id: "pipeline", label: "Pipeline overview", iconSrc: pipelineOverviewIcon },
  { id: "overview", label: "AI analysis", iconSrc: aiAnalysisIcon },
  { id: "processing", label: "Processing", iconSrc: processingIcon },
  { id: "visual-search", label: "Visual Search", iconSrc: visualSearchIcon },
  { id: "creative-pipeline", label: "Creative Pipeline", iconSrc: creativePipelineIcon },
  { id: "inventory", label: "Inventory Daily", iconSrc: inventoryDailyIcon },
  { id: "cost", label: "Cost & Usage", iconSrc: costUsageIcon },
  { id: "providers", label: "Providers", iconSrc: providersIcon },
  { id: "configuration", label: "Configuration", iconSrc: configurationIcon },
];

export function isAiOpsTab(value: string | null | undefined): value is AiOpsTab {
  return AI_OPERATIONS_TABS.some(item => item.id === value);
}

export function aiOperationsTabHref(tab: AiOpsTab): string {
  return `/ai-operations?tab=${encodeURIComponent(tab)}`;
}
