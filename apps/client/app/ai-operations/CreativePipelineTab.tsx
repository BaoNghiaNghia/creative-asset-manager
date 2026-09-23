import { useEffect, useMemo, useRef, useState } from "react";

type Node = {
  id: string | null;
  node_type: string;
  inherited: boolean;
  status: string;
  executor_type?: "gpt_skill" | "system";
};

type Artifact = {
  id: string;
  artifact_type: string;
  status: string;
  version: number;
  aspect_ratio: string | null;
};

type Generation = {
  id: string;
  provider: string;
  model: string;
  aspect_ratio: string;
  generation_number: number;
  status: string;
};

type SkillExecution = {
  id: string;
  node_run_id: string;
  skill_key: string;
  skill_version: number;
  binding_scope: string;
  variant_key: string;
  attempt_number: number;
  status: string;
  provider: string;
  model: string | null;
  usage: Record<string, unknown>;
  details: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
};

type SkillBindingSummary = {
  node_type: string;
  executor_type: "gpt_skill";
  skill_id: string;
  skill_version_id: string;
  skill_key: string;
  skill_name: string;
  skill_version: number;
  provider: string;
  preferred_model: string | null;
  knowledge_refs: string[];
  binding_scope: string;
  binding_id: string | null;
};

type SkillVersion = {
  id: string;
  version: number;
  status: "draft" | "published" | "archived";
  instructions?: string;
  input_schema?: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
  knowledge_refs: string[];
  provider: string;
  preferred_model: string | null;
  owner_tenant_id: string | null;
  created_by: string | null;
  created_at: string;
  published_at: string | null;
};

type Skill = {
  id: string;
  key: string;
  name: string;
  description: string | null;
  node_type: string;
  executor_type: "gpt_skill";
  owner_tenant_id: string | null;
  active: boolean;
  latest_published_version: number | null;
  effective_tenant_version_id: string | null;
  effective_tenant_scope: "tenant" | "system_default" | null;
  versions: SkillVersion[];
};

type Run = {
  id: string;
  run_number: number;
  status: string;
  nodes: Node[];
  artifacts: Artifact[];
  generations: Generation[];
};

type Listing = {
  source_group_id: string;
  id: string;
  listing_key: string;
  folder_name: string;
  platform: string;
  source_status: string;
  pipeline_status: string | null;
  current_run: Run | null;
};

type Group = {
  id: string;
  name: string;
  platform: string;
  listing_count: number;
  active_listing_count: number;
  current_runs: Record<string, number>;
};

const labels: Record<string, string> = {
  input_data: "Input",
  idea_story: "Idea",
  prompt: "Prompt",
  video_generation: "Generate",
  video_output: "Output",
  watermark_smart_enhance: "Enhance",
};

const summaryStatuses = ["running", "queued", "failed", "completed"] as const;

const status = (value: string | null | undefined) =>
  ({
    queued: "Queued",
    running: "Running",
    retrying: "Retrying",
    blocked: "Blocked",
    failed: "Failed",
    completed: "Completed",
    cancelled: "Cancelled",
    pending: "Pending",
    ready: "Ready",
    retry_wait: "Waiting",
    draft: "Draft",
    published: "Published",
    archived: "Archived",
  })[value || ""] || "Not started";

const request = async <T,>(url: string, init?: RequestInit) => {
  const response = await fetch(url, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as
      | { detail?: string | { message?: string; code?: string } }
      | null;
    const message =
      typeof body?.detail === "string"
        ? body.detail
        : body?.detail?.message || "Request failed.";
    throw new Error(message);
  }

  return response.json() as Promise<T>;
};

const idempotency = () => crypto.randomUUID();

const scopeLabel = (scope: string) =>
  ({
    system_default: "System default",
    tenant: "Tenant default",
    source_group: "Group override",
    listing: "Listing override",
  })[scope] || scope;

const usageSummary = (usage: Record<string, unknown>) => {
  const entries = Object.entries(usage || {})
    .filter(([, value]) => typeof value === "number" || typeof value === "string")
    .slice(0, 3);
  return entries.length
    ? entries.map(([key, value]) => `${key.replaceAll("_", " ")}: ${value}`).join(" · ")
    : "No usage metrics";
};

export function CreativePipelineTab({ canManage }: { canManage: boolean }) {
  const [groups, setGroups] = useState<Group[]>([]);
  const [listings, setListings] = useState<Listing[]>([]);
  const [selected, setSelected] = useState<Listing | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [mutationBusy, setMutationBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [skillManagerOpen, setSkillManagerOpen] = useState(false);
  const mutationInFlight = useRef(false);

  const load = async (blocking = false) => {
    if (blocking) {
      setLoading(true);
    } else {
      setRefreshing(true);
    }
    setError(null);

    try {
      const [groupResponse, listingResponse] = await Promise.all([
        request<{ items: Group[] }>("/api/v1/creative-pipeline/groups"),
        request<{ items: Listing[] }>(
          "/api/v1/creative-pipeline/listings?limit=200",
        ),
      ]);
      setGroups(groupResponse.items);
      setListings(listingResponse.items);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load.");
    } finally {
      if (blocking) {
        setLoading(false);
      } else {
        setRefreshing(false);
      }
    }
  };

  useEffect(() => {
    void load(true);
  }, []);

  useEffect(() => {
    setSelected((current) =>
      current
        ? listings.find((listing) => listing.id === current.id) || null
        : null,
    );
  }, [listings]);

  const visible = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();

    return listings.filter(
      (listing) =>
        (!normalizedQuery ||
          (listing.folder_name + listing.listing_key)
            .toLowerCase()
            .includes(normalizedQuery)) &&
        (!filter || listing.pipeline_status === filter),
    );
  }, [listings, query, filter]);

  const visibleByGroup = useMemo(() => {
    const grouped = new Map<string, Listing[]>();
    for (const listing of visible) {
      const rows = grouped.get(listing.source_group_id);
      if (rows) {
        rows.push(listing);
      } else {
        grouped.set(listing.source_group_id, [listing]);
      }
    }
    return grouped;
  }, [visible]);

  const summary = useMemo(
    () =>
      summaryStatuses.map((key) => ({
        key,
        label: status(key),
        value: groups.reduce(
          (count, group) => count + (group.current_runs[key] || 0),
          0,
        ),
      })),
    [groups],
  );

  const action = async (path: string) => {
    if (mutationInFlight.current) return;
    mutationInFlight.current = true;
    setMutationBusy(true);
    try {
      await request(path, {
        method: "POST",
        headers: { "Idempotency-Key": idempotency() },
      });
      await load(false);
    } catch (actionError) {
      setError(
        actionError instanceof Error ? actionError.message : "Action failed.",
      );
    } finally {
      mutationInFlight.current = false;
      setMutationBusy(false);
    }
  };

  if (loading) {
    return (
      <div
        className="creative-pipeline-loading"
        role="status"
        aria-label="Loading creative pipeline"
      >
        <i />
        <i />
        <i />
        <i />
      </div>
    );
  }

  return (
    <div className="creative-pipeline-tab">
      <header className="creative-pipeline-heading">
        <div className="creative-pipeline-heading-copy">
          <small>CREATIVE PIPELINE</small>
          <h2>Listing video pipeline</h2>
          <p>Track inputs, GPT skills, prompts and video output by source folder.</p>
        </div>
        <div className="creative-pipeline-heading-actions">
          {canManage && (
            <button
              className="ops-button secondary"
              type="button"
              onClick={() => setSkillManagerOpen(true)}
            >
              GPT Skills
            </button>
          )}
          <button
            className="ops-button"
            type="button"
            disabled={refreshing || mutationBusy}
            onClick={() => void load(false)}
          >
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </header>

      {error && (
        <div className="creative-pipeline-error" role="alert">
          {error}
          <button
            type="button"
            onClick={() => setError(null)}
            aria-label="Dismiss error"
          >
            ×
          </button>
        </div>
      )}

      <section
        className="creative-pipeline-summary"
        aria-label="Pipeline run summary"
      >
        {summary.map((item) => (
          <article
            className={`creative-pipeline-stat ${item.key}`}
            key={item.key}
          >
            <div>
              <small>{item.label}</small>
              <span>Current runs</span>
            </div>
            <strong>{item.value}</strong>
          </article>
        ))}
      </section>

      <div className="creative-pipeline-toolbar">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Find listing or folder"
          aria-label="Find listing"
        />
        <span className="creative-pipeline-result-count" aria-live="polite">
          {visible.length} / {listings.length} listings
        </span>
        <select
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          aria-label="Filter status"
        >
          <option value="">All statuses</option>
          <option value="running">Running</option>
          <option value="queued">Queued</option>
          <option value="failed">Failed</option>
          <option value="completed">Completed</option>
        </select>
      </div>

      <div className="creative-pipeline-groups">
        {groups.map((group) => {
          const rows = visibleByGroup.get(group.id) || [];

          return (
            <section
              className={`creative-pipeline-group${rows.length ? "" : " is-empty"}`}
              key={group.id}
            >
              <header>
                <div className="creative-pipeline-group-main">
                  <div className="creative-pipeline-group-title">
                    <span className="creative-pipeline-platform">
                      {group.platform.toUpperCase()}
                    </span>
                    <h3>{group.name}</h3>
                  </div>
                  <span className="creative-pipeline-group-count">
                    {group.active_listing_count}/{group.listing_count} active
                  </span>
                </div>

                <div className="creative-pipeline-group-actions">
                  {!rows.length && (
                    <span className="creative-pipeline-no-match">
                      No matching listings
                    </span>
                  )}
                  {canManage && (
                    <button
                      className="ops-button secondary"
                      type="button"
                      disabled={mutationBusy}
                      onClick={() =>
                        void action(
                          "/api/v1/creative-pipeline/groups/" +
                            group.id +
                            "/scan",
                        )
                      }
                    >
                      {mutationBusy ? "Working…" : "Scan now"}
                    </button>
                  )}
                </div>
              </header>

              {rows.length > 0 && (
                <div className="creative-pipeline-list">
                  {rows.map((listing) => (
                    <button
                      className="creative-pipeline-row"
                      key={listing.id}
                      type="button"
                      onClick={() => setSelected(listing)}
                    >
                      <div className="creative-pipeline-row-copy">
                        <b>{listing.folder_name}</b>
                        <small>
                          {listing.listing_key} · {listing.source_status}
                        </small>
                      </div>
                      <Nodes nodes={listing.current_run?.nodes || []} />
                      <span
                        className={
                          "creative-status " +
                          (listing.pipeline_status || "empty")
                        }
                      >
                        {status(listing.pipeline_status)}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </section>
          );
        })}
      </div>

      {selected && (
        <Drawer
          listing={selected}
          canManage={canManage}
          close={() => setSelected(null)}
          action={action}
          busy={mutationBusy}
        />
      )}

      {skillManagerOpen && (
        <SkillManager close={() => setSkillManagerOpen(false)} />
      )}
    </div>
  );
}

const nodeSymbol = (node: Node) =>
  node.inherited || node.status === "completed"
    ? "✓"
    : node.status === "failed" ||
        node.status === "blocked" ||
        node.status === "cancelled"
      ? "×"
      : node.status === "running" || node.status === "retrying"
        ? "●"
        : "○";

const nodeTone = (node: Node) => (node.inherited ? "completed" : node.status);

function Nodes({ nodes }: { nodes: Node[] }) {
  return (
    <div className="creative-node-strip" aria-label="Pipeline stages">
      {nodes.map((node, index) => (
        <div className="creative-node" key={node.node_type}>
          <span
            className={nodeTone(node)}
            title={`${labels[node.node_type] || node.node_type}: ${status(node.status)}${node.executor_type === "gpt_skill" ? " · GPT Skill" : ""}`}
          >
            <i aria-hidden="true">{nodeSymbol(node)}</i>
            <small>{labels[node.node_type] || node.node_type}</small>
            {node.executor_type === "gpt_skill" && <em>GPT</em>}
          </span>
          {index < nodes.length - 1 ? (
            <b
              className={
                node.inherited || node.status === "completed" ? "done" : ""
              }
              aria-hidden="true"
            />
          ) : null}
        </div>
      ))}
    </div>
  );
}

function Drawer({
  listing,
  canManage,
  close,
  action,
  busy,
}: {
  listing: Listing;
  canManage: boolean;
  close: () => void;
  action: (path: string) => Promise<void>;
  busy: boolean;
}) {
  const run = listing.current_run;
  const [skills, setSkills] = useState<SkillBindingSummary[]>([]);
  const [executions, setExecutions] = useState<SkillExecution[]>([]);
  const [catalog, setCatalog] = useState<Skill[]>([]);
  const [skillLoading, setSkillLoading] = useState(true);
  const [skillError, setSkillError] = useState<string | null>(null);
  const [savingNode, setSavingNode] = useState<string | null>(null);
  const [selectedVersions, setSelectedVersions] = useState<
    Record<string, string>
  >({});
  const [selectedScopes, setSelectedScopes] = useState<
    Record<string, "tenant" | "source_group" | "listing">
  >({});

  const loadSkillDetails = async () => {
    setSkillLoading(true);
    setSkillError(null);
    try {
      const [bindingResponse, executionResponse] = await Promise.all([
        request<{ items: SkillBindingSummary[] }>(
          `/api/v1/creative-pipeline/listings/${listing.id}/skills`,
        ),
        run
          ? request<{ items: SkillExecution[] }>(
              `/api/v1/creative-pipeline/runs/${run.id}/skill-executions`,
            )
          : Promise.resolve({ items: [] as SkillExecution[] }),
      ]);
      setSkills(bindingResponse.items);
      setExecutions(executionResponse.items);
      setSelectedVersions(
        Object.fromEntries(
          bindingResponse.items.map((item) => [
            item.node_type,
            item.skill_version_id,
          ]),
        ),
      );
      setSelectedScopes(
        Object.fromEntries(
          bindingResponse.items.map((item) => [
            item.node_type,
            item.binding_scope === "tenant" ||
            item.binding_scope === "source_group" ||
            item.binding_scope === "listing"
              ? item.binding_scope
              : "listing",
          ]),
        ),
      );

      if (canManage) {
        try {
          const catalogResponse = await request<{ items: Skill[] }>(
            "/api/v1/creative-pipeline/skills?include_content=false",
          );
          setCatalog(catalogResponse.items);
        } catch {
          setCatalog([]);
        }
      } else {
        setCatalog([]);
      }
    } catch (loadError) {
      setSkillError(
        loadError instanceof Error
          ? loadError.message
          : "Unable to load GPT skill details.",
      );
    } finally {
      setSkillLoading(false);
    }
  };

  useEffect(() => {
    void loadSkillDetails();
  }, [listing.id, run?.id, canManage]);

  const bindSkill = async (nodeType: string) => {
    const versionId = selectedVersions[nodeType];
    const scopeType = selectedScopes[nodeType] || "listing";
    if (!versionId) return;
    const scopeId =
      scopeType === "listing"
        ? listing.id
        : scopeType === "source_group"
          ? listing.source_group_id
          : null;
    setSavingNode(nodeType);
    setSkillError(null);
    try {
      await request("/api/v1/creative-pipeline/skill-bindings", {
        method: "PUT",
        body: JSON.stringify({
          node_type: nodeType,
          skill_version_id: versionId,
          scope_type: scopeType,
          scope_id: scopeId,
        }),
      });
      await loadSkillDetails();
    } catch (saveError) {
      setSkillError(
        saveError instanceof Error ? saveError.message : "Unable to save skill.",
      );
    } finally {
      setSavingNode(null);
    }
  };

  const resetSkill = async (
    nodeType: string,
    scopeType: "tenant" | "source_group" | "listing",
  ) => {
    const scopeId =
      scopeType === "listing"
        ? listing.id
        : scopeType === "source_group"
          ? listing.source_group_id
          : null;
    const scopeQuery = scopeId
      ? `&scope_id=${encodeURIComponent(scopeId)}`
      : "";
    setSavingNode(nodeType);
    setSkillError(null);
    try {
      await request(
        `/api/v1/creative-pipeline/skill-bindings/${nodeType}?scope_type=${scopeType}${scopeQuery}`,
        { method: "DELETE" },
      );
      await loadSkillDetails();
    } catch (saveError) {
      setSkillError(
        saveError instanceof Error ? saveError.message : "Unable to reset skill.",
      );
    } finally {
      setSavingNode(null);
    }
  };

  return (
    <div className="creative-drawer-backdrop">
      <aside className="creative-drawer" role="dialog" aria-modal="true">
        <header>
          <div>
            <small>{listing.platform.toUpperCase()}</small>
            <h2>{listing.folder_name}</h2>
            <p>
              Listing {listing.listing_key} · {status(listing.pipeline_status)}
            </p>
          </div>
          <button type="button" onClick={close} aria-label="Close details">
            ×
          </button>
        </header>

        <section className="creative-skill-section">
          <div className="creative-section-heading">
            <div>
              <h3>GPT Skills</h3>
              <p>Effective version for Idea and Prompt nodes.</p>
            </div>
            {skillLoading && <span className="creative-inline-loading">Loading…</span>}
          </div>
          {skillError && (
            <p className="creative-skill-error">{skillError}</p>
          )}
          {!skillLoading && skills.length > 0 && (
            <div className="creative-skill-binding-list">
              {skills.map((item) => {
                const versions = catalog
                  .filter((skill) => skill.node_type === item.node_type)
                  .flatMap((skill) =>
                    skill.versions
                      .filter((version) => version.status === "published")
                      .map((version) => ({
                        id: version.id,
                        label: `${skill.name} · v${version.version}`,
                      })),
                  );
                return (
                  <article key={item.node_type}>
                    <div className="creative-skill-binding-copy">
                      <span>{labels[item.node_type] || item.node_type}</span>
                      <strong>
                        {item.skill_name} <b>v{item.skill_version}</b>
                      </strong>
                      <small>
                        {scopeLabel(item.binding_scope)} · {item.provider}
                        {item.preferred_model
                          ? ` · ${item.preferred_model}`
                          : ""}
                      </small>
                    </div>
                    {canManage && versions.length > 0 && (
                      <div className="creative-skill-binding-controls">
                        <select
                          aria-label={`Skill version for ${item.node_type}`}
                          value={
                            selectedVersions[item.node_type] ||
                            item.skill_version_id
                          }
                          onChange={(event) =>
                            setSelectedVersions((current) => ({
                              ...current,
                              [item.node_type]: event.target.value,
                            }))
                          }
                        >
                          {versions.map((version) => (
                            <option key={version.id} value={version.id}>
                              {version.label}
                            </option>
                          ))}
                        </select>
                        <select
                          className="creative-skill-scope-select"
                          aria-label={`Binding scope for ${item.node_type}`}
                          value={selectedScopes[item.node_type] || "listing"}
                          onChange={(event) =>
                            setSelectedScopes((current) => ({
                              ...current,
                              [item.node_type]: event.target.value as
                                | "tenant"
                                | "source_group"
                                | "listing",
                            }))
                          }
                        >
                          <option value="listing">This listing</option>
                          <option value="source_group">Source group</option>
                          <option value="tenant">Tenant default</option>
                        </select>
                        <button
                          type="button"
                          className="ops-button secondary"
                          disabled={savingNode === item.node_type}
                          onClick={() => void bindSkill(item.node_type)}
                        >
                          Apply
                        </button>
                        {(item.binding_scope === "listing" ||
                          item.binding_scope === "source_group" ||
                          item.binding_scope === "tenant") && (
                          <button
                            type="button"
                            className="creative-skill-reset"
                            disabled={savingNode === item.node_type}
                            title={`Remove ${scopeLabel(item.binding_scope).toLowerCase()}`}
                            onClick={() =>
                              void resetSkill(
                                item.node_type,
                                item.binding_scope as
                                  | "tenant"
                                  | "source_group"
                                  | "listing",
                              )
                            }
                          >
                            Reset
                          </button>
                        )}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          )}
        </section>


        {run ? (
          <>
            <section>
              <h3>Run #{run.run_number}</h3>
              <Nodes nodes={run.nodes} />
            </section>

            {executions.length > 0 && (
              <section>
                <h3>Skill executions</h3>
                <div className="creative-skill-execution-list">
                  {executions.map((execution) => {
                    const node = run.nodes.find(
                      (item) => item.id === execution.node_run_id,
                    );
                    return (
                      <article key={execution.id}>
                        <div>
                          <strong>
                            {labels[node?.node_type || ""] ||
                              node?.node_type ||
                              "GPT"}{" "}
                            {execution.variant_key !== "default" && (
                              <span>{execution.variant_key}</span>
                            )}
                          </strong>
                          <small>
                            {execution.skill_key} · v{execution.skill_version} ·{" "}
                            {scopeLabel(execution.binding_scope)}
                          </small>
                        </div>
                        <div className="creative-skill-execution-meta">
                          <span
                            className={`creative-status ${execution.status}`}
                          >
                            {status(execution.status)}
                          </span>
                          <small>
                            {execution.model || execution.provider} ·{" "}
                            {usageSummary(execution.usage)}
                          </small>
                        </div>
                      </article>
                    );
                  })}
                </div>
              </section>
            )}

            <section>
              <h3>Artifact versions</h3>
              {run.artifacts.length ? (
                <ul className="creative-artifact-list">
                  {run.artifacts.map((artifact) => (
                    <li key={artifact.id}>
                      <b>{artifact.artifact_type.replaceAll("_", " ")}</b>
                      <span>
                        v{artifact.version} · {artifact.aspect_ratio || "all"} ·{" "}
                        {status(artifact.status)}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p>No artifacts yet.</p>
              )}
            </section>
            <section>
              <h3>Generation history</h3>
              {run.generations.map((generation) => (
                <p className="creative-generation" key={generation.id}>
                  {generation.provider} · {generation.model} ·{" "}
                  {generation.aspect_ratio} · #{generation.generation_number}
                  <b>{status(generation.status)}</b>
                </p>
              ))}
            </section>
          </>
        ) : (
          <p>No run has been created.</p>
        )}

        {canManage && (
          <footer>
            {!run && (
              <button
                className="ops-button"
                type="button"
                disabled={busy}
                onClick={() =>
                  void action(
                    "/api/v1/creative-pipeline/listings/" + listing.id + "/run",
                  )
                }
              >
                Run pipeline
              </button>
            )}
            {run?.status === "queued" && (
              <button
                className="ops-button"
                type="button"
                disabled={busy}
                onClick={() =>
                  void action(
                    "/api/v1/creative-pipeline/listings/" +
                      listing.id +
                      "/start",
                  )
                }
              >
                Start
              </button>
            )}
            {run &&
              !["completed", "failed", "cancelled"].includes(run.status) && (
                <button
                  className="ops-button secondary"
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void action(
                      "/api/v1/creative-pipeline/runs/" + run.id + "/cancel",
                    )
                  }
                >
                  Cancel
                </button>
              )}
            {run &&
              ["completed", "failed", "cancelled"].includes(run.status) && (
                <>
                  <button
                    className="ops-button secondary"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void action(
                        "/api/v1/creative-pipeline/listings/" +
                          listing.id +
                          "/regenerate-idea",
                      )
                    }
                  >
                    Regenerate idea
                  </button>
                  <button
                    className="ops-button secondary"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void action(
                        "/api/v1/creative-pipeline/listings/" +
                          listing.id +
                          "/regenerate-prompt",
                      )
                    }
                  >
                    Regenerate prompt
                  </button>
                </>
              )}
          </footer>
        )}
      </aside>
    </div>
  );
}

function SkillManager({ close }: { close: () => void }) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [instructions, setInstructions] = useState("");
  const [preferredModel, setPreferredModel] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected =
    skills.find((skill) => skill.id === selectedId) || skills[0] || null;
  const latestPublished = selected?.versions.find(
    (version) => version.status === "published",
  );
  const effectiveTenantVersion = selected?.versions.find(
    (version) => version.id === selected.effective_tenant_version_id,
  );
  const editorBaseVersion = effectiveTenantVersion || latestPublished;

  const load = async (preferredSkillId?: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await request<{ items: Skill[] }>(
        "/api/v1/creative-pipeline/skills?include_content=true",
      );
      setSkills(response.items);
      const nextId =
        preferredSkillId ||
        (response.items.some((item) => item.id === selectedId)
          ? selectedId
          : response.items[0]?.id || "");
      setSelectedId(nextId);
      const nextSkill = response.items.find((item) => item.id === nextId);
      const nextVersion =
        nextSkill?.versions.find(
          (version) => version.id === nextSkill.effective_tenant_version_id,
        ) ||
        nextSkill?.versions.find((version) => version.status === "published");
      setInstructions(nextVersion?.instructions || "");
      setPreferredModel(nextVersion?.preferred_model || "");
    } catch (loadError) {
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Unable to load GPT skills.",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const selectSkill = (skill: Skill) => {
    setSelectedId(skill.id);
    const version =
      skill.versions.find(
        (item) => item.id === skill.effective_tenant_version_id,
      ) || skill.versions.find((item) => item.status === "published");
    setInstructions(version?.instructions || "");
    setPreferredModel(version?.preferred_model || "");
    setError(null);
  };

  const publishAndUse = async () => {
    if (!selected || !instructions.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const version = await request<SkillVersion>(
        `/api/v1/creative-pipeline/skills/${selected.id}/versions`,
        {
          method: "POST",
          body: JSON.stringify({
            instructions: instructions.trim(),
            preferred_model: preferredModel.trim() || null,
            knowledge_refs:
              editorBaseVersion?.knowledge_refs ||
              selected.versions[0]?.knowledge_refs ||
              [],
            status: "published",
          }),
        },
      );
      await request("/api/v1/creative-pipeline/skill-bindings", {
        method: "PUT",
        body: JSON.stringify({
          node_type: selected.node_type,
          skill_version_id: version.id,
          scope_type: "tenant",
          scope_id: null,
        }),
      });
      await load(selected.id);
    } catch (saveError) {
      setError(
        saveError instanceof Error
          ? saveError.message
          : "Unable to publish GPT skill.",
      );
    } finally {
      setSaving(false);
    }
  };

  const useVersion = async (version: SkillVersion) => {
    if (!selected) return;
    setSaving(true);
    setError(null);
    try {
      await request("/api/v1/creative-pipeline/skill-bindings", {
        method: "PUT",
        body: JSON.stringify({
          node_type: selected.node_type,
          skill_version_id: version.id,
          scope_type: "tenant",
          scope_id: null,
        }),
      });
      await load(selected.id);
    } catch (saveError) {
      setError(
        saveError instanceof Error
          ? saveError.message
          : "Unable to activate skill version.",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="creative-drawer-backdrop creative-skill-manager-backdrop">
      <aside
        className="creative-drawer creative-skill-manager"
        role="dialog"
        aria-modal="true"
        aria-label="GPT Skill registry"
      >
        <header>
          <div>
            <small>GPT SKILL REGISTRY</small>
            <h2>Creative skills</h2>
            <p>Versioned instructions for GPT-backed pipeline nodes.</p>
          </div>
          <button type="button" onClick={close} aria-label="Close skill registry">
            ×
          </button>
        </header>

        {error && <p className="creative-skill-error">{error}</p>}

        {loading ? (
          <div className="creative-skill-manager-loading">Loading skills…</div>
        ) : (
          <div className="creative-skill-manager-layout">
            <nav aria-label="GPT skills">
              {skills.map((skill) => (
                <button
                  type="button"
                  key={skill.id}
                  className={skill.id === selected?.id ? "active" : ""}
                  onClick={() => selectSkill(skill)}
                >
                  <span>{labels[skill.node_type] || skill.node_type}</span>
                  <strong>{skill.name}</strong>
                  <small>
                    {skill.latest_published_version
                      ? `v${skill.latest_published_version}`
                      : "No published version"}
                  </small>
                </button>
              ))}
            </nav>

            {selected && (
              <div className="creative-skill-editor">
                <div className="creative-skill-editor-title">
                  <div>
                    <span className="creative-skill-executor-badge">GPT Skill</span>
                    <h3>{selected.name}</h3>
                    <p>{selected.description}</p>
                  </div>
                  <small>{selected.key}</small>
                </div>

                <label>
                  Instructions
                  <textarea
                    value={instructions}
                    onChange={(event) => setInstructions(event.target.value)}
                    rows={10}
                    spellCheck={false}
                  />
                </label>

                <label>
                  Preferred model
                  <input
                    value={preferredModel}
                    onChange={(event) => setPreferredModel(event.target.value)}
                    placeholder="Provider default"
                  />
                </label>

                <div className="creative-skill-knowledge">
                  <span>Knowledge references</span>
                  <div>
                    {(editorBaseVersion?.knowledge_refs || []).map((ref) => (
                      <small key={ref}>{ref}</small>
                    ))}
                  </div>
                </div>

                <div className="creative-skill-editor-actions">
                  <button
                    className="ops-button"
                    type="button"
                    disabled={saving || !instructions.trim()}
                    onClick={() => void publishAndUse()}
                  >
                    {saving ? "Publishing…" : "Publish new version & use"}
                  </button>
                </div>

                <div className="creative-skill-version-list">
                  <h3>Versions</h3>
                  {selected.versions.map((version) => (
                    <article key={version.id}>
                      <div>
                        <strong>v{version.version}</strong>
                        <span
                          className={`creative-status ${version.status}`}
                        >
                          {status(version.status)}
                        </span>
                      </div>
                      <small>
                        {version.provider}
                        {version.preferred_model
                          ? ` · ${version.preferred_model}`
                          : " · provider default"}
                        {version.owner_tenant_id ? " · tenant version" : " · system"}
                      </small>
                      {version.status === "published" && (
                        <button
                          type="button"
                          className="creative-skill-use-version"
                          disabled={
                            saving ||
                            version.id === selected.effective_tenant_version_id
                          }
                          onClick={() => void useVersion(version)}
                        >
                          {version.id === selected.effective_tenant_version_id
                            ? selected.effective_tenant_scope === "tenant"
                              ? "Active tenant default"
                              : "Active system default"
                            : "Use as tenant default"}
                        </button>
                      )}
                    </article>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}