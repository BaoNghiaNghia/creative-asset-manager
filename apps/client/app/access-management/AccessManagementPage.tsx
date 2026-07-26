import { useEffect, useState } from "react";
import {
  AccessApiError, addMember, assignMemberRole, createCustomRole, fetchAccessIdentity,
  fetchMembers, fetchPermissions, fetchRoles, removeMemberRole, switchActiveTenant,
  updateCustomRole, updateMemberStatus,
  type AccessFilters, type AccessIdentity, type AccessMember, type AccessPermission,
  type AccessRole, type Page,
} from "../../features/access_management";

export type AccessTab = "members" | "roles" | "my-access";
const tabs: Array<{ id: AccessTab; label: string }> = [
  { id: "members", label: "Members" }, { id: "roles", label: "Roles" }, { id: "my-access", label: "My access" },
];
const emptyMembers: Page<AccessMember> = { items: [], page: 1, page_size: 25, total: 0 };
const emptyFilters: AccessFilters = { query: "", status: "", role: "", page: 1 };
export type AccessPageState = "loading" | "ready" | "unauthenticated" | "no-tenant" | "permission-denied" | "stale-membership" | "error";

export function AccessManagementPage() {
  const requestedTab = new URLSearchParams(window.location.search).get("tab") as AccessTab | null;
  const [tab, setTab] = useState<AccessTab>(tabs.some(item => item.id === requestedTab) ? requestedTab! : "members");
  const [identity, setIdentity] = useState<AccessIdentity | null>(null);
  const [members, setMembers] = useState<Page<AccessMember>>(emptyMembers);
  const [roles, setRoles] = useState<AccessRole[]>([]);
  const [permissions, setPermissions] = useState<AccessPermission[]>([]);
  const [filters, setFilters] = useState<AccessFilters>(emptyFilters);
  const [state, setState] = useState<AccessPageState>("loading");
  const [message, setMessage] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const scopedFetch: typeof fetch = (url, init) => fetch(url, { ...init, signal: controller.signal });
    setState("loading"); setMessage("");
    fetchAccessIdentity(scopedFetch).then(async nextIdentity => {
      setIdentity(nextIdentity);
      if (!nextIdentity.active_tenant_id || !nextIdentity.available_tenants.some(item => item.id === nextIdentity.active_tenant_id)) {
        setState("no-tenant"); return;
      }
      if (!nextIdentity.permissions.includes("tenant_members.read")) { setState("permission-denied"); return; }
      const [nextMembers, nextRoles, nextPermissions] = await Promise.all([
        fetchMembers(nextIdentity.active_tenant_id, filters, scopedFetch),
        fetchRoles(nextIdentity.active_tenant_id, scopedFetch),
        fetchPermissions(nextIdentity.active_tenant_id, scopedFetch),
      ]);
      setMembers(nextMembers); setRoles(safeTenantRoles(nextRoles.items)); setPermissions(nextPermissions); setState("ready");
    }).catch(error => {
      if (controller.signal.aborted) return;
      setState(accessStateForError(error)); setMessage(error instanceof Error ? error.message : "Access Management could not be loaded.");
    });
    return () => controller.abort();
  }, [filters, reload]);

  function changeTab(next: AccessTab) {
    setTab(next); window.history.replaceState({}, "", `/settings/access${next === "members" ? "" : `?tab=${next}`}`);
  }
  async function changeTenant(tenantId: string) {
    try {
      const next = await switchActiveTenant(tenantId); setIdentity(next); setFilters(emptyFilters);
      setMessage(`Active tenant changed to ${next.available_tenants.find(item => item.id === tenantId)?.name || tenantId}.`);
      setReload(value => value + 1);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Tenant switch failed."); }
  }
  return <AccessManagementShell identity={identity}><AccessManagementContent
    state={state} identity={identity} members={members} roles={roles} permissions={permissions}
    filters={filters} tab={tab} message={message} onTab={changeTab} onFilters={setFilters}
    onTenant={changeTenant} onRetry={() => setReload(value => value + 1)}
    onMutation={() => setReload(value => value + 1)} onMessage={setMessage}
  /></AccessManagementShell>;
}

export function AccessManagementShell({ children, identity = null }: { children: React.ReactNode; identity?: AccessIdentity | null }) {
  return <main className="access-shell"><aside className="access-sidebar">
    <div className="brand"><b>C</b><span><strong>Creative assets</strong><small>Workspace settings</small></span></div>
    <p>WORKSPACE</p><a href="/">Asset Explorer</a>{identity?.permissions.includes("ai_operations.read") && <a href="/ai-operations">AI Operations</a>}
    <a href="/settings/access" className="active" aria-current="page">⚿ Access Management</a>
    <small>Permissions are enforced by the server for every tenant operation.</small>
  </aside><section className="access-main">{children}</section></main>;
}

type ContentProps = {
  state: AccessPageState; identity: AccessIdentity | null; members: Page<AccessMember>;
  roles: AccessRole[]; permissions: AccessPermission[]; filters: AccessFilters; tab: AccessTab;
  message?: string; onTab: (tab: AccessTab) => void; onFilters: (filters: AccessFilters) => void;
  onTenant: (tenantId: string) => void; onRetry: () => void; onMutation: () => void; onMessage: (value: string) => void;
};

export function AccessManagementContent(props: ContentProps) {
  if (props.state !== "ready") return <AccessState state={props.state} message={props.message} onRetry={props.onRetry} />;
  const activeTenant = props.identity!.available_tenants.find(item => item.id === props.identity!.active_tenant_id);
  return <><header className="access-header"><div><small>SETTINGS</small><h1>Access Management</h1><p>Manage tenant members, roles, and your active workspace.</p></div><div className="access-header-actions">
    {activeTenant && <span className="access-tenant-chip">Workspace · {activeTenant.name}</span>}
    <a href="/">← Back to assets</a>
  </div></header>
    <nav className="access-tabs" role="tablist" aria-label="Access Management sections" onKeyDown={event => handleAccessTabKeyDown(event, props.tab, props.onTab)}>
      {tabs.map(item => <button key={item.id} id={`access-tab-${item.id}`} role="tab" type="button" aria-selected={props.tab === item.id} aria-controls={`access-panel-${item.id}`} tabIndex={props.tab === item.id ? 0 : -1} className={props.tab === item.id ? "active" : ""} onClick={() => props.onTab(item.id)}>{item.label}</button>)}
    </nav>{props.message && <div className="access-notice" role="status" aria-live="polite">{props.message}</div>}
    <section id={`access-panel-${props.tab}`} role="tabpanel" aria-labelledby={`access-tab-${props.tab}`} tabIndex={0}>
      {props.tab === "members" ? <MembersTab {...props} /> : props.tab === "roles" ? <RolesTab {...props} /> : <MyAccessTab identity={props.identity!} onTenant={props.onTenant} />}
    </section></>;
}

export function accessStateForError(error: unknown): AccessPageState {
  if (!(error instanceof AccessApiError)) return "error";
  if (error.status === 401 || error.code === "authentication_required") return "unauthenticated";
  if (["tenant_membership_required", "tenant_mismatch"].includes(error.code)) return "stale-membership";
  if (error.status === 403 || error.code === "permission_required") return "permission-denied";
  return "error";
}

function AccessState({ state, message, onRetry }: { state: AccessPageState; message?: string; onRetry: () => void }) {
  if (state === "loading") return <div className="access-state" aria-busy="true"><i /><i /><span>Loading access settings…</span></div>;
  const copy = {
    unauthenticated: ["Sign in required", "Sign in to manage workspace access."],
    "no-tenant": ["No active tenant", "Ask an administrator to add you to a tenant, then sign in again."],
    "permission-denied": ["Permission required", "You are signed in, but tenant_members.read is required for this page."],
    "stale-membership": ["Membership changed", "Your active tenant membership is no longer valid. Refresh your session or choose another tenant."],
    error: ["Access settings unavailable", message || "The server could not load access settings."],
  }[state as Exclude<AccessPageState, "loading" | "ready">];
  return <div className="access-state" role={state === "error" ? "alert" : "status"}><span aria-hidden="true">⚿</span><h1>{copy[0]}</h1><p>{copy[1]}</p>{state === "unauthenticated" ? <a href="/">Return to sign in</a> : <button type="button" onClick={onRetry}>Retry</button>}</div>;
}

function MembersTab(props: ContentProps) {
  const { identity, members, roles, filters, onFilters, onMutation, onMessage } = props;
  const canManageMembers = identity!.permissions.includes("tenant_members.manage");
  const canManageRoles = identity!.permissions.includes("tenant_roles.manage");
  const pages = Math.max(1, Math.ceil(members.total / members.page_size));
  return <div className="access-content"><div className="access-toolbar"><div><small className="access-toolbar-label">Find a member</small><form aria-label="Member filters" onSubmit={event => event.preventDefault()}>
    <label>Search<input value={filters.query} onChange={event => onFilters({ ...filters, query: event.target.value, page: 1 })} placeholder="Name or email" /></label>
    <label>Status<select value={filters.status} onChange={event => onFilters({ ...filters, status: event.target.value, page: 1 })}><option value="">All statuses</option>{["invited", "active", "suspended", "removed"].map(value => <option key={value}>{value}</option>)}</select></label>
    <label>Role<select value={filters.role} onChange={event => onFilters({ ...filters, role: event.target.value, page: 1 })}><option value="">All roles</option>{roles.map(role => <option key={role.id} value={role.key}>{role.name}</option>)}</select></label>
  </form></div>{canManageMembers && <InviteMemberForm tenantId={identity!.active_tenant_id} onDone={onMutation} onMessage={onMessage} />}</div>
    {!members.items.length ? <div className="access-empty"><h2>No members found</h2><p>Adjust the filters or invite an existing application user.</p></div> : <div className="access-table-wrap"><table className="access-table"><caption>Tenant members</caption><thead><tr><th>Member</th><th>Status</th><th>Roles</th><th>Joined</th><th>Actions</th></tr></thead><tbody>{members.items.map(member => <tr key={member.membership_id}>
      <td><strong>{member.display_name || "Unnamed user"}</strong><small>{member.email || member.user_id}</small></td><td><StatusBadge status={member.status} /></td>
      <td><div className="role-chips">{member.roles.length ? member.roles.map(role => <span key={role.id}>{role.name}{canManageRoles && <DangerousAction label="Remove" title="Remove role" description={`Remove ${role.name} from this member?`} onConfirm={reason => removeMemberRole(identity!.active_tenant_id, member.membership_id, role.id, reason).then(() => { onMessage("Role removed."); onMutation(); })} />}</span>) : <em>No roles</em>}</div></td>
      <td>{formatDate(member.joined_at)}</td><td><MemberActions member={member} roles={roles} tenantId={identity!.active_tenant_id} canManageMembers={canManageMembers} canManageRoles={canManageRoles} onDone={onMutation} onMessage={onMessage} /></td>
    </tr>)}</tbody></table></div>}
    <div className="access-pagination" aria-label="Member pagination"><button disabled={filters.page <= 1} onClick={() => onFilters({ ...filters, page: filters.page - 1 })}>Previous</button><span>Page {filters.page} of {pages}</span><button disabled={filters.page >= pages} onClick={() => onFilters({ ...filters, page: filters.page + 1 })}>Next</button></div>
  </div>;
}

function InviteMemberForm({ tenantId, onDone, onMessage }: { tenantId: string; onDone: () => void; onMessage: (value: string) => void }) {
  const [open, setOpen] = useState(false); const [email, setEmail] = useState(""); const [reason, setReason] = useState(""); const [error, setError] = useState("");
  if (!open) return <button className="primary" type="button" onClick={() => setOpen(true)}>Invite member</button>;
  return <form className="access-inline-form" aria-label="Invite or add member" onSubmit={async event => { event.preventDefault(); setError(""); try { await addMember(tenantId, { email, status: "invited", reason }); setOpen(false); onMessage("Invitation recorded. Email delivery is not configured; share access instructions separately."); onDone(); } catch (next) { setError(next instanceof Error ? next.message : "Invitation failed."); } }}>
    <label>Email<input type="email" required value={email} onChange={event => setEmail(event.target.value)} /></label><label>Reason<input required minLength={3} value={reason} onChange={event => setReason(event.target.value)} /></label><button className="primary">Save invitation</button><button type="button" onClick={() => setOpen(false)}>Cancel</button>{error && <span role="alert">{error}</span>}
  </form>;
}

function MemberActions({ member, roles, tenantId, canManageMembers, canManageRoles, onDone, onMessage }: { member: AccessMember; roles: AccessRole[]; tenantId: string; canManageMembers: boolean; canManageRoles: boolean; onDone: () => void; onMessage: (value: string) => void }) {
  const [roleId, setRoleId] = useState(""); const available = roles.filter(role => role.status === "active" && !member.roles.some(item => item.id === role.id));
  const transition = member.status === "suspended" || member.status === "removed" ? "restore" : member.status === "invited" ? "activate" : null;
  return <div className="member-actions">{canManageRoles && available.length > 0 && <><label className="sr-only" htmlFor={`role-${member.membership_id}`}>Role for member</label><select id={`role-${member.membership_id}`} value={roleId} onChange={event => setRoleId(event.target.value)}><option value="">Assign role…</option>{available.map(role => <option key={role.id} value={role.id}>{role.name}</option>)}</select><button disabled={!roleId} onClick={async () => { await assignMemberRole(tenantId, member.membership_id, roleId, "Role assigned from Access Management"); onMessage("Role assigned."); onDone(); }}>Assign</button></>}
    {canManageMembers && transition && <button onClick={async () => { await updateMemberStatus(tenantId, member.membership_id, transition, `${transition} member from Access Management`); onMessage(`Member ${transition}d.`); onDone(); }}>{transition === "restore" ? "Restore" : "Activate"}</button>}
    {canManageMembers && member.status === "active" && <DangerousAction label="Suspend" title="Suspend member" description="The member will immediately lose effective tenant access." onConfirm={reason => updateMemberStatus(tenantId, member.membership_id, "suspend", reason).then(() => { onMessage("Member suspended."); onDone(); })} />}
    {canManageMembers && member.status !== "removed" && <DangerousAction label="Remove" title="Remove member" description="Membership history is preserved, but tenant access is removed." onConfirm={reason => updateMemberStatus(tenantId, member.membership_id, "remove", reason).then(() => { onMessage("Member removed."); onDone(); })} />}</div>;
}

function RolesTab(props: ContentProps) {
  const canManage = props.identity!.permissions.includes("tenant_roles.manage"); const [editing, setEditing] = useState<AccessRole | "new" | null>(null);
  return <div className="access-content"><div className="access-section-heading"><div><h2>Tenant roles</h2><p>System roles are protected. Platform administration is never assignable here.</p></div>{canManage && <button className="primary" onClick={() => setEditing("new")}>Create custom role</button>}</div>
    {editing && <RoleEditor role={editing === "new" ? null : editing} tenantId={props.identity!.active_tenant_id} permissions={props.permissions} actorPermissions={props.identity!.permissions} onCancel={() => setEditing(null)} onDone={() => { setEditing(null); props.onMessage("Custom role saved."); props.onMutation(); }} />}
    <div className="role-grid">{props.roles.map(role => <article key={role.id}><header><div><h3>{role.name}</h3><code>{role.key}</code></div>{role.protected || role.system ? <span className="protected-badge">Protected system role</span> : <span>Custom role</span>}</header><p>{role.description || "No description"}</p><PermissionGroups keys={role.permissions} /><footer><StatusBadge status={role.status} />{canManage && !role.protected && !role.system && <button onClick={() => setEditing(role)}>Edit role</button>}</footer></article>)}</div></div>;
}

function RoleEditor({ role, tenantId, permissions, actorPermissions, onCancel, onDone }: { role: AccessRole | null; tenantId: string; permissions: AccessPermission[]; actorPermissions: string[]; onCancel: () => void; onDone: () => void }) {
  const allowed = permissions.filter(item => actorPermissions.includes(item.key) && !item.key.startsWith("platform."));
  const [name, setName] = useState(role?.name || ""); const [key, setKey] = useState(role?.key || ""); const [description, setDescription] = useState(role?.description || ""); const [selected, setSelected] = useState(new Set(role?.permissions || [])); const [reason, setReason] = useState(""); const [error, setError] = useState("");
  return <form className="role-editor" aria-label={role ? "Edit custom role" : "Create custom role"} onSubmit={async event => { event.preventDefault(); setError(""); const body = { name, description, permission_keys: [...selected], reason }; try { if (role) await updateCustomRole(tenantId, role.id, body); else await createCustomRole(tenantId, { ...body, role_key: key }); onDone(); } catch (next) { setError(next instanceof Error ? next.message : "Role could not be saved."); } }}>
    <h3>{role ? `Edit ${role.name}` : "Create custom role"}</h3><div className="role-fields"><label>Name<input required value={name} onChange={event => setName(event.target.value)} /></label>{!role && <label>Role key<input required pattern="[a-z0-9_.-]+" value={key} onChange={event => setKey(event.target.value.toLowerCase())} /></label>}<label>Description<input value={description} onChange={event => setDescription(event.target.value)} /></label><label>Reason<input required minLength={3} value={reason} onChange={event => setReason(event.target.value)} /></label></div>
    <fieldset><legend>Permissions this administrator may grant</legend>{groupPermissions(allowed).map(group => <section key={group.domain}><h4>{group.domain}</h4>{group.items.map(item => <label key={item.key}><input type="checkbox" checked={selected.has(item.key)} onChange={() => setSelected(current => { const next = new Set(current); next.has(item.key) ? next.delete(item.key) : next.add(item.key); return next; })} /> <code>{item.key}</code><span>{item.description}</span></label>)}</section>)}</fieldset>{error && <p role="alert">{error}</p>}<button className="primary">Save role</button><button type="button" onClick={onCancel}>Cancel</button>
  </form>;
}

function MyAccessTab({ identity, onTenant }: { identity: AccessIdentity; onTenant: (tenantId: string) => void }) {
  const tenant = identity.available_tenants.find(item => item.id === identity.active_tenant_id);
  return <div className="access-content my-access"><section><h2>Current user</h2><dl><dt>User ID</dt><dd><code>{identity.user_id}</code></dd><dt>Active tenant</dt><dd>{tenant?.name || identity.active_tenant_id}</dd><dt>Authorization source</dt><dd>{identity.authorization_source}</dd></dl></section><section><h2>Switch tenant</h2><label>Available tenants<select value={identity.active_tenant_id} onChange={event => onTenant(event.target.value)}>{identity.available_tenants.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label></section><section><h2>Current roles</h2><div className="role-chips">{identity.roles.length ? identity.roles.map(role => <span key={role}>{role}</span>) : <em>No assigned roles</em>}</div></section><section className="access-permissions-card"><h2>Effective permissions</h2><PermissionGroups keys={identity.permissions} /></section></div>;
}

export function safeTenantRoles(roles: AccessRole[]) { return roles.filter(role => !role.key.includes("platform_admin")); }
export function groupPermissions(items: AccessPermission[] | string[]) {
  const normalized = items.map(item => typeof item === "string" ? { id: item, key: item, description: "" } : item); const groups = new Map<string, AccessPermission[]>();
  normalized.forEach(item => { const domain = item.key.split(".")[0] || "other"; groups.set(domain, [...(groups.get(domain) || []), item]); });
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([domain, values]) => ({ domain, items: values.sort((a, b) => a.key.localeCompare(b.key)) }));
}
function PermissionGroups({ keys }: { keys: string[] }) { return keys.length ? <div className="permission-groups">{groupPermissions(keys).map(group => <section key={group.domain}><h4>{group.domain}</h4><ul>{group.items.map(item => <li key={item.key}><code>{item.key}</code></li>)}</ul></section>)}</div> : <p>No permissions assigned.</p>; }
function StatusBadge({ status }: { status: string }) { return <span className={`access-status status-${status}`}><i aria-hidden="true" />{status.replace("_", " ")}</span>; }
function formatDate(value: string | null) { return value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value)) : "Not joined"; }

export function DangerousAction({ label, title, description, onConfirm }: { label: string; title: string; description: string; onConfirm: (reason: string) => Promise<unknown> }) {
  const [open, setOpen] = useState(false); const [reason, setReason] = useState(""); const [error, setError] = useState("");
  return <><button type="button" className="danger-link" aria-haspopup="dialog" onClick={() => setOpen(true)}>{label}</button>{open && <div className="access-dialog-backdrop"><div role="dialog" aria-modal="true" aria-labelledby="danger-title"><h2 id="danger-title">{title}</h2><p>{description}</p><label>Reason<input autoFocus required minLength={3} value={reason} onChange={event => setReason(event.target.value)} /></label>{error && <p role="alert">{error}</p>}<div><button type="button" onClick={() => setOpen(false)}>Cancel</button><button type="button" className="danger" disabled={reason.trim().length < 3} onClick={async () => { try { await onConfirm(reason.trim()); setOpen(false); } catch (next) { setError(next instanceof Error ? next.message : "Action failed."); } }}>Confirm {label.toLowerCase()}</button></div></div></div>}</>;
}

export function handleAccessTabKeyDown(event: React.KeyboardEvent<HTMLElement>, active: AccessTab, onTab: (tab: AccessTab) => void) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; event.preventDefault(); const current = tabs.findIndex(item => item.id === active);
  const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : event.key === "ArrowRight" ? (current + 1) % tabs.length : (current - 1 + tabs.length) % tabs.length;
  onTab(tabs[next].id); event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
}
