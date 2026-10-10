import "./OutputActivityLoader.css";

type OutputActivityLoaderProps = {
  status: "queued" | "running";
  compact?: boolean;
};

/** Reusable, layout-stable activity indicator for generation output slots. */
export function OutputActivityLoader({ status, compact = false }: OutputActivityLoaderProps) {
  const label = status === "queued" ? "Queued…" : "Generating…";
  return <span
    className={"rrugc-output-activity" + (compact ? " is-compact" : "")}
    role="status"
    aria-label={status === "queued" ? "Output waiting in queue" : "Generating output"}
  >
    <span className="rrugc-output-activity-orbit" aria-hidden="true">
      <span /><span /><span />
    </span>
    {!compact && <span className="rrugc-output-activity-label" aria-hidden="true">{label}</span>}
  </span>;
}
