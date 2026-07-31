// @ts-expect-error Vitest executes this test-only import in Node.
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  AccessApiError, addMember, assignMemberRole, createCustomRole, fetchAccessIdentity,
  fetchMembers, removeMemberRole, switchActiveTenant, updateMemberStatus,
  type AccessFilters, type AccessIdentity, type AccessMember, type AccessPermission, type AccessRole,
} from "../../features/access_management";
import { routeForPath } from "../AppRoute";
import {
  AccessManagementContent, AccessManagementShell, accessStateForError,
  groupPermissions, handleAccessTabKeyDown, safeTenantRoles,
} from "./AccessManagementPage";

const noop = () => undefined;
const filters: AccessFilters = { query: "", status: "", role: "", page: 1 };
const identity: AccessIdentity = {
  user_id: "user-1", actor_id: "google:subject", active_tenant_id: "tenant-1",
  available_tenants: [{ id: "tenant-1", name: "Creative Team", slug: "creative" }, { id: "tenant-2", name: "Studio", slug: "studio" }],
  roles: ["tenant_admin"], permissions: ["tenant_members.read", "tenant_members.manage", "tenant_roles.manage", "assets.read"],
  is_processing_admin: true, authorization_source: "tenant_rbac",
};
const role: AccessRole = {
  id: "role-1", key: "tenant_admin", name: "Tenant admin", system: true, protected: true,
  description: "Tenant administration", status: "active", permissions: ["tenant_members.read", "tenant_members.manage"],
  created_at: "2026-07-20T00:00:00Z", updated_at: "2026-07-20T00:00:00Z",
};
const customRole: AccessRole = { ...role, id: "role-2", key: "creative_reviewer", name: "Creative reviewer", system: false, protected: false };
const member: AccessMember = {
  membership_id: "membership-1", user_id: "user-2", display_name: "Ari Artist", email: "ari@example.com",
  status: "active", roles: [{ id: role.id, key: role.key, name: role.name, system: true }],
  joined_at: "2026-07-21T00:00:00Z", last_login_at: "2026-07-22T00:00:00Z",
};
const permissions: AccessPermission[] = [
  { id: "p1", key: "tenant_members.read", description: "Read members" },
  { id: "p2", key: "assets.read", description: "Read assets" },
];

function markup(tab: "members" | "roles" | "my-access" = "members", overrides: Partial<Parameters<typeof AccessManagementContent>[0]> = {}) {
  return renderToStaticMarkup(<AccessManagementContent
    state="ready" identity={identity} members={{ items: [member], page: 1, page_size: 25, total: 1 }}
    roles={[role, customRole]} permissions={permissions} filters={filters} tab={tab}
    onTab={noop} onFilters={noop} onTenant={noop} onRetry={noop} onMutation={noop} onMessage={noop}
    {...overrides}
  />);
}

describe("Access Management route and presentation", () => {
  it("adds the normal settings route and workspace navigation", () => {
    expect(routeForPath("/settings/access")).toBe("access-management");
    expect(routeForPath("/settings/access/members")).toBe("access-management");
    const shell = renderToStaticMarkup(<AccessManagementShell><p>content</p></AccessManagementShell>);
    expect(shell).toContain('href="/settings/access"'); expect(shell).not.toContain('target="_blank"');
    expect(shell).not.toContain("AI Operations");
    const aiShell = renderToStaticMarkup(<AccessManagementShell identity={{ ...identity, permissions: [...identity.permissions, "ai_operations.read"] }}><p>content</p></AccessManagementShell>);
    expect(aiShell).toContain("AI Operations");
  });

  it("renders the members list, filters, role assignment and dangerous actions", () => {
    const html = markup();
    for (const value of ["Tenant members", "Ari Artist", "ari@example.com", "active", "Tenant admin", "Invite member", "Assign role", "Suspend", "Remove", "Page 1 of 1"]) expect(html).toContain(value);
    expect(html).toContain("Member filters"); expect(html).toContain('aria-haspopup="dialog"');
  });

  it("offers folder scoping only for viewer memberships", () => {
    const viewer = { ...member, roles: [{ id: "viewer-role", key: "viewer", name: "Viewer", system: true }] };
    expect(markup("members", { members: { items: [viewer], page: 1, page_size: 25, total: 1 } })).toContain("Limit viewer folders");
    expect(markup("members", { members: { items: [member], page: 1, page_size: 25, total: 1 } })).not.toContain("Limit viewer folders");
  });

  it("uses permission-based visibility while leaving backend enforcement authoritative", () => {
    const readOnly = { ...identity, roles: ["viewer"], permissions: ["tenant_members.read"] };
    const html = markup("members", { identity: readOnly });
    expect(html).toContain("Ari Artist"); expect(html).not.toContain("Invite member"); expect(html).not.toContain("Assign role"); expect(html).not.toContain("Suspend");
  });

  it("renders protected and custom roles with permissions grouped by domain", () => {
    const html = markup("roles");
    expect(html).toContain("Protected system role"); expect(html).toContain("Creative reviewer"); expect(html).toContain("Create custom role");
    expect(html).toContain("tenant_members.read"); expect(groupPermissions(permissions).map(item => item.domain)).toEqual(["assets", "tenant_members"]);
    expect(safeTenantRoles([...safeTenantRoles([role]), { ...role, key: "platform_admin" }])).toEqual([role]);
  });

  it("renders current identity, authorization source, permissions and tenant switcher", () => {
    const html = markup("my-access");
    for (const value of ["Current user", "user-1", "Creative Team", "Studio", "tenant_rbac", "tenant_admin", "assets.read", "Available tenants"]) expect(html).toContain(value);
    for (const secret of ["access_token", "refresh_token", "api_key", "session_id", "provider_credentials"]) expect(html.toLowerCase()).not.toContain(secret);
  });

  it("renders loading, empty, unauthenticated, permission, stale and network states", () => {
    const common = { identity: null, members: { items: [], page: 1, page_size: 25, total: 0 }, roles: [], permissions: [], filters, tab: "members" as const, onTab: noop, onFilters: noop, onTenant: noop, onRetry: noop, onMutation: noop, onMessage: noop };
    expect(renderToStaticMarkup(<AccessManagementContent {...common} state="loading" />)).toContain('aria-busy="true"');
    expect(renderToStaticMarkup(<AccessManagementContent {...common} state="unauthenticated" />)).toContain("Sign in required");
    expect(renderToStaticMarkup(<AccessManagementContent {...common} state="permission-denied" />)).toContain("tenant_members.read");
    expect(renderToStaticMarkup(<AccessManagementContent {...common} state="stale-membership" />)).toContain("Membership changed");
    expect(renderToStaticMarkup(<AccessManagementContent {...common} state="error" message="Network offline" />)).toContain("Network offline");
    expect(markup("members", { members: { items: [], page: 1, page_size: 25, total: 0 } })).toContain("No members found");
  });

  it("supports keyboard tabs and responsive layouts", () => {
    const selected: string[] = []; const focus = vi.fn();
    handleAccessTabKeyDown({ key: "ArrowRight", preventDefault: vi.fn(), currentTarget: { querySelectorAll: () => [{ focus }, { focus }, { focus }] } } as never, "members", tab => selected.push(tab));
    expect(selected).toEqual(["roles"]); expect(focus).toHaveBeenCalled();
    const css = readFileSync(new URL("../../styles/access-management.css", import.meta.url), "utf8");
    expect(css).toContain("@media(max-width:900px)"); expect(css).toContain("@media(max-width:650px)"); expect(css).toContain("overflow:auto");
  });

  it("maps stable authorization errors without exposing internals", () => {
    expect(accessStateForError(new AccessApiError("sign in", 401, "authentication_required"))).toBe("unauthenticated");
    expect(accessStateForError(new AccessApiError("stale", 403, "tenant_membership_required"))).toBe("stale-membership");
    expect(accessStateForError(new AccessApiError("denied", 403, "permission_required"))).toBe("permission-denied");
  });
  it("preserves renamed folders and flags deleted folder scopes", async () => {
    const { mergeViewerFolderOptions } = await import("./AccessManagementPage");
    expect(mergeViewerFolderOptions(
      [{ id: "folder-1", name: "New name" }],
      [{ folder_id: "folder-1", folder_name: "Old name" }, { folder_id: "gone", folder_name: "Old folder" }],
    )).toEqual([
      { id: "folder-1", name: "New name", renamedFrom: "Old name" },
      { id: "gone", name: "Old folder", stale: true },
    ]);
  });

});

describe("Access Management API client", () => {
  const response = (body: object, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  it("loads identity and tenant-scoped members with bounded filters", async () => {
    const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => String(url).includes("identity") ? response(identity) : response({ items: [member], page: 2, page_size: 25, total: 30 }));
    expect((await fetchAccessIdentity(fetcher)).active_tenant_id).toBe("tenant-1");
    await fetchMembers("tenant/1", { query: "Ari", status: "active", role: "operator", page: 2 }, fetcher);
    expect(String(fetcher.mock.calls[1][0])).toContain("/api/v1/tenants/tenant%2F1/members?");
    expect(String(fetcher.mock.calls[1][0])).toContain("query=Ari"); expect(fetcher.mock.calls[1][1]?.credentials).toBe("same-origin");
  });

  it("switches tenants and never submits session identifiers", async () => {
    const fetcher = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => response({ ...identity, active_tenant_id: "tenant-2" }));
    await switchActiveTenant("tenant-2", fetcher);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/auth/active-tenant");
    expect(fetcher.mock.calls[0][1]?.body).toBe(JSON.stringify({ tenant_id: "tenant-2" }));
  });

  it("supports invitation, status transitions and role assignment/removal", async () => {
    const fetcher = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => response({ ok: true }));
    await addMember("tenant-1", { email: "new@example.com", status: "invited", reason: "Joining project" }, fetcher);
    await updateMemberStatus("tenant-1", "membership-1", "suspend", "Left project", fetcher);
    await updateMemberStatus("tenant-1", "membership-1", "restore", "Returned", fetcher);
    await updateMemberStatus("tenant-1", "membership-1", "remove", "Offboarded", fetcher);
    await assignMemberRole("tenant-1", "membership-1", "role-1", "Assigned by admin", fetcher);
    await removeMemberRole("tenant-1", "membership-1", "role-1", "Changed duties", fetcher);
    expect(fetcher.mock.calls.map(call => call[1]?.method)).toEqual(["POST", "PATCH", "PATCH", "PATCH", "POST", "DELETE"]);
    expect(fetcher.mock.calls.map(call => String(call[1]?.body)).join(" ")).not.toContain("token");
  });

  it("creates custom roles from safe permission keys and preserves final-admin errors", async () => {
    const ok = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => response({ id: "role-new" }, 201));
    await createCustomRole("tenant-1", { role_key: "reviewer", name: "Reviewer", permission_keys: ["assets.read"], reason: "Review workflow" }, ok);
    expect(String(ok.mock.calls[0][1]?.body)).not.toContain("platform_admin");
    const denied = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => response({ detail: { code: "final_tenant_admin", message: "Final tenant admin is protected" } }, 409));
    await expect(updateMemberStatus("tenant-1", "membership-1", "remove", "Offboard", denied)).rejects.toMatchObject({ code: "final_tenant_admin", status: 409 });
  });
});
