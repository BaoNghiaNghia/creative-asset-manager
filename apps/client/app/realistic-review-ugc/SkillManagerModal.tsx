import { useEffect, useMemo, useState } from "react";
import {
  createStage2Skill,
  createStage2SkillVersion,
  deleteStage2Skill,
  deleteStage2SkillVersion,
  listStage2SkillRegistry,
  setStage2SkillDefaultVersion,
  setStage2SkillEnabled,
  syncStage2SkillRegistry,
} from "./api";
import type { Stage2SkillRegistry, Stage2SkillRegistryItem } from "./types";

const EMPTY_REGISTRY: Stage2SkillRegistry = { can_manage: false, items: [] };

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

  async function reload(refresh = false) {
    setLoading(true);
    setError("");
    try {
      const next = await listStage2SkillRegistry(refresh);
      setRegistry(next);
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
      "Skill uploaded, installed and added to the Stage 2 registry.",
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

  async function hardDelete(item: Stage2SkillRegistryItem) {
    if (!window.confirm('Delete "' + item.display_name + '" from OpenAI Skills? Historical Stage 2 jobs will be kept.')) {
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
        <div>
          <small>STAGE 2 · SKILL REGISTRY</small>
          <h2 id="rrugc-skill-modal-title">Manage Skills</h2>
          <p>CAM-local skills can be uploaded and used immediately. OpenAI-hosted skills remain an optional sync source.</p>
        </div>
        <button type="button" className="rrugc-skill-modal-close" onClick={onClose} disabled={Boolean(busyKey)} aria-label="Close skill manager">×</button>
      </header>

      <div className="rrugc-skill-modal-toolbar">
        <div className="rrugc-skill-modal-summary">
          <strong>{registry.items.length} skills</strong>
          <span>{enabledCount} enabled</span>
          <span>{registry.can_manage ? "Admin controls" : "Read only"}</span>
        </div>
        <button type="button" onClick={() => void reload(true)} disabled={loading || Boolean(busyKey)}>
          {loading ? "Refreshing…" : "Refresh skills"}
        </button>
      </div>

      {registry.can_manage && <div className="rrugc-skill-upload">
        <div>
          <strong>Add Stage 2 skill</strong>
          <small>Upload a ZIP containing exactly one SKILL.md. CAM validates it and installs it directly into the Stage 2 runtime.</small>
        </div>
        <label className="rrugc-skill-file">
          <span>{createFile?.name || "Choose skill ZIP"}</span>
          <input
            type="file"
            accept=".zip,application/zip"
            disabled={Boolean(busyKey)}
            onChange={event => setCreateFile(event.target.files?.[0] || null)}
          />
        </label>
        <button type="button" className="rrugc-primary" disabled={!createFile || Boolean(busyKey)} onClick={() => void addSkill()}>
          {busyKey === "create" ? "Creating…" : "Add Skill"}
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
          <span>{registry.can_manage ? "Upload the first Stage 2 skill ZIP." : "Ask an admin to add or enable a Stage 2 skill."}</span>
        </div>}
        {registry.items.map(item => {
          const selected = selectedVersions[item.id] || defaultSelectedVersion(item);
          const selectedMeta = item.versions.find(version => version.version === selected);
          const state = statusLabel(item);
          const hosted = item.source === "openai";
          return <article className={"rrugc-skill-card " + (!item.enabled ? "is-disabled" : "")} key={item.id}>
            <div className="rrugc-skill-card-main">
              <div className="rrugc-skill-card-title">
                <div>
                  <strong>{item.display_name}</strong>
                  <small>{"$" + item.skill_name}</small>
                </div>
                <span className={"rrugc-skill-status is-" + state.toLowerCase().replaceAll(" ", "-")}>{state}</span>
              </div>
              <p>{item.description || "No description."}</p>
              <div className="rrugc-skill-meta">
                <span>{hosted ? "OpenAI" : "Local"}</span>
                <span>Default {item.default_version ? "v" + item.default_version : "—"}</span>
                <span>Synced {item.synced_version ? "v" + item.synced_version : "—"}</span>
                <span>Latest {item.latest_version ? "v" + item.latest_version : "—"}</span>
              </div>
              {item.last_error && <small className="rrugc-source-error">{item.last_error}</small>}
            </div>

            <div className="rrugc-skill-version-panel">
              <label>
                <small>Version</small>
                <select
                  value={selected}
                  disabled={!item.versions.length || Boolean(busyKey)}
                  onChange={event => setSelectedVersions(current => ({ ...current, [item.id]: event.target.value }))}
                >
                  {!item.versions.length && <option value="">No versions</option>}
                  {item.versions.map(version => <option key={version.id} value={version.version}>
                    {"v" + version.version}
                    {version.is_default ? " · default" : ""}
                    {version.is_synced ? " · synced" : ""}
                  </option>)}
                </select>
              </label>
              <div className="rrugc-skill-version-actions">
                {hosted && registry.can_manage && <button
                  type="button"
                  disabled={!selected || selectedMeta?.is_synced || Boolean(busyKey)}
                  onClick={() => void mutate(
                    "sync:" + item.id,
                    () => syncStage2SkillRegistry(item.id, selected),
                    "Skill runtime synced to v" + selected + ".",
                  )}
                >{busyKey === "sync:" + item.id ? "Syncing…" : selectedMeta?.is_synced ? "Synced" : "Sync runtime"}</button>}
                {hosted && registry.can_manage && <button
                  type="button"
                  disabled={!selected || selectedMeta?.is_default || Boolean(busyKey)}
                  onClick={() => void mutate(
                    "default:" + item.id,
                    () => setStage2SkillDefaultVersion(item.id, selected),
                    "Default version changed to v" + selected + ".",
                  )}
                >{selectedMeta?.is_default ? "Default" : "Set default"}</button>}
                {hosted && registry.can_manage && <button
                  type="button"
                  className="is-danger"
                  disabled={!selected || selectedMeta?.is_default || selectedMeta?.is_synced || Boolean(busyKey)}
                  onClick={() => void mutate(
                    "delete-version:" + item.id,
                    () => deleteStage2SkillVersion(item.id, selected),
                    "Version v" + selected + " deleted.",
                  )}
                >Delete version</button>}
              </div>
            </div>

            {registry.can_manage && <div className="rrugc-skill-admin-row">
              {hosted && <div className="rrugc-skill-version-upload">
                <label className="rrugc-skill-file is-compact">
                  <span>{versionFiles[item.id]?.name || "New version ZIP"}</span>
                  <input
                    type="file"
                    accept=".zip,application/zip"
                    disabled={Boolean(busyKey)}
                    onChange={event => setVersionFiles(current => ({
                      ...current,
                      [item.id]: event.target.files?.[0] || null,
                    }))}
                  />
                </label>
                <label className="rrugc-skill-default-check">
                  <input
                    type="checkbox"
                    checked={Boolean(makeDefault[item.id])}
                    disabled={Boolean(busyKey)}
                    onChange={event => setMakeDefault(current => ({ ...current, [item.id]: event.target.checked }))}
                  />
                  Make default
                </label>
                <button type="button" disabled={!versionFiles[item.id] || Boolean(busyKey)} onClick={() => void addVersion(item)}>
                  {busyKey === "version:" + item.id ? "Uploading…" : "Add version"}
                </button>
              </div>}
              <div className="rrugc-skill-admin-actions">
                <button
                  type="button"
                  disabled={Boolean(busyKey)}
                  onClick={() => void mutate(
                    "enabled:" + item.id,
                    () => setStage2SkillEnabled(item.id, !item.enabled),
                    item.enabled ? "Skill disabled for new jobs." : "Skill enabled.",
                  )}
                >{item.enabled ? "Disable" : "Enable"}</button>
                {hosted && <button type="button" className="is-danger" disabled={Boolean(busyKey)} onClick={() => void hardDelete(item)}>
                  Delete skill
                </button>}
              </div>
            </div>}
          </article>;
        })}
      </div>
    </section>
  </div>;
}
