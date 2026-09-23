import type { ReactNode } from "react";
import tenantDefaultsIcon from "../../assets/configuration-card-icons/tenant-defaults.svg";
import imageJobPriorityIcon from "../../assets/configuration-card-icons/image-job-priority.svg";
import videoJobPriorityIcon from "../../assets/configuration-card-icons/video-job-priority.svg";
import budgetPolicyIcon from "../../assets/configuration-card-icons/budget-policy.svg";
import imagePromptTemplateIcon from "../../assets/configuration-card-icons/image-prompt-template.svg";
import videoPromptTemplateIcon from "../../assets/configuration-card-icons/video-prompt-template.svg";
import globalControlsIcon from "../../assets/configuration-card-icons/global-controls.svg";
import videoCdnActivationIcon from "../../assets/configuration-card-icons/video-cdn-activation.svg";

export type ConfigurationCardIconName =
  | "tenant-defaults"
  | "image-job-priority"
  | "video-job-priority"
  | "budget-policy"
  | "image-prompt-template"
  | "video-prompt-template"
  | "global-controls"
  | "video-cdn-activation";

const icons: Record<ConfigurationCardIconName, string> = {
  "tenant-defaults": tenantDefaultsIcon,
  "image-job-priority": imageJobPriorityIcon,
  "video-job-priority": videoJobPriorityIcon,
  "budget-policy": budgetPolicyIcon,
  "image-prompt-template": imagePromptTemplateIcon,
  "video-prompt-template": videoPromptTemplateIcon,
  "global-controls": globalControlsIcon,
  "video-cdn-activation": videoCdnActivationIcon,
};

export function ConfigurationCardHeader({
  icon,
  title,
  description,
  kicker,
  titleId,
  className = "",
  extra,
}: {
  icon: ConfigurationCardIconName;
  title: ReactNode;
  description: ReactNode;
  kicker?: ReactNode;
  titleId?: string;
  className?: string;
  extra?: ReactNode;
}) {
  return (
    <header
      className={
        "ops-config-card-header" + (className ? ` ${className}` : "")
      }
    >
      <div className="ops-config-card-heading">
        <span className="ops-config-card-icon" aria-hidden="true">
          <img src={icons[icon]} alt="" />
        </span>
        <div className="ops-config-card-heading-copy">
          <h3 id={titleId}>{title}</h3>
          <p>{description}</p>
        </div>
      </div>
      <div className="ops-config-card-header-aside">
        {extra}
        {kicker ? <span className="ops-card-kicker">{kicker}</span> : null}
      </div>
    </header>
  );
}
