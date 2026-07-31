export type MembershipStatus = "invited" | "active" | "suspended" | "removed";

export type AccessRoleSummary = {
  id: string;
  key: string;
  name: string;
  system: boolean;
};

export type AccessMember = {
  membership_id: string;
  user_id: string;
  display_name: string | null;
  email: string | null;
  status: MembershipStatus;
  roles: AccessRoleSummary[];
  joined_at: string | null;
  last_login_at: string | null;
};

export type AccessRole = AccessRoleSummary & {
  description: string | null;
  protected: boolean;
  status: "active" | "disabled";
  permissions: string[];
  created_at: string;
  updated_at: string;
};

export type AccessPermission = { id: string; key: string; description: string | null };
export type AccessTenant = { id: string; name: string; slug: string };

export type AccessIdentity = {
  user_id: string;
  actor_id: string;
  active_tenant_id: string;
  available_tenants: AccessTenant[];
  roles: string[];
  permissions: string[];
  is_processing_admin: boolean;
  authorization_source: string;
};

export type Page<T> = { items: T[]; page: number; page_size: number; total: number };

export type AccessFilters = {
  query: string;
  status: string;
  role: string;
  page: number;
};

export type AccessApiErrorCode =
  | "authentication_required"
  | "tenant_membership_required"
  | "permission_required"
  | "tenant_mismatch"
  | "final_tenant_admin"
  | "membership_exists"
  | "invitation_conflict"
  | string;


export type ViewerFolderOption = { id: string; name: string };
export type ViewerFolderScope = { id: string; folder_id: string; folder_name: string | null; external_source_id: string };
