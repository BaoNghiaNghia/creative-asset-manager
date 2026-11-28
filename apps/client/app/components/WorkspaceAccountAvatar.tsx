import { createContext, useContext, useState } from "react";
import type { AccessIdentity } from "../../features/access_management";
import "./WorkspaceAccountAvatar.css";

export type WorkspaceAccountProfile = Pick<AccessIdentity, "display_name" | "email" | "avatar_url">;

// Provided by the already-authorized route, not a second identity request.
export const WorkspaceAccountContext = createContext<WorkspaceAccountProfile | null>(null);
export const useWorkspaceAccount = () => useContext(WorkspaceAccountContext);

export function WorkspaceAccountAvatar({ profile }: { profile: WorkspaceAccountProfile }) {
  const picture = profile.avatar_url?.trim() || "";
  const [failedPicture, setFailedPicture] = useState<string | null>(null);
  const name = profile.display_name?.trim() || profile.email?.trim() || "";
  const initials = Array.from(name).slice(0, 2).join("").toUpperCase() || "G";
  const title = [profile.display_name?.trim(), profile.email?.trim()].filter(Boolean).join(" · ") || "Signed-in account";
  return <span className="workspace-account-avatar" role="img" aria-label={title} title={title}>
    {picture && failedPicture !== picture
      ? <img src={picture} alt="" referrerPolicy="no-referrer" width={34} height={34}
          onError={() => setFailedPicture(picture)} />
      : <span aria-hidden="true" className="workspace-account-avatar-initials">{initials}</span>}
  </span>;
}
