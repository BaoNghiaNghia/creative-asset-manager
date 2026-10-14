import "./SortDirectionIndicator.css";

/** Accessible sort order is exposed by the parent th[aria-sort] / button label. */
export function SortDirectionIndicator({
  active,
  direction,
}: {
  active: boolean;
  direction: "asc" | "desc";
}) {
  return <span aria-hidden="true"
    className={"rrugc-sort-indicator" + (active ? " is-active is-" + direction : " is-idle")}
    data-sort-state={active ? direction : "none"}>
    <span className="rrugc-sort-indicator-arrow">{active ? direction === "asc" ? "↑" : "↓" : "↕"}</span>
  </span>;
}
