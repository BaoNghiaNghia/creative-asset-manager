// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccessManagementContent } from "./AccessManagementPage";
import type { AccessIdentity, AccessMember, AccessNavigationModule } from "../../features/access_management";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => { vi.unstubAllGlobals(); document.body.replaceChildren(); });

const identity: AccessIdentity = {
  user_id: "admin", actor_id: "admin", active_tenant_id: "tenant-one",
  available_tenants: [{ id: "tenant-one", name: "Creative Assets", slug: "creative" }],
  roles: ["tenant_admin"], permissions: ["tenant_members.read", "tenant_members.manage", "tenant_roles.manage"],
  is_processing_admin: true, authorization_source: "tenant_rbac",
};
const member: AccessMember = {
  membership_id: "membership-one", user_id: "member-one", display_name: "Test Member",
  email: "test@example.com", avatar_url: null, roles: [{ id: "viewer", key: "viewer", name: "Viewer", system: true }],
  status: "active", joined_at: null, last_login_at: null, navigation_modules: null,
};
const modules: AccessNavigationModule[] = [
  { key: "inventory", label: "Inventory", permissions: ["inventory.read", "inventory.control"], can_grant: true },
  { key: "branding-dna", label: "Branding DNA", permissions: ["ai_provider.configure"], can_grant: true },
  { key: "access", label: "Access Management", permissions: ["tenant_members.manage"], can_grant: false },
];

describe("Compact member navbar access editor", () => {
  it("saves selected modules through a tenant-scoped audited endpoint", async () => {
    const fetcher = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ membership_id: member.membership_id, navigation_modules: ["inventory"] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetcher);
    const done = vi.fn();
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<AccessManagementContent
      state="ready" identity={identity} members={{ items: [member], page: 1, page_size: 25, total: 1 }}
      roles={[]} permissions={[]} navigationCatalog={modules}
      filters={{ query: "", status: "", role: "", page: 1 }} tab="members"
      onTab={() => {}} onFilters={() => {}} onTenant={() => {}}
      onRetry={() => {}} onMutation={done} onMessage={() => {}}
    />));
    expect(host.textContent).toContain("Role-based access");
    expect(host.querySelectorAll("tbody tr")).toHaveLength(1);
    await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Manage access for Test Member"]')!.click());
    expect(host.querySelector('[role="dialog"]')).not.toBeNull();
    await act(async () => host.querySelector<HTMLButtonElement>('[aria-pressed="false"]')!.click());
    const inventory = host.querySelector<HTMLInputElement>('.access-nav-module input[type="checkbox"]')!;
    await act(async () => inventory.click());
    expect(inventory.checked).toBe(true);
    expect(host.querySelectorAll<HTMLInputElement>('.access-nav-module input:disabled')).toHaveLength(1);
    const reason = host.querySelector<HTMLInputElement>('.access-nav-save input')!;
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      set.call(reason, "Assigned inventory responsibilities");
      reason.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { host.querySelector<HTMLButtonElement>(".access-nav-save button")!.click(); await Promise.resolve(); });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/tenants/tenant-one/members/membership-one/navigation-access");
    const sent = (fetcher.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(sent.method).toBe("PUT");
    expect(JSON.parse(String(sent.body))).toEqual({ modules: ["inventory"], reason: "Assigned inventory responsibilities" });
    expect(done).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
  });

  it("disallows per-navbar editing for tenant administrators", async () => {
    const host = document.createElement("div"); document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<AccessManagementContent
      state="ready" identity={identity}
      members={{ items: [{ ...member, roles: [{ id: "admin", key: "tenant_admin", name: "Tenant administrator", system: true }] }], page: 1, page_size: 25, total: 1 }}
      roles={[]} permissions={[]} navigationCatalog={modules}
      filters={{ query: "", status: "", role: "", page: 1 }} tab="members"
      onTab={() => {}} onFilters={() => {}} onTenant={() => {}}
      onRetry={() => {}} onMutation={() => {}} onMessage={() => {}}
    />));
    expect(host.textContent).toContain("All modules · Admin");
    await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Manage access for Test Member"]')!.click());
    expect(host.textContent).toContain("Tenant administrators keep unrestricted navigation access");
    expect(host.querySelector(".access-nav-save")).toBeNull();
    await act(async () => root.unmount());
  });
});
