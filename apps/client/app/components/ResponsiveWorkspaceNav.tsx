import { useEffect, useState } from "react";
import { fetchAccessIdentity } from "../../features/access_management";
import { BrandIcon } from "./Icons";
import { WorkspaceNavigation, mayViewReviewBoard, type WorkspaceRoute } from "./WorkspaceNavigation";

export function ResponsiveWorkspaceNav({ active }: { active: WorkspaceRoute }) {
  const [permissions, setPermissions] = useState<readonly string[]>([]);

  useEffect(() => {
    let alive = true;
    fetchAccessIdentity()
      .then(identity => { if (alive) setPermissions(identity.permissions); })
      .catch(() => { if (alive) setPermissions([]); });
    return () => { alive = false; };
  }, []);

  return <div className="responsive-workspace-nav">
    <a className="responsive-workspace-brand" href="/" aria-label="Creative Asset Manager">
      <span><BrandIcon /></span>
      <strong>Creative Asset Manager</strong>
    </a>
    <WorkspaceNavigation
      active={active}
      showOperations={permissions.includes("ai_operations.read")}
      showReviewBoard={mayViewReviewBoard(permissions)}
    />
  </div>;
}
