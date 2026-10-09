import { useEffect, useMemo, useState } from "react";
import {
  createStage2Skill,
  createStage2SkillVersion,
  listStage2Skills,
  restoreArchivedStage1Skill,
  uploadLocalKeywordSkillForStage1,
  deleteStage2Skill,
  deleteStage2SkillVersion,
  listStage2SkillRegistry,
  setStage2SkillDefaultVersion,
  setStage2SkillEnabled,
  updateStage2SkillNote,
  updateStageSkillDefault,
  syncStage2SkillRegistry,
} from "./api";
import type { Stage2SkillRegistry, Stage2SkillRegistryItem } from "./types";

const EMPTY_REGISTRY: Stage2SkillRegistry = { can_manage: false, items: [] };

type SkillIconName = "layers" | "refresh" | "upload" | "search" | "chevron" | "settings" | "check" | "power" | "trash" | "cloud" | "local" | "note" | "plus" | "close" | "sparkles" | "image" | "users" | "alert";

function SkillIcon({ name, size = 16 }: { name: SkillIconName; size?: number }) {
  const paths: Record<SkillIconName, React.ReactNode> = {
    layers: <><rect x="3" y="4" width="18" height="16" rx="3" /><path d="M7 9h10M7 13h7" /></>,
    refresh: <><path d="M20 8a8 8 0 0 0-13.7-2L4 8M4 4v4h4M4 16a8 8 0 0 0 13.7 2L20 16m-4 0h4v4" /></>,
    upload: <><path d="M12 16V4m-4 4 4-4 4 4M4 16v4h16v-4" /></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>,
    chevron: <path d="m6 9 6 6 6-6" />,
    settings: <><path d="M4 7h16M4 17h16" /><circle cx="9" cy="7" r="2" fill="white" /><circle cx="15" cy="17" r="2" fill="white" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    power: <><path d="M12 3v9M6 6.5a9 9 0 1 0 12 0" /></>,
    trash: <><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5" /></>,
    cloud: <><path d="M6 18h12a4 4 0 0 0 .4-8 6 6 0 0 0-11.6-1.4A4.7 4.7 0 0 0 6 18Z" /></>,
    local: <><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M8 9h8M8 13h5" /></>,
    note: <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h5" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    close: <path d="M6 6l12 12M18 6 6 18" />,
    sparkles: <><path d="m12 2 2.2 7.8L22 12l-7.8 2.2L12 22l-2.2-7.8L2 12l7.8-2.2L12 2Z" /></>,
    image: <><rect x="3" y="4" width="18" height="16" rx="3" /><circle cx="8" cy="9" r="1.3" /><path d="m4 18 6-6 4 4 3-3 4 4" /></>,
    users: <><circle cx="12" cy="8" r="3" /><path d="M5 20v-2a7 7 0 0 1 14 0v2" /></>,
    alert: <><path d="M12 3 2 21h20L12 3Z" /><path d="M12 9v5m0 3v.5" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function statusLabel(item: Stage2SkillRegistryItem) {
  if (!item.enabled) return "Disabled";
  if (item.validation_status === "missing") return "Missing";
  if (item.validation_status === "conflict") return "Conflict";
  if (item.sync_state === "ready") return "Ready";
  if (item.sync_state === "update_available") return "Update available";
  if (item.source === "local") return "Ready";
  return "Not synced";
}

function defaultSelectedVersion(item: Stage2SkillRegistryItem) {
  return item.synced_version || item.default_version || item.latest_version || item.versions[0]?.version || "";
}

export function SkillManagerModal({
  open,
  onClose,
  onChanged,
}: {
  open: boolean;
  onClose: () => void;
  onChanged: () => void | Promise<void>;
}) {
  const [registry, setRegistry] = useState<Stage2SkillRegistry>(EMPTY_REGISTRY);
  const [loading, setLoading] = useState(false);
  const [busyKey, setBusyKey] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [createFile, setCreateFile] = useState<File | null>(null);
  const [versionFiles, setVersionFiles] = useState<Record<string, File | null>>({});
  const [selectedVersions, setSelectedVersions] = useState<Record<string, string>>({});
  const [makeDefault, setMakeDefault] = useState<Record<string, boolean>>({});
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [skillSearch, setSkillSearch] = useState("");
  const [showUpload, setShowUpload] = useState(false);
  const [stage1CompatibleKeys, setStage1CompatibleKeys] = useState<Set<string>>(new Set());
  const [stage1CatalogError, setStage1CatalogError] = useState(false);
  const [stage1CatalogChecked, setStage1CatalogChecked] = useState(false);

  async function reload(refresh = false) {
    setLoading(true);
    setStage1CatalogChecked(false);
    setStage1CatalogError(false);
    setError("");
    try {
      const next = await listStage2SkillRegistry(refresh);
      setRegistry(next);
      // Only show installed, enabled keyword-only workflows in the Stage 1 default picker.
      // A catalog error must not hide the shared registry and its admin controls.
      try {
        const catalog = await listStage2Skills(false);
        setStage1CompatibleKeys(new Set(catalog.items.filter(item => item.keyword_artwork_ready && item.ready)
          .map(item => [item.source, item.skill_id || item.skill_name].join(":"))));
        setStage1CatalogError(false);
      } catch {
        setStage1CompatibleKeys(new Set());
        setStage1CatalogError(true);
      } finally {
        setStage1CatalogChecked(true);
      }
      setNoteDrafts(current => {
        const updated = { ...current };
        for (const item of next.items) if (!(item.id in updated)) updated[item.id] = item.note || "";
        return updated;
      });
      setSelectedVersions(current => {
        const updated = { ...current };
        for (const item of next.items) {
          if (!updated[item.id] || !item.versions.some(version => version.version === updated[item.id])) {
            updated[item.id] = defaultSelectedVersion(item);
          }
        }
        return updated;
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load skills.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!open) return;
    void reload(false);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !busyKey) onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, busyKey, onClose]);

  const enabledCount = useMemo(
    () => registry.items.filter(item => item.enabled).length,
    [registry.items],
  );
  const archivedKeywordSkill = (registry.archived_items || []).find(item => item.skill_name === "gatorhats-keyword-embroidery");
  const disabledKeywordSkill = registry.items.find(item => item.skill_name === "gatorhats-keyword-embroidery" && !item.enabled);
  const filteredSkills = useMemo(() => registry.items.filter(item =>
    [item.display_name, item.skill_name, item.description, item.note].some(value =>
      (value || "").toLowerCase().includes(skillSearch.trim().toLowerCase()),
    ),
  ), [registry.items, skillSearch]);
  const toggleExpanded = (id: string) => setExpandedIds(current => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  async function mutate(key: string, action: () => Promise<unknown>, success: string) {
    if (busyKey) return;
    setBusyKey(key);
    setError("");
    setMessage("");
    try {
      await action();
      await reload(true);
      await onChanged();
      setMessage(success);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Skill operation failed.");
    } finally {
      setBusyKey("");
    }
  }

  async function addSkill() {
    if (!createFile) {
      setError("Choose a skill ZIP first.");
      return;
    }
    await mutate(
      "create",
      () => createStage2Skill(createFile),
      "Skill uploaded, installed and added to the image-generation registry.",
    );
    setCreateFile(null);
  }

  async function addVersion(item: Stage2SkillRegistryItem) {
    const file = versionFiles[item.id];
    if (!file) {
      setError("Choose a version ZIP first.");
      return;
    }
    await mutate(
      "version:" + item.id,
      () => createStage2SkillVersion(item.id, file, Boolean(makeDefault[item.id])),
      "New skill version created.",
    );
    setVersionFiles(current => ({ ...current, [item.id]: null }));
  }

  async function uploadKeywordSkill(item: Stage2SkillRegistryItem) {
    const file = versionFiles[item.id];
    if (!file) {
      setError("Choose the Stage 1-ready Skill ZIP first.");
      return;
    }
    await mutate(
      "keyword-upload:" + item.id,
      () => uploadLocalKeywordSkillForStage1(item.id, file),
      "Redesign Skill updated and assigned to Stage 1. Existing outputs are preserved.",
    );
    setVersionFiles(current => ({ ...current, [item.id]: null }));
  }

  async function hardDelete(item: Stage2SkillRegistryItem) {
    if (!window.confirm('Remove "' + item.display_name + '" from the shared registry? Existing outputs are preserved. Local bundled runtime files are retained. Active jobs block removal.')) {
      return;
    }
    await mutate(
      "delete:" + item.id,
      () => deleteStage2Skill(item.id),
      "Skill deleted.",
    );
  }

  if (!open) return null;

  return <div className="rrugc-skill-modal-backdrop" role="presentation" onMouseDown={event => {
    if (event.target === event.currentTarget && !busyKey) onClose();
  }}>
    <section
      className="rrugc-skill-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rrugc-skill-modal-title"
    >
      <header className="rrugc-skill-modal-header">
        <div className="rrugc-skill-heading">
          <span className="rrugc-skill-heading-icon"><SkillIcon name="layers" size={20} /></span>
          <div>
            <small>WORKFLOW / SHARED SKILLS</small>
            <h2 id="rrugc-skill-modal-title">Manage Skills</h2>
            <p>Choose a Skill for each stage, or manage installed versions and notes.</p>
          </div>
        </div>
        <button type="button" className="rrugc-skill-modal-close" onClick={onClose} disabled={Boolean(busyKey)} aria-label="Close skill manager"><SkillIcon name="close" size={17} /></button>
      </header>

      <div className="rrugc-skill-modal-toolbar">
        <div className="rrugc-skill-modal-summary">
          <span className="rrugc-skill-summary-total"><SkillIcon name="layers" size={13} />{registry.items.length} skills</span>
          <span className="rrugc-skill-summary-active"><SkillIcon name="check" size={13} />{enabledCount} enabled</span>
          <span className="rrugc-skill-summary-role">{registry.can_manage ? "Admin" : "Read only"}</span>
        </div>
        <button type="button" onClick={() => void reload(true)} disabled={loading || Boolean(busyKey)} className="rrugc-skill-refresh"><SkillIcon name="refresh" size={14} />{loading ? "Refreshing…" : "Refresh"}</button>
      </div>

      {registry.can_manage && <div className="rrugc-stage-defaults" aria-label="Default skill per Stage">
        <div className="rrugc-stage-default-heading">
          <div><strong>Default Skills by stage</strong><small>Independent settings · Affect new jobs only</small></div>
        </div>
        <div className="rrugc-stage-default-grid">
          {([ ["stage1", "Stage 1", "Keywords", "sparkles"], ["stage2", "Stage 2", "13 Colors", "image"], ["stage4", "Stage 4", "UGC Images", "users"] ] as const).map(([stage, number, label, icon]) => <label key={stage}>
            <span className="rrugc-stage-default-label"><span className={"rrugc-stage-default-icon is-" + stage}><SkillIcon name={icon} size={15} /></span><span><strong>{number}</strong><small>{label}</small></span></span>
            <select
              aria-label={number + " default skill"}
              value={registry.items.find(item => [item.source, item.skill_id || item.skill_name].join(":") === registry.stage_defaults?.[stage])?.id || ""}
              disabled={Boolean(busyKey) || loading}
              onChange={event => void mutate("stage:" + stage, () => updateStageSkillDefault(stage, event.target.value || null), "Default skill saved for " + number + ".")}
            >
              <option value="">Automatic · No override</option>
              {registry.items.filter(item => item.enabled && item.validation_status === "valid" && (stage !== "stage1" || stage1CompatibleKeys.has([item.source, item.skill_id || item.skill_name].join(":")))).map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}
            </select>
          </label>)}
        </div>

        {stage1CatalogChecked && stage1CatalogError && <p className="rrugc-stage-default-guidance" role="status"><SkillIcon name="alert" size={14} /> Could not check Stage 1 Skill compatibility. Refresh skills to retry.</p>}
        {stage1CatalogChecked && !stage1CatalogError && stage1CompatibleKeys.size === 0 && <div className="rrugc-stage1-skill-recovery" role="status">
          <span className="rrugc-stage1-skill-recovery-icon"><SkillIcon name="alert" size={15} /></span>
          <div>
            <strong>{archivedKeywordSkill ? "Stage 1 Skill is archived" : "Stage 1 requires a keyword-only Skill"}</strong>
            <small>{archivedKeywordSkill
              ? archivedKeywordSkill.display_name + " was archived. Restore it to generate images from Stage 0 keywords."
              : disabledKeywordSkill ? "The compatible Skill is disabled. Enable it to make Stage 1 ready."
              : "Install an enabled Skill that supports keyword_artwork without reference images."}</small>
          </div>
          {archivedKeywordSkill && <button type="button" className="rrugc-stage1-skill-restore" disabled={Boolean(busyKey) || loading}
            onClick={() => void mutate("restore:stage1",
              () => restoreArchivedStage1Skill(archivedKeywordSkill.id),
              "Skill restored and set as the default for Stage 1.")}>
            <SkillIcon name="refresh" size={14} />
            {busyKey === "restore:stage1" ? "Restoring…" : "Restore & set Stage 1"}
          </button>}
          {!archivedKeywordSkill && disabledKeywordSkill && <button type="button" className="rrugc-stage1-skill-restore" disabled={Boolean(busyKey) || loading}
            onClick={() => void mutate("enabled:" + disabledKeywordSkill.id,
              () => setStage2SkillEnabled(disabledKeywordSkill.id, true),
              "Skill enabled. You can now choose it as Stage 1 default.")}>
            <SkillIcon name="power" size={14} />Enable Skill
          </button>}
        </div>}
      </div>}

      <div className="rrugc-skill-catalog-toolbar">
        <div className="rrugc-skill-catalog-title"><strong>Installed Skills</strong><small>{filteredSkills.length} of {registry.items.length}</small></div>
        <label className="rrugc-skill-search">
          <SkillIcon name="search" size={16} />
          <input type="search" placeholder="Search skills…" value={skillSearch} onChange={event => setSkillSearch(event.target.value)} aria-label="Search installed skills" />
        </label>
        {registry.can_manage && <button type="button" className={"rrugc-skill-add-toggle" + (showUpload ? " is-open" : "")} onClick={() => setShowUpload(current => !current)} aria-expanded={showUpload}>
          <SkillIcon name={showUpload ? "chevron" : "plus"} size={15} />{showUpload ? "Hide upload" : "Add Skill"}
        </button>}
      </div>

      {registry.can_manage && showUpload && <div className="rrugc-skill-upload">
        <div><strong><SkillIcon name="upload" size={16} /> Upload Skill ZIP</strong><small>One SKILL.md per ZIP. Validated before installation.</small></div>
        <label className="rrugc-skill-file">
          <SkillIcon name="upload" size={15} /><span>{createFile?.name || "Choose ZIP file"}</span>
          <input type="file" accept=".zip,application/zip" disabled={Boolean(busyKey)} onChange={event => setCreateFile(event.target.files?.[0] || null)} />
        </label>
        <button type="button" className="rrugc-primary" disabled={!createFile || Boolean(busyKey)} onClick={() => void addSkill()}>
          {busyKey === "create" ? "Uploading…" : "Upload & install"}
        </button>
      </div>}

      {message && <p className="rrugc-editor-product-result" role="status">{message}</p>}
      {error && <p className="rrugc-source-error" role="alert">{error}</p>}

      <div className="rrugc-skill-list" aria-busy={loading}>
        {loading && registry.items.length === 0 && <div className="rrugc-skill-loading">
          <span /><span /><span />
        </div>}
        {!loading && registry.items.length === 0 && <div className="rrugc-skill-empty">
          <strong>No skills found</strong>
          <span>{registry.can_manage ? "Upload the first image-generation skill ZIP." : "Ask an admin to add or enable an image-generation skill."}</span>
        </div>}
        {!loading && registry.items.length > 0 && filteredSkills.length === 0 && <div className="rrugc-skill-empty"><strong>No matching Skills</strong><span>Try a different search.</span></div>}
        {filteredSkills.map(item => {
          const selected = selectedVersions[item.id] || defaultSelectedVersion(item);
          const selectedMeta = item.versions.find(version => version.version === selected);
          const state = statusLabel(item);
          const hosted = item.source === "openai";
          const expanded = expandedIds.has(item.id);
          const noteValue = noteDrafts[item.id] ?? item.note ?? "";
          const stages = ([
            ["stage1", "Stage 1"],
            ["stage2", "Stage 2"],
            ["stage4", "Stage 4"],
          ] as const).filter(([stage]) => registry.stage_defaults?.[stage] === [item.source, item.skill_id || item.skill_name].join(":"));
          return <article className={"rrugc-skill-card" + (item.enabled ? "" : " is-disabled") + (expanded ? " is-expanded" : "")} key={item.id}>
            <div className="rrugc-skill-compact-row">
              <span className={"rrugc-skill-type-icon" + (item.enabled ? "" : " is-muted")}><SkillIcon name={hosted ? "cloud" : "local"} size={19} /></span>
              <div className="rrugc-skill-compact-info">
                <div className="rrugc-skill-compact-title">
                  <strong title={item.display_name}>{item.display_name}</strong>
                  <span className={"rrugc-skill-status is-" + state.toLowerCase().replaceAll(" ", "-")}><span className="rrugc-skill-status-dot" />{state}</span>
                </div>
                <div className="rrugc-skill-compact-subtitle"><span title={item.skill_name}>{item.skill_name}</span><span className="rrugc-skill-compact-divider">·</span><span>{hosted ? "OpenAI" : "Local"}</span><span className="rrugc-skill-compact-divider">·</span><span>{item.synced_version ? "v" + item.synced_version : item.versions.length ? item.versions.length + " versions" : "Bundled"}</span></div>
                {stages.length > 0 && <div className="rrugc-skill-stage-chips">{stages.map(([,label]) => <span key={label}><SkillIcon name="check" size={11} />{label} default</span>)}</div>}
                {item.last_error && <small className="rrugc-skill-inline-error"><SkillIcon name="alert" size={12} />{item.last_error}</small>}
              </div>
              <div className="rrugc-skill-compact-actions">
                {registry.can_manage && <button type="button" className={"rrugc-skill-toggle" + (item.enabled ? "" : " is-enable")} disabled={Boolean(busyKey)} onClick={() => void mutate(
                  "enabled:" + item.id,
                  () => setStage2SkillEnabled(item.id, !item.enabled),
                  item.enabled ? "Skill disabled for new jobs." : "Skill enabled.",
                )}><SkillIcon name="power" size={14} />{item.enabled ? "Disable" : "Enable"}</button>}
                <button type="button" className="rrugc-skill-details-toggle" aria-expanded={expanded} aria-controls={"rrugc-skill-details-" + item.id} onClick={() => toggleExpanded(item.id)}>
                  <SkillIcon name="settings" size={15} />{expanded ? "Hide" : "Details"}<span className={expanded ? "is-rotated" : ""}><SkillIcon name="chevron" size={14} /></span>
                </button>
              </div>
            </div>
            {!expanded && (item.note || item.description) && <p className="rrugc-skill-compact-description" title={item.note || item.description}>{item.note || item.description}</p>}
            {expanded && <div className="rrugc-skill-details" id={"rrugc-skill-details-" + item.id}>
              <div className="rrugc-skill-details-left">
                <div className="rrugc-skill-details-section-title"><SkillIcon name="note" size={15} /><strong>Purpose & notes</strong></div>
                <p className="rrugc-skill-full-description">{item.description || "No description provided."}</p>
                <label className="rrugc-skill-note">
                  <span>Internal usage note</span>
                  <textarea rows={3} maxLength={1500} value={noteValue} readOnly={!registry.can_manage} disabled={Boolean(busyKey)}
                    onChange={event => setNoteDrafts(current => ({ ...current, [item.id]: event.target.value }))}
                    placeholder="Describe use cases, preferred Stage and expected output…" />
                </label>
                {registry.can_manage && <button type="button" className="rrugc-skill-save-note" disabled={Boolean(busyKey) || noteValue === (item.note || "")}
                  onClick={() => void mutate("note:" + item.id, () => updateStage2SkillNote(item.id, noteValue), "Skill note saved.")}>
                  <SkillIcon name="check" size={14} />{busyKey === "note:" + item.id ? "Saving…" : "Save note"}
                </button>}
              </div>
              <div className="rrugc-skill-details-right">
                <div className="rrugc-skill-details-section-title"><SkillIcon name="layers" size={15} /><strong>Versions & runtime</strong></div>
                <div className="rrugc-skill-meta">
                  <span>Default: {item.default_version ? "v" + item.default_version : "—"}</span>
                  <span>Synced: {item.synced_version ? "v" + item.synced_version : "—"}</span>
                  <span>Latest: {item.latest_version ? "v" + item.latest_version : "—"}</span>
                </div>
                <div className="rrugc-skill-version-panel">
                  <label>
                    <small>Available versions</small>
                    <select value={selected} disabled={!item.versions.length || Boolean(busyKey)}
                      onChange={event => setSelectedVersions(current => ({ ...current, [item.id]: event.target.value }))}>
                      {!item.versions.length && <option value="">Bundled Skill · No managed versions</option>}
                      {item.versions.map(version => <option key={version.id} value={version.version}>
                        {"v" + version.version}{version.is_default ? " · default" : ""}{version.is_synced ? " · synced" : ""}
                      </option>)}
                    </select>
                  </label>
                  {hosted && registry.can_manage && <div className="rrugc-skill-version-actions">
                    <button type="button" disabled={!selected || selectedMeta?.is_synced || Boolean(busyKey)}
                      onClick={() => void mutate("sync:" + item.id, () => syncStage2SkillRegistry(item.id, selected), "Skill runtime synced to v" + selected + ".")}>
                      <SkillIcon name="refresh" size={13} />{busyKey === "sync:" + item.id ? "Syncing…" : selectedMeta?.is_synced ? "Synced" : "Sync runtime"}
                    </button>
                    <button type="button" disabled={!selected || selectedMeta?.is_default || Boolean(busyKey)}
                      onClick={() => void mutate("default:" + item.id, () => setStage2SkillDefaultVersion(item.id, selected), "Default version changed to v" + selected + ".")}>
                      <SkillIcon name="check" size={13} />{selectedMeta?.is_default ? "Default" : "Set default"}
                    </button>
                    <button type="button" className="is-danger" disabled={!selected || selectedMeta?.is_default || selectedMeta?.is_synced || Boolean(busyKey)}
                      onClick={() => void mutate("delete-version:" + item.id, () => deleteStage2SkillVersion(item.id, selected), "Version v" + selected + " deleted.")}>
                      <SkillIcon name="trash" size={13} />Delete version
                    </button>
                  </div>}
                </div>
                {item.source === "local" && item.skill_name === "redesign-8869-v3" && registry.can_manage && <div className="rrugc-skill-version-upload rrugc-stage1-skill-update">
                  <div className="rrugc-stage1-skill-update-intro"><strong>Stage 1 · Keyword concepts</strong><small>Replace this local Skill with the Stage 1-ready ZIP. Creates a 10-concept board + 1 matching 8869 hero; keeps old outputs and Stage 2/4 defaults.</small></div>
                  <label className="rrugc-skill-file is-compact"><SkillIcon name="upload" size={13} /><span>{versionFiles[item.id]?.name || "Choose Stage 1-ready ZIP"}</span>
                    <input type="file" accept=".zip,application/zip" disabled={Boolean(busyKey)}
                      onChange={event => setVersionFiles(current => ({ ...current, [item.id]: event.target.files?.[0] || null }))} />
                  </label>
                  <button type="button" disabled={!versionFiles[item.id] || Boolean(busyKey)}
                    onClick={() => void uploadKeywordSkill(item)}>
                    <SkillIcon name="sparkles" size={13} />
                    {busyKey === "keyword-upload:" + item.id ? "Installing…" : "Upload & set Stage 1"}
                  </button>
                </div>}
                {hosted && registry.can_manage && <div className="rrugc-skill-version-upload">
                  <label className="rrugc-skill-file is-compact"><SkillIcon name="upload" size={13} /><span>{versionFiles[item.id]?.name || "New version ZIP"}</span>
                    <input type="file" accept=".zip,application/zip" disabled={Boolean(busyKey)}
                      onChange={event => setVersionFiles(current => ({ ...current, [item.id]: event.target.files?.[0] || null }))} />
                  </label>
                  <label className="rrugc-skill-default-check"><input type="checkbox" checked={Boolean(makeDefault[item.id])} disabled={Boolean(busyKey)}
                    onChange={event => setMakeDefault(current => ({ ...current, [item.id]: event.target.checked }))} />Make default</label>
                  <button type="button" disabled={!versionFiles[item.id] || Boolean(busyKey)} onClick={() => void addVersion(item)}>
                    {busyKey === "version:" + item.id ? "Uploading…" : "Add version"}
                  </button>
                </div>}
              </div>
              {registry.can_manage && <div className="rrugc-skill-danger-zone">
                <span>Removing a Skill keeps saved outputs; active jobs block deletion.</span>
                <button type="button" className="is-danger" disabled={Boolean(busyKey)} onClick={() => void hardDelete(item)}>
                  <SkillIcon name="trash" size={13} />Delete Skill
                </button>
              </div>}
            </div>}
          </article>;
        })}
      </div>
    </section>
  </div>;
}