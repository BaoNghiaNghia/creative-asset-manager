import type {
  AccessApiErrorCode, AccessFilters, AccessIdentity, AccessMember, AccessPermission,
  AccessRole, MembershipStatus, Page,
} from "./types";

type Fetcher = typeof fetch;

export class AccessApiError extends Error {
  constructor(message: string, readonly status: number, readonly code: AccessApiErrorCode) {
    super(message);
  }
}

async function request<T>(url: string, init: RequestInit = {}, fetcher: Fetcher = fetch): Promise<T> {
  const response = await fetcher(url, {
    ...init,
    credentials: "same-origin",
    headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: string | { code?: string; message?: string } };
    const detail = payload.detail;
    const code = typeof detail === "object" && detail?.code ? detail.code : response.status === 401 ? "authentication_required" : "request_failed";
    const message = typeof detail === "string" ? detail : detail?.message || `Request failed (${response.status})`;
    throw new AccessApiError(message, response.status, code);
  }
  return response.json() as Promise<T>;
}

function tenantBase(tenantId: string): string {
  return `/api/v1/tenants/${encodeURIComponent(tenantId)}`;
}

export const fetchAccessIdentity = (fetcher: Fetcher = fetch) =>
  request<AccessIdentity>("/api/v1/auth/identity", {}, fetcher);

export const switchActiveTenant = (tenantId: string, fetcher: Fetcher = fetch) =>
  request<AccessIdentity>("/api/v1/auth/active-tenant", { method: "POST", body: JSON.stringify({ tenant_id: tenantId }) }, fetcher);

export function fetchMembers(tenantId: string, filters: AccessFilters, fetcher: Fetcher = fetch) {
  const params = new URLSearchParams({ page: String(filters.page), page_size: "25" });
  if (filters.query.trim()) params.set("query", filters.query.trim());
  if (filters.status) params.set("status", filters.status);
  if (filters.role) params.set("role", filters.role);
  return request<Page<AccessMember>>(`${tenantBase(tenantId)}/members?${params}`, {}, fetcher);
}

export const fetchRoles = (tenantId: string, fetcher: Fetcher = fetch) =>
  request<Page<AccessRole>>(`${tenantBase(tenantId)}/roles?page=1&page_size=100`, {}, fetcher);

export async function fetchPermissions(tenantId: string, fetcher: Fetcher = fetch) {
  const response = await request<{ items: AccessPermission[] }>(`${tenantBase(tenantId)}/permissions`, {}, fetcher);
  return response.items;
}

export const addMember = (tenantId: string, body: { email?: string; user_id?: string; status: "invited" | "active"; reason: string }, fetcher: Fetcher = fetch) =>
  request(`${tenantBase(tenantId)}/members`, { method: "POST", body: JSON.stringify(body) }, fetcher);

export const updateMemberStatus = (tenantId: string, membershipId: string, action: "activate" | "suspend" | "restore" | "remove", reason: string, fetcher: Fetcher = fetch) =>
  request<{ membership_id: string; status: MembershipStatus }>(`${tenantBase(tenantId)}/members/${encodeURIComponent(membershipId)}`, { method: "PATCH", body: JSON.stringify({ action, reason }) }, fetcher);

export const assignMemberRole = (tenantId: string, membershipId: string, roleId: string, reason: string, fetcher: Fetcher = fetch) =>
  request(`${tenantBase(tenantId)}/members/${encodeURIComponent(membershipId)}/roles`, { method: "POST", body: JSON.stringify({ role_id: roleId, reason }) }, fetcher);

export const removeMemberRole = (tenantId: string, membershipId: string, roleId: string, reason: string, fetcher: Fetcher = fetch) =>
  request(`${tenantBase(tenantId)}/members/${encodeURIComponent(membershipId)}/roles/${encodeURIComponent(roleId)}`, { method: "DELETE", body: JSON.stringify({ reason }) }, fetcher);

export const createCustomRole = (tenantId: string, body: { role_key: string; name: string; description?: string; permission_keys: string[]; reason: string }, fetcher: Fetcher = fetch) =>
  request(`${tenantBase(tenantId)}/roles`, { method: "POST", body: JSON.stringify(body) }, fetcher);

export const updateCustomRole = (tenantId: string, roleId: string, body: { name: string; description?: string; permission_keys: string[]; reason: string }, fetcher: Fetcher = fetch) =>
  request(`${tenantBase(tenantId)}/roles/${encodeURIComponent(roleId)}`, { method: "PATCH", body: JSON.stringify(body) }, fetcher);
