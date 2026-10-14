import { SortDirectionIndicator } from "./SortDirectionIndicator";
import type { SourcePlanSortBy, SourcePlanSortDirection } from "./api";
import "./StageSortControls.css";

const LABELS: Record<SourcePlanSortBy, string> = {
  source: "Design / folder",
  updated: "Last updated",
  analyzed: "AI analyzed time",
  group_size: "Embroidery group size",
  status: "Status",
  progress: "Scout progress",
  refs: "Drive-ready references",
  active_jobs: "Active jobs",
  outputs: "Completed outputs",
};
export function StageSortControls({
  stage, sortBy, direction, options, onChange,
}: {
  stage: string;
  sortBy: SourcePlanSortBy;
  direction: SourcePlanSortDirection;
  options: SourcePlanSortBy[];
  onChange: (sortBy: SourcePlanSortBy, direction: SourcePlanSortDirection) => void;
}) {
  const numeric = ["group_size", "progress", "refs", "active_jobs", "outputs"].includes(sortBy);
  const text = sortBy === "source" || sortBy === "status";
  const label = numeric ? direction === "asc" ? "Low → high" : "High → low"
    : text ? direction === "asc" ? "A → Z" : "Z → A"
    : direction === "asc" ? "Oldest first" : "Newest first";
  return <div className={"rrugc-stage-sort is-" + direction} role="group" aria-label={"Sort " + stage}>
    <label>
      <span>Sort by</span>
      <select aria-label={"Sort " + stage + " by"} value={sortBy}
        onChange={event => {
          const next = event.target.value as SourcePlanSortBy;
          onChange(next, next === "source" || next === "status" ? "asc" : "desc");
        }}>
        {options.map(key => <option key={key} value={key}>{LABELS[key]}</option>)}
      </select>
    </label>
    <button type="button" className="rrugc-icon-action"
      aria-label={"Reverse sort order for " + stage}
      title={"Sort direction: " + label}
      onClick={() => onChange(sortBy, direction === "asc" ? "desc" : "asc")}>
      <SortDirectionIndicator active direction={direction} />{label}
    </button>
  </div>;
}
