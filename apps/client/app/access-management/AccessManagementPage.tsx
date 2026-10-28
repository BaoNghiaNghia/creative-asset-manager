import { useEffect, useRef, useState } from "react";
import { ActionMessageToast } from "../components/ActionToast";
import {
  AccessApiError, addMember, assignMemberRole, createCustomRole, replaceMemberNavigationAccess, fetchAccessIdentity, fetchViewerFolderOptions, fetchViewerFolderScopes, replaceViewerFolderScopes,
  fetchMembers, fetchPermissions, fetchNavigationModules, fetchRoles, removeMemberRole, switchActiveTenant,
  updateCustomRole, updateMemberStatus,
  type AccessFilters, type AccessIdentity, type AccessMember, type AccessPermission,
  type AccessRole, type AccessNavigationModule, type Page,
} from "../../features/access_management";
import { CenteredLoadingState } from "../components/CenteredLoadingState";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation, WorkspaceNavigationIcon, roleBasedNavbarModules } from "../components/WorkspaceNavigation";
import { WorkspaceBackToAssets, WorkspacePageHeader } from "../components/WorkspacePageHeader";

export type AccessTab = "members" | "roles" | "my-access";
const tabs: Array<{ id: AccessTab; label: string }> = [
  { id: "members", label: "Members" }, { id: "roles", label: "Roles" }, { id: "my-access", label: "My access" },
];
const emptyMembers: Page<AccessMember> = { items: [], page: 1, page_size: 25, total: 0 };
const emptyFilters: AccessFilters = { query: "", status: "", role: "", page: 1 };
export function memberAvatarLabel(name: string | null, email: string | null): string { const source=(name||email||"?").trim();const words=source.split(/\s+/).filter(Boolean);return (words.length>1?words[0][0]+words.at(-1)![0]:source.slice(0,2)).toUpperCase() }
function MemberAvatar({ member }: { member: AccessMember }) { const [failed,setFailed]=useState(false);useEffect(()=>setFailed(false),[member.avatar_url]);return member.avatar_url&&!failed?<img className="access-member-avatar" src={member.avatar_url} alt="" referrerPolicy="no-referrer" onError={()=>setFailed(true)}/>:<span className="access-member-avatar fallback" role="img" aria-label={`Avatar của ${member.display_name||member.email||"thành viên"}`}>{memberAvatarLabel(member.display_name,member.email)}</span> }
export type AccessPageState = "loading" | "ready" | "unauthenticated" | "no-tenant" | "permission-denied" | "stale-membership" | "error";

export function AccessManagementPage() {
  const requestedTab = new URLSearchParams(window.location.search).get("tab") as AccessTab | null;
  const [tab, setTab] = useState<AccessTab>(tabs.some(item => item.id === requestedTab) ? requestedTab! : "members");
  const [identity, setIdentity] = useState<AccessIdentity | null>(null);
  const [members, setMembers] = useState<Page<AccessMember>>(emptyMembers);
  const [roles, setRoles] = useState<AccessRole[]>([]);
  const [permissions, setPermissions] = useState<AccessPermission[]>([]);
  const [navigationCatalog, setNavigationCatalog] = useState<AccessNavigationModule[]>([]);
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
      const [nextMembers, nextRoles, nextPermissions, nextModules] = await Promise.all([
        fetchMembers(nextIdentity.active_tenant_id, filters, scopedFetch),
        fetchRoles(nextIdentity.active_tenant_id, scopedFetch),
        fetchPermissions(nextIdentity.active_tenant_id, scopedFetch),
        fetchNavigationModules(nextIdentity.active_tenant_id, scopedFetch),
      ]);
      setMembers(nextMembers); setRoles(safeTenantRoles(nextRoles.items)); setPermissions(nextPermissions);
      setNavigationCatalog(nextModules); setState("ready");
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
    state={state} identity={identity} members={members} roles={roles} permissions={permissions} navigationCatalog={navigationCatalog}
    filters={filters} tab={tab} message={message} onTab={changeTab} onFilters={setFilters}
    onTenant={changeTenant} onRetry={() => setReload(value => value + 1)}
    onMutation={() => setReload(value => value + 1)} onMessage={setMessage}
  /></AccessManagementShell>;
}

export function AccessManagementShell({ children, identity = null }: { children: React.ReactNode; identity?: AccessIdentity | null }) {
  return <main className="access-shell"><aside className="access-sidebar">
    <div className="brand"><b><BrandIcon /></b><span><strong>Creative assets</strong><small>{identity?.email || "Workspace settings"}</small></span></div>
    <WorkspaceNavigation active="access" showOperations={Boolean(identity?.permissions.includes("ai_operations.read"))} permissions={identity?.permissions} navigationModules={identity?.navigation_modules} />
    <small className="access-sidebar-note">Permissions are enforced by the server for every tenant operation.</small>
  </aside><section className="access-main">{children}</section></main>;
}

type ContentProps = {
  state: AccessPageState; identity: AccessIdentity | null; members: Page<AccessMember>;
  roles: AccessRole[]; permissions: AccessPermission[]; navigationCatalog?: AccessNavigationModule[]; filters: AccessFilters; tab: AccessTab;
  message?: string; onTab: (tab: AccessTab) => void; onFilters: (filters: AccessFilters) => void;
  onTenant: (tenantId: string) => void; onRetry: () => void; onMutation: () => void; onMessage: (value: string) => void;
};

export function AccessManagementContent(props: ContentProps) {
  if (props.state !== "ready") return <AccessState state={props.state} message={props.message} onRetry={props.onRetry} />;
  const activeTenant = props.identity!.available_tenants.find(item => item.id === props.identity!.active_tenant_id);
  return <><WorkspacePageHeader
    className="access-header"
    route="access"
    actionsClassName="access-header-actions"
    actions={<>
      {activeTenant && <span className="workspace-page-chip access-tenant-chip">Workspace · {activeTenant.name}</span>}
      <WorkspaceBackToAssets />
    </>}
  />
    <nav className="access-tabs" role="tablist" aria-label="Access Management sections" onKeyDown={event => handleAccessTabKeyDown(event, props.tab, props.onTab)}>
      {tabs.map(item => <button key={item.id} id={`access-tab-${item.id}`} role="tab" type="button" aria-selected={props.tab === item.id} aria-controls={`access-panel-${item.id}`} tabIndex={props.tab === item.id ? 0 : -1} className={props.tab === item.id ? "active" : ""} onClick={() => props.onTab(item.id)}>{item.label}</button>)}
    </nav><ActionMessageToast message={props.message} />
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
  if (state === "loading") return <CenteredLoadingState
    kind="permissions"
    layout="panel"
    title="Loading access settings…"
    detail="Checking workspace membership, roles, and effective permissions."
  />;
  const copy = {
    unauthenticated: ["Sign in required", "Sign in to manage workspace access."],
    "no-tenant": ["No active tenant", "Ask an administrator to add you to a tenant, then sign in again."],
    "permission-denied": ["Permission required", "You are signed in, but tenant_members.read is required for this page."],
    "stale-membership": ["Membership changed", "Your active tenant membership is no longer valid. Refresh your session or choose another tenant."],
    error: ["Access settings unavailable", message || "The server could not load access settings."],
  }[state as Exclude<AccessPageState, "loading" | "ready">];
  return <div className={`access-state access-state--${state}`} role={state === "error" ? "alert" : "status"}>
    <div className="access-state-card">
      <div className="access-state-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M12 3 20 6v5c0 5.1-3.4 8.7-8 10-4.6-1.3-8-4.9-8-10V6l8-3Z"/><path d="M12 8v5m0 3h.01"/></svg></div>
      <span className="access-state-eyebrow">Workspace access</span>
      <h1>{copy[0]}</h1>
      <p>{copy[1]}</p>
      <div className="access-state-actions">{state === "unauthenticated" ? <a className="access-primary-action" href="/">Return to sign in</a> : <button className="access-primary-action" type="button" onClick={onRetry}>Try again</button>}</div>
      {state === "permission-denied" && <small className="access-state-hint">If you believe this is a mistake, contact your workspace administrator.</small>}
    </div>
  </div>;
}

function MembersTab(props: ContentProps) {
  const { identity, members, roles, filters, onFilters, onMutation, onMessage } = props;
  const [editingId, setEditingId] = useState<string | null>(null);
  const canManageMembers = Boolean(identity?.permissions.includes("tenant_members.manage"));
  const canManageRoles = Boolean(identity?.permissions.includes("tenant_roles.manage"));
  const canEdit = canManageMembers || canManageRoles;
  const pages = Math.max(1, Math.ceil(members.total / members.page_size));
  const editing = members.items.find(member => member.membership_id === editingId);
  return <div className="access-content access-members-content">
    <div className="access-toolbar access-toolbar-compact">
      <form aria-label="Member filters" onSubmit={event => event.preventDefault()}>
        <label>Search<input type="search" value={filters.query} onChange={event => onFilters({ ...filters, query: event.target.value, page: 1 })} placeholder="Name or email" /></label>
        <label>Status<select value={filters.status} onChange={event => onFilters({ ...filters, status: event.target.value, page: 1 })}><option value="">All statuses</option>{["invited", "active", "suspended", "removed"].map(value => <option key={value} value={value}>{value}</option>)}</select></label>
        <label>Role<select value={filters.role} onChange={event => onFilters({ ...filters, role: event.target.value, page: 1 })}><option value="">All roles</option>{roles.map(role => <option key={role.id} value={role.key}>{role.name}</option>)}</select></label>
      </form>
      {canManageMembers && <InviteMemberForm tenantId={identity!.active_tenant_id} onDone={onMutation} onMessage={onMessage} />}
    </div>
    <div className="access-members-heading"><div><h2>Tenant members</h2><span>{members.total} members · Manage modules and workspace roles</span></div></div>
    {!members.items.length ? <div className="access-empty"><h2>No members found</h2><p>Adjust your filters or invite a member.</p></div> :
    <div className="access-table-wrap"><table className="access-table access-member-table">
      <caption className="sr-only">Tenant members</caption>
      <thead><tr><th>Member</th><th>Navbar access</th><th>Role</th><th>Status</th><th>Joined</th><th>Actions</th></tr></thead>
      <tbody>{members.items.map(member => {
        const admin = member.roles.some(role => role.key === "tenant_admin");
        const selected = member.navigation_modules;
        return <tr key={member.membership_id}>
          <td><div className="access-member-profile"><MemberAvatar member={member} /><span><strong>{member.display_name || "Unnamed user"}</strong><small>{member.email || member.user_id}</small></span></div></td>
          <td><span className={"access-nav-badge " + (admin ? "admin" : selected === undefined || selected === null ? "legacy" : "scoped")}>{admin ? "All modules · Admin" : selected == null ? "Role-based access" : selected.length + " / 9 modules"}</span>{selected && selected.length > 0 && <small className="access-nav-preview">{selected.map(key => props.navigationCatalog?.find(item => item.key === key)?.label || key).join(", ")}</small>}</td>
          <td><div className="access-compact-roles">{member.roles.length ? <><span>{member.roles[0].name}</span>{member.roles.length > 1 && <small>+{member.roles.length - 1}</small>}</> : <em>No roles</em>}</div></td>
          <td><StatusBadge status={member.status} /></td>
          <td className="access-joined">{formatDate(member.joined_at)}</td>
          <td>{canEdit ? <button type="button" className="access-manage-button" aria-haspopup="dialog" aria-label={"Manage access for " + (member.display_name || member.email || member.user_id)} onClick={() => setEditingId(member.membership_id)}>Manage access</button> : <span className="access-readonly">Read only</span>}</td>
        </tr>;
      })}</tbody>
    </table></div>}
    <div className="access-pagination" aria-label="Member pagination"><button disabled={filters.page <= 1} onClick={() => onFilters({ ...filters, page: filters.page - 1 })}>Previous</button><span>Page {filters.page} of {pages}</span><button disabled={filters.page >= pages} onClick={() => onFilters({ ...filters, page: filters.page + 1 })}>Next</button></div>
    {editing && <MemberAccessDialog key={editing.membership_id} member={editing}
      tenantId={identity!.active_tenant_id} roles={roles} navigationCatalog={props.navigationCatalog || []}
      canManageMembers={canManageMembers} canManageRoles={canManageRoles}
      onClose={() => setEditingId(null)}
      onDone={() => { setEditingId(null); onMutation(); }}
      onMessage={onMessage}/>}
  </div>;
}

function MemberAccessDialog({member, tenantId, roles, navigationCatalog, canManageMembers, canManageRoles, onClose, onDone, onMessage}: {
  member: AccessMember; tenantId: string; roles: AccessRole[]; navigationCatalog: AccessNavigationModule[];
  canManageMembers: boolean; canManageRoles: boolean; onClose: () => void; onDone: () => void; onMessage: (message: string) => void;
}) {
  const admin = member.roles.some(role => role.key === "tenant_admin");
  const [mode, setMode] = useState<"roles" | "navbar">(member.navigation_modules == null ? "roles" : "navbar");
  const [selected, setSelected] = useState<Set<string>>(() => new Set(member.navigation_modules ?? roleBasedNavbarModules(roles.filter(role => role.status === "active" && member.roles.some(assigned => assigned.id === role.id || assigned.key === role.key)).flatMap(role => role.permissions))));
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const canAssignModules = canManageMembers && canManageRoles && !admin && member.status !== "removed";
  const savingRef = useRef(false);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape" && !savingRef.current) onClose(); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose]);
  const selectedModules = [...selected];
  return <div className="access-member-dialog-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !saving) onClose(); }}>
    <section className="access-member-dialog" role="dialog" aria-modal="true" aria-label={"Manage access for " + (member.display_name || member.email || member.user_id)}>
      <header className="access-member-dialog-header"><div className="access-member-dialog-person"><MemberAvatar member={member}/><div><small>MEMBER ACCESS</small><h2>{member.display_name || "Unnamed user"}</h2><p>{member.email || member.user_id}</p></div></div><button type="button" className="access-member-dialog-close" autoFocus aria-label="Close access editor" onClick={onClose}>×</button></header>
      <div className="access-member-dialog-body">
        <section className="access-nav-editor">
          <div className="access-nav-editor-heading"><div><h3>Navbar access</h3><p>Selected modules grant all tenant actions supported by that module. Other modules remain inaccessible for scoped members.</p></div><span className="access-selection-count">{mode === "roles" ? "Role-based" : selected.size + " selected"}</span></div>
          {admin ? <p className="access-admin-protected">Tenant administrators keep unrestricted navigation access. Their scope cannot be reduced here.</p> : <>
            <div className="access-scope-switch" role="group" aria-label="Access policy"><button type="button" aria-pressed={mode === "navbar"} disabled={!canAssignModules || saving} className={mode === "navbar" ? "active" : ""} onClick={() => setMode("navbar")}>Selected navbar modules</button><button type="button" aria-pressed={mode === "roles"} disabled={!canAssignModules || saving} className={mode === "roles" ? "active" : ""} onClick={() => setMode("roles")}>Use existing roles</button></div>
            {mode === "navbar" && <fieldset className="access-nav-module-grid" disabled={!canAssignModules || saving}><legend>Allowed navbar sections</legend>
              {navigationCatalog.map(option => <label key={option.key} className={"access-nav-module " + (selected.has(option.key) ? "selected" : "") + (!option.can_grant && !selected.has(option.key) ? " unavailable" : "")}>
                <input type="checkbox" checked={selected.has(option.key)} disabled={!option.can_grant || !canAssignModules || saving}
                  onChange={() => setSelected(current => { const next = new Set(current); next.has(option.key) ? next.delete(option.key) : next.add(option.key); return next; })}/>
                <WorkspaceNavigationIcon name={option.key as import("../components/WorkspaceNavigation").WorkspaceRoute} />
                <span>{option.label}</span>
              </label>)}
              {!navigationCatalog.length && <p role="status">Navbar permissions are not available.</p>}
            </fieldset>}
            {mode === "navbar" && member.navigation_modules == null && <p className="access-nav-scope-help" role="note">These checkboxes reflect the current role-based navbar. Saving converts the selection to full permissions within each chosen module; viewing this preview does not change access.</p>}
            {mode === "navbar" && <p className="access-nav-scope-help">Granted: {selectedModules.length ? selectedModules.map(key => navigationCatalog.find(option => option.key === key)?.label || key).join(", ") : "No modules (member cannot access workspace features)"}. Tenant membership and folder security still apply.</p>}
            {canAssignModules && <div className="access-nav-save"><label>Reason for change<input value={reason} minLength={3} disabled={saving} onChange={event => setReason(event.target.value)} placeholder="e.g. Assign inventory and review work" /></label><button className="primary" type="button" disabled={saving || reason.trim().length < 3 || (mode === "navbar" && !navigationCatalog.length)} onClick={async () => {
              if (savingRef.current) return; savingRef.current = true; setSaving(true); setError("");
              try { await replaceMemberNavigationAccess(tenantId, member.membership_id, mode === "roles" ? null : selectedModules, reason.trim()); onMessage("Navbar permissions updated."); onDone(); }
              catch (next) { setError(next instanceof Error ? next.message : "Could not update navigation access."); }
              finally { savingRef.current = false; setSaving(false); }
            }}>{saving ? "Saving…" : "Save navbar access"}</button></div>}
          </>}
        </section>
        <section className="access-member-detail-roles"><h3>Roles & membership</h3><p>Manage existing RBAC roles and member status separately from navbar permissions.</p>
          <div className="role-chips">{member.roles.length ? member.roles.map(role => <span key={role.id}>{role.name}{canManageRoles && <DangerousAction label="Remove" title="Remove role" description={"Remove " + role.name + " from this member?"} onConfirm={reason => removeMemberRole(tenantId, member.membership_id, role.id, reason).then(() => { onMessage("Role removed."); onDone(); })} />}</span>) : <em>No roles</em>}</div>
          <div className="member-action-stack"><MemberActions member={member} roles={roles} tenantId={tenantId} canManageMembers={canManageMembers} canManageRoles={canManageRoles} onDone={onDone} onMessage={onMessage}/>{canManageMembers && member.roles.some(role => role.key === "viewer") && <ViewerFolderScopeEditor tenantId={tenantId} membershipId={member.membership_id} onMessage={onMessage}/>}</div>
        </section>
      </div>
      {error && <p className="access-nav-error" role="alert">{error}</p>}
      <footer className="access-member-dialog-footer"><button type="button" disabled={saving} onClick={onClose}>Close</button></footer>
    </section>
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
  const [roleId, setRoleId] = useState("");
  const [pending, setPending] = useState<"role"|"status"|null>(null);
  const pendingRef = useRef(false);
  const available = roles.filter(role => role.status === "active" && !member.roles.some(item => item.id === role.id));
  const transition = member.status === "suspended" || member.status === "removed" ? "restore" : member.status === "invited" ? "activate" : null;
  return <div className="member-actions">{canManageRoles && available.length > 0 && <div className="member-role-assignment"><label className="sr-only" htmlFor={`role-${member.membership_id}`}>Role for member</label><select disabled={pending !== null} id={`role-${member.membership_id}`} value={roleId} onChange={event => setRoleId(event.target.value)}><option value="">Assign role…</option>{available.map(role => <option key={role.id} value={role.id}>{role.name}</option>)}</select><button disabled={!roleId || pending !== null} onClick={async () => { if (pendingRef.current) return; pendingRef.current = true; setPending("role"); try { await assignMemberRole(tenantId, member.membership_id, roleId, "Role assigned from Access Management"); setRoleId(""); onMessage("Role assigned."); onDone(); } finally { pendingRef.current = false; setPending(null); } }}>{pending === "role" ? "Assigning…" : "Assign"}</button></div>}
    <div className="member-status-actions">{canManageMembers && transition && <button disabled={pending !== null} onClick={async () => { if (pendingRef.current) return; pendingRef.current = true; setPending("status"); try { await updateMemberStatus(tenantId, member.membership_id, transition, `${transition} member from Access Management`); onMessage(`Member ${transition}d.`); onDone(); } finally { pendingRef.current = false; setPending(null); } }}>{pending === "status" ? "Updating…" : transition === "restore" ? "Restore" : "Activate"}</button>}
      {canManageMembers && member.status === "active" && <DangerousAction label="Suspend" title="Suspend member" description="The member will immediately lose effective tenant access." onConfirm={reason => updateMemberStatus(tenantId, member.membership_id, "suspend", reason).then(() => { onMessage("Member suspended."); onDone(); })} />}
      {canManageMembers && member.status !== "removed" && <DangerousAction label="Remove" title="Remove member" description="Membership history is preserved, but tenant access is removed." onConfirm={reason => updateMemberStatus(tenantId, member.membership_id, "remove", reason).then(() => { onMessage("Member removed."); onDone(); })} />}</div></div>;
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
  return <div className="access-content my-access"><section><h2>Current user</h2><dl><dt>User ID</dt><dd><code>{identity.user_id}</code></dd><dt>Active tenant</dt><dd>{tenant?.name || identity.active_tenant_id}</dd><dt>Authorization source</dt><dd>{identity.authorization_source}</dd></dl></section><section><h2>Switch tenant</h2><label>Available tenants<select value={identity.active_tenant_id} onChange={event => onTenant(event.target.value)}>{identity.available_tenants.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label></section><section><h2>Current roles</h2><div className="role-chips">{identity.roles.length ? identity.roles.map(role => <span key={role}>{role}</span>) : <em>No assigned roles</em>}</div></section><section className="access-module-summary"><h2>Navbar permissions</h2>{identity.roles.includes("tenant_admin") ? <p>All modules · Tenant administrator</p> : identity.navigation_modules == null ? <p>Existing role permissions</p> : <div className="role-chips">{identity.navigation_modules.length ? identity.navigation_modules.map(key => <span key={key}>{key}</span>) : <em>No navbar modules assigned</em>}</div>}</section><section className="access-permissions-card"><h2>Effective permissions</h2><PermissionGroups keys={identity.permissions} /></section></div>;
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
  const [open, setOpen] = useState(false); const [reason, setReason] = useState(""); const [error, setError] = useState(""); const [submitting, setSubmitting] = useState(false); const submittingRef = useRef(false);
  return <><button type="button" className="danger-link" data-action={label.toLowerCase()} aria-haspopup="dialog" onClick={() => setOpen(true)}>{label}</button>{open && <div className="access-dialog-backdrop"><div role="dialog" aria-modal="true" aria-labelledby="danger-title"><h2 id="danger-title">{title}</h2><p>{description}</p><label>Reason<input autoFocus required minLength={3} disabled={submitting} value={reason} onChange={event => setReason(event.target.value)} /></label>{error && <p role="alert">{error}</p>}<div><button type="button" disabled={submitting} onClick={() => setOpen(false)}>Cancel</button><button type="button" className="danger" disabled={submitting || reason.trim().length < 3} onClick={async () => { if (submittingRef.current) return; submittingRef.current = true; setSubmitting(true); setError(""); try { await onConfirm(reason.trim()); setOpen(false); } catch (next) { setError(next instanceof Error ? next.message : "Action failed."); } finally { submittingRef.current = false; setSubmitting(false); } }}>{submitting ? "Working…" : `Confirm ${label.toLowerCase()}`}</button></div></div></div>}</>;
}

export function handleAccessTabKeyDown(event: React.KeyboardEvent<HTMLElement>, active: AccessTab, onTab: (tab: AccessTab) => void) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; event.preventDefault(); const current = tabs.findIndex(item => item.id === active);
  const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : event.key === "ArrowRight" ? (current + 1) % tabs.length : (current - 1 + tabs.length) % tabs.length;
  onTab(tabs[next].id); event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
}


export type ViewerFolderChoice = { id: string; name: string; stale?: boolean; renamedFrom?: string };
type StoredViewerFolder = { folder_id: string; folder_name?: string | null };
export function mergeViewerFolderOptions(
  options: Array<{ id: string; name: string }>,
  stored: StoredViewerFolder[],
): ViewerFolderChoice[] {
  const byId = new Map(stored.map(item => [item.folder_id, item.folder_name || ""]));
  const live = options.map(folder => {
    const previous = byId.get(folder.id);
    return { id: folder.id, name: folder.name, ...(previous && previous !== folder.name ? { renamedFrom: previous } : {}) };
  });
  const liveIds = new Set(options.map(folder => folder.id));
  const stale = stored.filter(item => !liveIds.has(item.folder_id)).map(item => ({
    id: item.folder_id,
    name: item.folder_name || "Deleted folder",
    stale: true,
  }));
  return [...live, ...stale];
}

function ViewerFolderScopeEditor({ tenantId, membershipId, onMessage }: { tenantId: string; membershipId: string; onMessage: (value: string) => void }) {
  const [open, setOpen] = useState(false);
  const [sourceId, setSourceId] = useState("");
  const [folders, setFolders] = useState<ViewerFolderChoice[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  async function openEditor() {
    setOpen(true); setLoading(true); setError("");
    try {
      const options = await fetchViewerFolderOptions();
      setSourceId(options.external_source_id);
      const current = await fetchViewerFolderScopes(tenantId, membershipId, options.external_source_id);
      setFolders(mergeViewerFolderOptions(options.folders, current.items));
      setSelected(new Set(current.items.map(item => item.folder_id)));
    } catch (next) { setError(next instanceof Error ? next.message : "Could not load folders."); } finally { setLoading(false); }
  }
  if (!open) return <button type="button" className="secondary" onClick={openEditor}>Limit viewer folders</button>;
  const selectedLive = folders.filter(folder => selected.has(folder.id) && !folder.stale);
  return <div className="viewer-folder-scope-backdrop">
    <div className="viewer-folder-scope-dialog" role="dialog" aria-modal="true" aria-labelledby="viewer-folder-title">
      <header><div><small>VIEWER ACCESS</small><h2 id="viewer-folder-title">Choose accessible folders</h2></div><button type="button" className="viewer-folder-close" aria-label="Close folder access dialog" onClick={() => setOpen(false)}>x</button></header>
      <p className="viewer-folder-help">Select one or more Drive folders. The viewer can see every file and nested folder inside the selected folders. Nothing outside them is visible.</p>
      {selectedLive.length ? <div className="viewer-folder-summary"><strong>{selectedLive.length} folder{selectedLive.length === 1 ? "" : "s"} selected</strong><span>{selectedLive.map(folder => folder.name).join(" - ")}</span></div> : <div className="viewer-folder-summary empty"><strong>No folders selected</strong><span>The viewer will not see Drive items until a folder is selected.</span></div>}
      {loading ? <div className="viewer-folder-loading" aria-busy="true">Loading current Drive folders...</div> : <fieldset className="viewer-folder-list"><legend>Available root folders</legend>{folders.length ? folders.map(folder => <label key={folder.id} className={`${folder.stale ? "stale" : ""}${selected.has(folder.id) ? " selected" : ""}`}><input type="checkbox" checked={selected.has(folder.id)} disabled={folder.stale} onChange={() => setSelected(current => { const next = new Set(current); next.has(folder.id) ? next.delete(folder.id) : next.add(folder.id); return next; })} /><span><strong>{folder.name}</strong>{folder.renamedFrom && <small>Renamed from "{folder.renamedFrom}"</small>}{folder.stale && <small className="folder-warning">No longer available - remove this access when saving</small>}</span></label>) : <p>No root folders found for the connected Google Drive.</p>}</fieldset>}
      <div className="viewer-folder-actions"><button type="button" className="viewer-folder-clear" disabled={!selected.size} onClick={() => setSelected(new Set())}>Clear selection</button><label><span className="viewer-folder-reason-label">Reason <b aria-hidden="true">Required</b></span><input value={reason} minLength={3} onChange={event => setReason(event.target.value)} placeholder="Explain this access change" /></label></div>
      {error && <p role="alert" className="viewer-folder-error">{error}</p>}
      <footer><button type="button" onClick={() => setOpen(false)}>Cancel</button><button type="button" className="primary" disabled={loading || !sourceId || reason.trim().length < 3} onClick={async () => { try { await replaceViewerFolderScopes(tenantId, membershipId, { external_source_id: sourceId, folders: selectedLive.map(folder => ({ folder_id: folder.id, folder_name: folder.name })), reason: reason.trim() }); onMessage("Viewer folder access updated."); setOpen(false); } catch (next) { setError(next instanceof Error ? next.message : "Could not save folder access."); } }}>Save folder access</button></footer>
    </div>
  </div>;
}