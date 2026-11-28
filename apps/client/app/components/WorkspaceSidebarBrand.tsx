/** The same CAM sidebar identity is used by the Explorer and all workspace routes. */
export function WorkspaceSidebarBrand() {
  return <a className="inventory-sidebar-brand workspace-sidebar-brand" href="/">
    <img src="/app-icon-192.png" alt="" aria-hidden="true" />
    <span>Creative assets <small>Operations console</small></span>
  </a>;
}
