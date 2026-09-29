# Windows Native UI Redesign

## Goal

Make the Creative Asset Manager Windows app feel like a purpose-built desktop product instead of a web page inside an Electron window. The redesign keeps the existing product structure and workflows, but improves visual hierarchy, density, cloud-source onboarding, and native-window polish.

## UX specification

### Native shell

- Remove the Electron application menu bar (`File`, `Edit`, `View`, `Window`, `Help`) from packaged Windows builds.
- Keep the normal Windows title bar and system window controls for reliability and accessibility.
- Use a neutral window background so launch/reload does not flash pure white.
- Keep the current secure BrowserWindow settings, navigation restrictions, OAuth handoff, native drag-out, and ingestion IPC unchanged.

### Sidebar

- Default width: 272 px for new installs; user-resized width remains persisted.
- Product identity becomes `Creative Asset Manager`, with a compact workspace subtitle.
- Use a white surface with a subtle right border and lower visual noise.
- Workspace navigation uses 10–12 px radii, compact 42 px rows, restrained hover, and a stronger active state.
- `Sources` is treated as a utility group rather than a second navigation system.
- Disconnected providers use solid cards instead of dashed boxes.
- Connected sources retain tree browsing and context-menu actions.
- Tags remain low-priority and visually separated from source/account controls.

### Asset Explorer page header

- Keep the shared WorkspacePageHeader contract so all top-level pages remain consistent.
- On desktop, use a compact 96 px header rather than an oversized hero.
- Search/filter controls sit on a distinct white toolbar surface below the page title.
- Search field gets a stronger focus state and cleaner rounded geometry.
- Account/source status remains on the right side of the toolbar.
- Existing folder breadcrumb and `New` menu continue to appear only when a source is ready.

### Empty / onboarding state

- Replace the sparse two-column source layout with a centered workspace onboarding panel.
- Heading: `Connect your creative library`.
- Show Google Drive, OneDrive, and SharePoint in one row on wide screens, two columns on medium screens, and one column on narrow screens.
- Use provider brand artwork where available.
- Each provider card has provider icon, provider name, one short benefit statement, one primary action, and optional connected/switch-account status.
- Authentication semantics do not change: application sign-in happens before source connection.

### Visual system

- App background: soft cool gray.
- Primary surfaces: white.
- Border: low-contrast cool gray.
- Primary accent: existing CAM blue.
- Radius scale: 10 px controls, 14 px cards, 18–22 px onboarding container.
- Shadows only on elevated/interactive surfaces.
- Avoid dashed borders for primary actions.
- Preserve visible keyboard focus states.
- Respect current responsive behavior.

## Implementation checklist

### Electron

- [x] Hide/remove the native application menu.
- [x] Keep native Windows frame and controls.
- [x] Set a neutral BrowserWindow background.
- [ ] Future optional phase: custom frameless title bar only if native system controls are reproduced with full keyboard/accessibility support.

### React shell

- [x] Add a desktop-only shell class so native polish does not unintentionally restyle the web app.
- [x] Keep existing WorkspaceNavigation and source-tree behavior.
- [x] Refresh product naming in the native sidebar.
- [x] Make Windows drag-over detection use the desktop-aware file-drag classifier.

### Asset Explorer

- [x] Restyle sidebar/navigation for desktop.
- [x] Restyle page header and search toolbar for desktop.
- [x] Redesign disconnected-source rows.
- [x] Redesign onboarding/source cards.
- [x] Use provider artwork for Google Drive and OneDrive.
- [ ] Optional later phase: add user-controlled grid/list toggle once list layout has a complete interaction contract.

### Quality gates

- [x] Client tests.
- [x] Client typecheck.
- [x] Desktop tests.
- [x] Desktop typecheck/build.
- [ ] Production frontend build.
- [ ] Windows installer rebuild before distributing the menu-bar change.
