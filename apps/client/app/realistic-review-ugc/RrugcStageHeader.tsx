import type { ReactNode } from "react";

export function RrugcStageHeader({
  kicker,
  title,
  description,
  actions,
  className = "",
}: {
  kicker: ReactNode;
  title: ReactNode;
  description: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return <header className={"rrugc-stage-header" + (className ? " " + className : "")}>
    <div className="rrugc-stage-header-copy">
      <small>{kicker}</small>
      <h2>{title}</h2>
      <p>{description}</p>
    </div>
    {actions ? <div className="rrugc-stage-header-actions">{actions}</div> : null}
  </header>;
}
