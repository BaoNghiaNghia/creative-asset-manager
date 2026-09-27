import { useEffect, useMemo, useState } from "react";
import {
  bindCampaignProduct,
  executeGenerationAttempt,
  createDeliveryDestination,
  deliverCampaign,
  exportCampaignOutputs,
  generationAttemptOutputUrl,
  getCampaign,
  getCampaignDeliverySummary,
  getCampaignExportSummary,
  getGenerationCapability,
  listDeliveryDestinations,
  listDeliveryPackages,
  listGenerationAttempts,
  listProducts,
  listSupervisorResults,
  prepareGenerationAttempt,
  prepareSupervisorCorrection,
  reconcileDeliveryLifecycle,
  updateCampaignLifecyclePolicy,
} from "./api";
import type {
  Campaign,
  Candidate,
  GenerationAttempt,
  GenerationCapability,
  CampaignExportSummary,
  CampaignDeliverySummary,
  DeliveryDestination,
  DeliveryPackage,
  Product,
  SupervisorResult,
} from "./types";

type Props = {
  campaign: Campaign;
  candidates: Candidate[];
  onCampaignChanged: (campaign: Campaign) => void;
  onError: (message: string) => void;
};

const durableStatuses = new Set(["drive_ready", "rejected_duplicate"]);
const activeAttemptStatuses = new Set(["queued", "running"]);

function metricPercent(value: number | null | undefined) {
  return typeof value === "number" ? Math.round(value * 100) + "%" : "—";
}

export function CampaignExportPanel({
  summary,
  busy,
  onExport,
}: {
  summary: CampaignExportSummary | null;
  busy: boolean;
  onExport: () => void;
}) {
  return <section className="rrugc-export-panel" aria-label="Export and catalog">
    <div className="rrugc-generation-heading">
      <div>
        <small>PHASE 9 · EXPORT + CATALOG</small>
        <strong>Approved output registry</strong>
        <p>Register approved Managed Drive outputs in the canonical asset catalog without copying or re-uploading the file.</p>
      </div>
      <button
        type="button"
        className="rrugc-primary"
        disabled={busy || !summary?.export_ready}
        onClick={onExport}
      >
        {busy ? "Exporting…" : "Export ready (" + (summary?.export_ready ?? 0) + ")"}
      </button>
    </div>
    <div className="rrugc-export-stats">
      <span><small>Generated</small><b>{summary?.generated ?? 0}</b></span>
      <span><small>Review pending</small><b>{summary?.review_pending ?? 0}</b></span>
      <span><small>Approved</small><b>{summary?.approved ?? 0}</b></span>
      <span><small>Rejected</small><b>{summary?.rejected ?? 0}</b></span>
      <span><small>Export ready</small><b>{summary?.export_ready ?? 0}</b></span>
      <span><small>Cataloged</small><b>{summary?.exported ?? 0}</b></span>
    </div>
  </section>;
}

export function CampaignDeliveryPanel({
  summary,
  destinations,
  packages,
  selectedDestinationId,
  autoComplete,
  destinationName,
  destinationFolderId,
  retentionDays,
  busy,
  onDestinationChange,
  onAutoCompleteChange,
  onDestinationNameChange,
  onDestinationFolderIdChange,
  onRetentionDaysChange,
  onCreateDestination,
  onDeliver,
  onSavePolicy,
  onReconcile,
}: {
  summary: CampaignDeliverySummary | null;
  destinations: DeliveryDestination[];
  packages: DeliveryPackage[];
  selectedDestinationId: string;
  autoComplete: boolean;
  destinationName: string;
  destinationFolderId: string;
  retentionDays: number;
  busy: string;
  onDestinationChange: (value: string) => void;
  onAutoCompleteChange: (value: boolean) => void;
  onDestinationNameChange: (value: string) => void;
  onDestinationFolderIdChange: (value: string) => void;
  onRetentionDaysChange: (value: number) => void;
  onCreateDestination: () => void;
  onDeliver: () => void;
  onSavePolicy: () => void;
  onReconcile: () => void;
}) {
  const selected = destinations.find(row => row.id === selectedDestinationId) || null;
  const latest = packages[0] || null;
  return <section className="rrugc-delivery-panel" aria-label="Delivery and lifecycle">
    <div className="rrugc-generation-heading">
      <div>
        <small>PHASE 10 · DELIVERY + LIFECYCLE</small>
        <strong>Destination delivery and campaign completion</strong>
        <p>Copy cataloged assets server-side into an explicit Drive destination, track every delivery, and keep canonical catalog originals untouched by delivery retention.</p>
      </div>
      <button
        type="button"
        className="rrugc-primary"
        disabled={Boolean(busy) || !selected || !summary?.cataloged}
        onClick={onDeliver}
      >
        {busy === "deliver" ? "Delivering…" : "Deliver cataloged (" + (summary?.cataloged ?? 0) + ")"}
      </button>
    </div>

    <div className="rrugc-export-stats rrugc-delivery-stats">
      <span><small>Cataloged</small><b>{summary?.cataloged ?? 0}</b></span>
      <span><small>Packages</small><b>{summary?.packages_total ?? 0}</b></span>
      <span><small>Delivered</small><b>{summary?.packages_delivered ?? 0}</b></span>
      <span><small>Retry needed</small><b>{summary?.packages_partial_failed ?? 0}</b></span>
      <span><small>Expired</small><b>{summary?.packages_expired ?? 0}</b></span>
      <span><small>Campaign</small><b>{summary?.completed_at ? "Complete" : summary?.campaign_status ?? "—"}</b></span>
    </div>

    <div className="rrugc-delivery-controls">
      <label>
        Delivery destination
        <select
          value={selectedDestinationId}
          onChange={event => onDestinationChange(event.target.value)}
        >
          <option value="">Choose destination</option>
          {destinations.map(row =>
            <option key={row.id} value={row.id}>
              {row.name} · {row.retention_days}d
            </option>
          )}
        </select>
      </label>
      <label className="rrugc-delivery-policy-toggle">
        <input
          type="checkbox"
          checked={autoComplete}
          onChange={event => onAutoCompleteChange(event.target.checked)}
        />
        Auto-complete after full delivery
      </label>
      <button
        type="button"
        disabled={Boolean(busy) || (autoComplete && !selectedDestinationId)}
        onClick={onSavePolicy}
      >
        {busy === "policy" ? "Saving…" : "Save lifecycle policy"}
      </button>
      <button
        type="button"
        disabled={Boolean(busy)}
        onClick={onReconcile}
      >
        {busy === "lifecycle" ? "Reconciling…" : "Reconcile retention"}
      </button>
    </div>

    <div className="rrugc-delivery-destination-create">
      <label>
        Destination name
        <input
          value={destinationName}
          maxLength={160}
          placeholder="Paid Social Finals"
          onChange={event => onDestinationNameChange(event.target.value)}
        />
      </label>
      <label>
        Google Drive folder ID
        <input
          value={destinationFolderId}
          maxLength={255}
          placeholder="Folder ID"
          onChange={event => onDestinationFolderIdChange(event.target.value)}
        />
      </label>
      <label>
        Retention
        <input
          type="number"
          min={1}
          max={3650}
          value={retentionDays}
          onChange={event => onRetentionDaysChange(Number(event.target.value) || 1)}
        />
      </label>
      <button
        type="button"
        disabled={Boolean(busy) || !destinationName.trim() || !destinationFolderId.trim()}
        onClick={onCreateDestination}
      >
        {busy === "destination" ? "Adding…" : "Add destination"}
      </button>
    </div>

    {selected && <p className="rrugc-delivery-note">
      Selected: <strong>{selected.name}</strong> · retention {selected.retention_days} days.
      Expiration changes delivery-package lifecycle state only; the catalog asset and managed original are preserved.
    </p>}
    {latest && <p className={"rrugc-delivery-latest status-" + latest.status}>
      Latest package: <strong>{latest.status.replaceAll("_", " ")}</strong>
      {" · "}{latest.delivered_count}/{latest.export_count} delivered
      {latest.failed_count ? " · " + latest.failed_count + " retry needed" : ""}
    </p>}
  </section>;
}

export function CampaignGenerationPanel({
  campaign,
  candidates,
  onCampaignChanged,
  onError,
}: Props) {
  const [products, setProducts] = useState<Product[]>([]);
  const [attempts, setAttempts] = useState<GenerationAttempt[]>([]);
  const [supervisorResults, setSupervisorResults] = useState<SupervisorResult[]>([]);
  const [capability, setCapability] = useState<GenerationCapability | null>(null);
  const [exportSummary, setExportSummary] = useState<CampaignExportSummary | null>(null);
  const [deliverySummary, setDeliverySummary] = useState<CampaignDeliverySummary | null>(null);
  const [deliveryDestinations, setDeliveryDestinations] = useState<DeliveryDestination[]>([]);
  const [deliveryPackages, setDeliveryPackages] = useState<DeliveryPackage[]>([]);
  const [selectedDestinationId, setSelectedDestinationId] = useState(
    campaign.completion_destination_id || "",
  );
  const [autoCompleteDelivery, setAutoCompleteDelivery] = useState(
    campaign.auto_complete_on_delivery,
  );
  const [destinationName, setDestinationName] = useState("");
  const [destinationFolderId, setDestinationFolderId] = useState("");
  const [retentionDays, setRetentionDays] = useState(90);
  const [productId, setProductId] = useState(campaign.product_id || "");
  const [candidateId, setCandidateId] = useState("");
  const [busy, setBusy] = useState("");

  const durableCandidates = useMemo(
    () => candidates.filter(candidate => durableStatuses.has(candidate.status)),
    [candidates],
  );
  const hasActiveAttempt = attempts.some(attempt => activeAttemptStatuses.has(attempt.status));
  const hasActiveSupervisor = supervisorResults.some(
    result => result.status === "queued" || result.status === "running",
  );
  const supervisorByAttempt = useMemo(() => {
    const rows = new Map<string, SupervisorResult>();
    for (const result of supervisorResults) {
      if (!rows.has(result.generation_attempt_id)) rows.set(result.generation_attempt_id, result);
    }
    return rows;
  }, [supervisorResults]);

  useEffect(() => {
    setProductId(campaign.product_id || "");
  }, [campaign.id, campaign.product_id]);

  useEffect(() => {
    setAutoCompleteDelivery(campaign.auto_complete_on_delivery);
    setSelectedDestinationId(current =>
      campaign.completion_destination_id
        || current
        || deliveryDestinations[0]?.id
        || "",
    );
  }, [
    campaign.id,
    campaign.auto_complete_on_delivery,
    campaign.completion_destination_id,
    deliveryDestinations,
  ]);

  useEffect(() => {
    setCandidateId(current =>
      current && durableCandidates.some(candidate => candidate.id === current)
        ? current
        : durableCandidates[0]?.id || "",
    );
  }, [durableCandidates]);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      listProducts(controller.signal),
      listGenerationAttempts(campaign.id, controller.signal),
      getGenerationCapability(controller.signal),
      listSupervisorResults(campaign.id, controller.signal),
      getCampaignExportSummary(campaign.id, controller.signal),
      listDeliveryDestinations(controller.signal),
      listDeliveryPackages(campaign.id, controller.signal),
      getCampaignDeliverySummary(campaign.id, controller.signal),
    ]).then(([
      productRows,
      attemptRows,
      generationCapability,
      supervisorRows,
      exportStats,
      destinationRows,
      packageRows,
      deliveryStats,
    ]) => {
      setProducts(productRows);
      setAttempts(attemptRows);
      setCapability(generationCapability);
      setSupervisorResults(supervisorRows);
      setExportSummary(exportStats);
      setDeliveryDestinations(destinationRows);
      setDeliveryPackages(packageRows.items);
      setDeliverySummary(deliveryStats);
    }).catch(reason => {
      if (!controller.signal.aborted) {
        onError(reason instanceof Error ? reason.message : "Unable to load generation workspace.");
      }
    });
    return () => controller.abort();
  }, [campaign.id]);

  useEffect(() => {
    if (!hasActiveAttempt && !hasActiveSupervisor) return;
    const timer = window.setInterval(() => {
      void Promise.all([
        listGenerationAttempts(campaign.id),
        listSupervisorResults(campaign.id),
        getCampaignExportSummary(campaign.id),
      ]).then(([attemptRows, supervisorRows, exportStats]) => {
        setAttempts(attemptRows);
        setSupervisorResults(supervisorRows);
        setExportSummary(exportStats);
      }).catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [campaign.id, hasActiveAttempt, hasActiveSupervisor]);

  async function bindProduct() {
    if (busy) return;
    setBusy("bind");
    onError("");
    try {
      const updated = await bindCampaignProduct(campaign.id, productId || null);
      onCampaignChanged(updated);
      setProductId(updated.product_id || "");
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to bind product.");
    } finally {
      setBusy("");
    }
  }

  async function prepare() {
    if (!candidateId || busy) return;
    const existingPrepared = attempts.find(
      attempt => attempt.candidate_id === candidateId && attempt.status === "prepared",
    );
    if (existingPrepared) return;
    const variants = attempts
      .filter(attempt => attempt.candidate_id === candidateId)
      .map(attempt => attempt.generation_variant);
    const nextVariant = Math.max(0, ...variants) + 1;
    setBusy("prepare");
    onError("");
    try {
      const result = await prepareGenerationAttempt(
        campaign.id,
        candidateId,
        nextVariant,
      );
      setAttempts(rows => {
        const without = rows.filter(row => row.id !== result.attempt.id);
        return [result.attempt, ...without];
      });
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to prepare generation.");
    } finally {
      setBusy("");
    }
  }

  async function execute(attemptId: string) {
    if (busy || !capability?.available) return;
    setBusy("execute:" + attemptId);
    onError("");
    try {
      const updated = await executeGenerationAttempt(attemptId);
      setAttempts(rows => rows.map(row => row.id === updated.id ? updated : row));
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to queue generation.");
    } finally {
      setBusy("");
    }
  }

  async function prepareCorrection(resultId: string) {
    if (busy) return;
    setBusy("correction:" + resultId);
    onError("");
    try {
      const result = await prepareSupervisorCorrection(resultId);
      setAttempts(rows => {
        const without = rows.filter(row => row.id !== result.attempt.id);
        return [result.attempt, ...without];
      });
      const refreshed = await listSupervisorResults(campaign.id);
      setSupervisorResults(refreshed);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to prepare Supervisor correction.");
    } finally {
      setBusy("");
    }
  }

  async function refreshDeliveryWorkspace() {
    const [destinations, packages, deliveryStats] = await Promise.all([
      listDeliveryDestinations(),
      listDeliveryPackages(campaign.id),
      getCampaignDeliverySummary(campaign.id),
    ]);
    setDeliveryDestinations(destinations);
    setDeliveryPackages(packages.items);
    setDeliverySummary(deliveryStats);
    setSelectedDestinationId(current =>
      current && destinations.some(row => row.id === current)
        ? current
        : campaign.completion_destination_id
          || destinations[0]?.id
          || "",
    );
  }

  async function exportApproved() {
    if (busy || !exportSummary?.export_ready) return;
    setBusy("export");
    onError("");
    try {
      await exportCampaignOutputs(campaign.id, 100);
      const [attemptRows, exportStats] = await Promise.all([
        listGenerationAttempts(campaign.id),
        getCampaignExportSummary(campaign.id),
      ]);
      setAttempts(attemptRows);
      setExportSummary(exportStats);
      await refreshDeliveryWorkspace();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to export approved outputs.");
    } finally {
      setBusy("");
    }
  }

  async function createDestination() {
    if (busy || !destinationName.trim() || !destinationFolderId.trim()) return;
    setBusy("destination");
    onError("");
    try {
      const created = await createDeliveryDestination({
        name: destinationName.trim(),
        target_ref: destinationFolderId.trim(),
        retention_days: Math.max(1, Math.min(3650, retentionDays)),
      });
      setDestinationName("");
      setDestinationFolderId("");
      setSelectedDestinationId(created.id);
      await refreshDeliveryWorkspace();
      setSelectedDestinationId(created.id);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to add delivery destination.");
    } finally {
      setBusy("");
    }
  }

  async function saveLifecyclePolicy() {
    if (busy || (autoCompleteDelivery && !selectedDestinationId)) return;
    setBusy("policy");
    onError("");
    try {
      const updated = await updateCampaignLifecyclePolicy(
        campaign.id,
        autoCompleteDelivery,
        selectedDestinationId || null,
      );
      onCampaignChanged(updated);
      setAutoCompleteDelivery(updated.auto_complete_on_delivery);
      setSelectedDestinationId(updated.completion_destination_id || selectedDestinationId);
      await refreshDeliveryWorkspace();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to save lifecycle policy.");
    } finally {
      setBusy("");
    }
  }

  async function deliverCataloged() {
    if (busy || !selectedDestinationId || !deliverySummary?.cataloged) return;
    setBusy("deliver");
    onError("");
    try {
      await deliverCampaign(campaign.id, selectedDestinationId);
      const [updated, exportStats] = await Promise.all([
        getCampaign(campaign.id),
        getCampaignExportSummary(campaign.id),
      ]);
      onCampaignChanged(updated);
      setExportSummary(exportStats);
      await refreshDeliveryWorkspace();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to deliver cataloged outputs.");
    } finally {
      setBusy("");
    }
  }

  async function reconcileLifecycle() {
    if (busy) return;
    setBusy("lifecycle");
    onError("");
    try {
      await reconcileDeliveryLifecycle(200);
      await refreshDeliveryWorkspace();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to reconcile delivery retention.");
    } finally {
      setBusy("");
    }
  }

  return <section className="rrugc-generation-foundation">
    <div className="rrugc-generation-heading">
      <div>
        <small>PHASE 6–7 · WORKER + SUPERVISOR</small>
        <strong>Reference-conditioned generation + deterministic QA</strong>
        <p>Freeze person/SKU provenance, run the multi-reference edit, then score the stored output with structured Supervisor QA before any correction is prepared.</p>
      </div>
      <span className={capability?.available ? "rrugc-safe-badge" : "rrugc-safe-badge unavailable"}>
        {capability == null
          ? "Checking provider…"
          : capability.available
            ? "Gemini multi-reference ready"
            : "Provider unavailable"}
      </span>
    </div>

    {capability && !capability.available && <p className="rrugc-generation-note">
      {capability.reason || "Reference-conditioned generation is unavailable."}
    </p>}

    <div className="rrugc-generation-bind">
      <label>
        Product SKU
        <select value={productId} onChange={event => setProductId(event.target.value)}>
          <option value="">No product bound</option>
          {products.map(product =>
            <option key={product.id} value={product.id}>
              {product.sku} — {product.name} · rev {product.revision}
            </option>
          )}
        </select>
      </label>
      <button type="button" disabled={Boolean(busy)} onClick={() => void bindProduct()}>
        {busy === "bind"
          ? "Saving…"
          : campaign.product_id === productId && productId
            ? "Refresh product snapshot"
            : productId
              ? "Bind product"
              : "Unbind product"}
      </button>
    </div>

    {campaign.product_id ? <div className="rrugc-generation-binding">
      <span><small>Bound SKU</small><b>{campaign.product_sku || "—"}</b></span>
      <span><small>Geometry rev</small><b>{campaign.product_revision ?? "—"}</b></span>
      <span><small>Reference views</small><b>{campaign.product_reference_count}</b></span>
      <span><small>Generation ready</small><b>{campaign.generation_ready ? "Yes" : "No"}</b></span>
      {campaign.product_binding_stale && <span className="stale"><small>Snapshot</small><b>Refresh required</b></span>}
    </div> : <p className="rrugc-generation-note">
      Bind an active SKU before preparing a generation attempt.
    </p>}

    <div className="rrugc-generation-prepare">
      <label>
        Durable person reference
        <select
          value={candidateId}
          onChange={event => setCandidateId(event.target.value)}
          disabled={durableCandidates.length === 0}
        >
          {durableCandidates.length === 0 && <option value="">No Drive-ready reference yet</option>}
          {durableCandidates.map(candidate =>
            <option key={candidate.id} value={candidate.id}>
              {candidate.id.slice(0, 8)} · {candidate.status} · fit {candidate.final_score == null ? "—" : Math.round(candidate.final_score * 100) + "%"}
            </option>
          )}
        </select>
      </label>
      <button
        type="button"
        disabled={Boolean(busy) || !candidateId || !campaign.generation_ready}
        onClick={() => void prepare()}
      >
        {busy === "prepare" ? "Preparing…" : "Prepare generation attempt"}
      </button>
    </div>

    <div className="rrugc-generation-attempts">
      <div className="rrugc-generation-attempt-title">
        <span>Generation attempts</span>
        <b>{attempts.length}</b>
      </div>
      {attempts.length === 0 ? <p>No generation attempt prepared yet.</p> :
        attempts.slice(0, 8).map(attempt => {
          const qa = supervisorByAttempt.get(attempt.id);
          return <article key={attempt.id}>
            <div>
              <strong>{attempt.product_sku}</strong>
              <small>{attempt.worker_skill_version}</small>
            </div>
            <span>person {attempt.candidate_id.slice(0, 8)}</span>
            <span>rev {attempt.product_revision} · variant {attempt.generation_variant}</span>
            <span>{attempt.reference_count} refs</span>
            <em>{attempt.status}</em>
            {attempt.export_status && <span className={"rrugc-export-state status-" + attempt.export_status}>
              {attempt.export_status === "exported"
                ? "Cataloged"
                : attempt.export_status === "export_ready"
                  ? "Export ready"
                  : attempt.export_status === "not_exportable"
                    ? "Not exportable"
                    : "Review pending"}
            </span>}
            {attempt.catalog_asset_id && <small className="rrugc-catalog-id">
              asset {attempt.catalog_asset_id.slice(0, 8)}
            </small>}
            {attempt.status === "prepared" && <button
              type="button"
              className="rrugc-attempt-action"
              disabled={Boolean(busy) || !capability?.available}
              onClick={() => void execute(attempt.id)}
            >
              {busy === "execute:" + attempt.id ? "Queueing…" : "Queue"}
            </button>}
            {attempt.status === "completed" && <a
              className="rrugc-attempt-action"
              href={generationAttemptOutputUrl(attempt.id)}
              target="_blank"
              rel="noreferrer"
            >
              View output
            </a>}
            {attempt.status === "failed" && <small
              className="rrugc-attempt-error"
              title={attempt.last_error_message || undefined}
            >
              {attempt.last_error_code || "generation_failed"}
            </small>}
            {qa && <div className={"rrugc-supervisor-card status-" + qa.status}>
              <div className="rrugc-supervisor-heading">
                <strong>Supervisor QA</strong>
                <em>{qa.status.replaceAll("_", " ")}</em>
              </div>
              {qa.reason && <b className="rrugc-supervisor-reason">{qa.reason}</b>}
              {qa.metrics && <div className="rrugc-supervisor-metrics">
                <span>Product <b>{metricPercent(qa.metrics.product_visual_similarity)}</b></span>
                <span>Placement <b>{metricPercent(qa.metrics.placement_score)}</b></span>
                <span>Scene <b>{metricPercent(qa.metrics.person_scene_preservation)}</b></span>
                <span>Artifact risk <b>{metricPercent(qa.metrics.artifact_risk)}</b></span>
              </div>}
              {qa.summary && <small className="rrugc-supervisor-summary">{qa.summary}</small>}
              {qa.can_retry && <button
                type="button"
                className="rrugc-attempt-action"
                disabled={Boolean(busy)}
                onClick={() => void prepareCorrection(qa.id)}
              >
                {busy === "correction:" + qa.id ? "Preparing…" : "Prepare correction"}
              </button>}
              {qa.status === "needs_human_review" && <small className="rrugc-human-review">
                Retry budget exhausted ({qa.attempt_count}/{qa.max_attempts}) · human review required.
              </small>}
              {qa.status === "error" && <small className="rrugc-attempt-error">
                {qa.last_error_code || "supervisor_failed"}
              </small>}
            </div>}
          </article>;
        })}
    </div>
    <CampaignExportPanel
      summary={exportSummary}
      busy={busy === "export"}
      onExport={() => void exportApproved()}
    />
    <CampaignDeliveryPanel
      summary={deliverySummary}
      destinations={deliveryDestinations}
      packages={deliveryPackages}
      selectedDestinationId={selectedDestinationId}
      autoComplete={autoCompleteDelivery}
      destinationName={destinationName}
      destinationFolderId={destinationFolderId}
      retentionDays={retentionDays}
      busy={busy}
      onDestinationChange={setSelectedDestinationId}
      onAutoCompleteChange={setAutoCompleteDelivery}
      onDestinationNameChange={setDestinationName}
      onDestinationFolderIdChange={setDestinationFolderId}
      onRetentionDaysChange={setRetentionDays}
      onCreateDestination={() => void createDestination()}
      onDeliver={() => void deliverCataloged()}
      onSavePolicy={() => void saveLifecyclePolicy()}
      onReconcile={() => void reconcileLifecycle()}
    />
  </section>;
}
