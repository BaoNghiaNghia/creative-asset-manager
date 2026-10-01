import { useEffect, useMemo, useState } from "react";
import {
  bindCampaignProduct,
  executeGenerationAttempt,
  createDeliveryDestination,
  createReferenceSetFromSkill,
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
  listGenerationSkills,
  listProducts,
  listReferenceAssets,
  listReferenceSets,
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
  GenerationSkill,
  ReferenceAsset,
  ReferenceSet,
  CampaignExportSummary,
  CampaignDeliverySummary,
  DeliveryDestination,
  DeliveryPackage,
  Product,
  SupervisorResult,
} from "./types";
import {
  defaultReferenceSetName,
  missingRequiredPresetRoles,
  referenceAssetsForRole,
  referenceRoleSlots,
} from "./referenceSetPresets";

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
  return <details className="rrugc-export-panel rrugc-compact-disclosure" aria-label="Export and catalog">
    <summary>
      <span><strong>Catalog</strong><small>Approved outputs and export readiness</small></span>
      <b>{summary?.export_ready ?? 0} ready</b>
    </summary>
    <div className="rrugc-generation-heading">
      <div>
        <small>CATALOG</small>
        <strong>Approved outputs</strong>
        <p>Register approved Managed Drive outputs in the canonical asset catalog without duplicating the file.</p>
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
  </details>;
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
  return <details className="rrugc-delivery-panel rrugc-compact-disclosure" aria-label="Delivery and lifecycle">
    <summary>
      <span><strong>Delivery</strong><small>Destination, lifecycle, and package history</small></span>
      <b>{summary?.cataloged ?? 0} cataloged{summary?.packages_partial_failed ? " · " + summary.packages_partial_failed + " retry" : ""}</b>
    </summary>
    <div className="rrugc-generation-heading">
      <div>
        <small>DELIVERY</small>
        <strong>Campaign delivery</strong>
        <p>Send cataloged assets to an explicit Drive destination, track delivery state, and keep canonical originals untouched.</p>
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
  </details>;
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
  const [generationSkills, setGenerationSkills] = useState<GenerationSkill[]>([]);
  const [recommendedSkillName, setRecommendedSkillName] = useState("");
  const [referenceSets, setReferenceSets] = useState<ReferenceSet[]>([]);
  const [referenceAssets, setReferenceAssets] = useState<ReferenceAsset[]>([]);
  const [skillName, setSkillName] = useState("");
  const [referenceSetId, setReferenceSetId] = useState("");
  const [presetName, setPresetName] = useState("");
  const [presetRoleAssets, setPresetRoleAssets] = useState<Record<string, string>>({});
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
  const selectedSkill = useMemo(
    () => generationSkills.find(skill => skill.skill_name === skillName) || null,
    [generationSkills, skillName],
  );
  const presetSlots = useMemo(
    () => referenceRoleSlots(selectedSkill),
    [selectedSkill],
  );
  const missingPresetRoles = useMemo(
    () => missingRequiredPresetRoles(selectedSkill, presetRoleAssets),
    [selectedSkill, presetRoleAssets],
  );
  const compatibleReferenceSets = useMemo(() => {
    if (!selectedSkill) return referenceSets;
    return referenceSets.filter(referenceSet => {
      if (referenceSet.items.length > selectedSkill.max_references) return false;
      const roles = new Set(referenceSet.items.map(item => item.role));
      return selectedSkill.required_reference_roles.every(role => roles.has(role));
    });
  }, [referenceSets, selectedSkill]);
  const selectedReferenceSet = useMemo(
    () => referenceSets.find(referenceSet => referenceSet.id === referenceSetId) || null,
    [referenceSets, referenceSetId],
  );
  const selectedReferenceSetReady = Boolean(
    selectedReferenceSet
      && compatibleReferenceSets.some(referenceSet => referenceSet.id === selectedReferenceSet.id),
  );
  const generationInputReady = Boolean(
    campaign.product_id
      && !campaign.product_binding_stale
      && (selectedReferenceSetReady || campaign.generation_ready),
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
    setSkillName(current =>
      current && generationSkills.some(skill => skill.skill_name === current)
        ? current
        : recommendedSkillName || generationSkills[0]?.skill_name || "",
    );
  }, [generationSkills, recommendedSkillName]);

  useEffect(() => {
    setReferenceSetId(current =>
      current && compatibleReferenceSets.some(referenceSet => referenceSet.id === current)
        ? current
        : compatibleReferenceSets[0]?.id || "",
    );
  }, [compatibleReferenceSets]);

  useEffect(() => {
    setPresetName(defaultReferenceSetName(campaign, selectedSkill));
    setPresetRoleAssets(current => {
      const next: Record<string, string> = {};
      for (const slot of presetSlots) next[slot.role] = current[slot.role] || "";
      return next;
    });
  }, [
    campaign.id,
    campaign.name,
    campaign.product_sku,
    selectedSkill,
    presetSlots,
  ]);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      listProducts(controller.signal),
      listGenerationAttempts(campaign.id, controller.signal),
      getGenerationCapability(controller.signal),
      listGenerationSkills(campaign.id, controller.signal),
      listReferenceSets(campaign.id, controller.signal),
      listReferenceAssets(controller.signal),
      listSupervisorResults(campaign.id, controller.signal),
      getCampaignExportSummary(campaign.id, controller.signal),
      listDeliveryDestinations(controller.signal),
      listDeliveryPackages(campaign.id, controller.signal),
      getCampaignDeliverySummary(campaign.id, controller.signal),
    ]).then(([
      productRows,
      attemptRows,
      generationCapability,
      skillCatalog,
      referenceSetRows,
      referenceAssetRows,
      supervisorRows,
      exportStats,
      destinationRows,
      packageRows,
      deliveryStats,
    ]) => {
      setProducts(productRows);
      setAttempts(attemptRows);
      setCapability(generationCapability);
      setGenerationSkills(skillCatalog.items);
      setRecommendedSkillName(skillCatalog.recommended_skill_name || "");
      setReferenceSets(referenceSetRows);
      setReferenceAssets(referenceAssetRows);
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
  }, [campaign.id, campaign.product_id, campaign.product_revision]);

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

  async function createSkillPreset() {
    if (
      busy
      || !selectedSkill
      || !presetName.trim()
      || missingPresetRoles.length > 0
    ) return;
    const items = presetSlots
      .map(slot => ({
        role: slot.role,
        reference_asset_id: presetRoleAssets[slot.role] || "",
      }))
      .filter(item => item.reference_asset_id);
    if (items.length === 0) return;

    setBusy("preset");
    onError("");
    try {
      const created = await createReferenceSetFromSkill(
        campaign.id,
        selectedSkill.skill_name,
        presetName.trim(),
        items,
      );
      setReferenceSets(rows => [
        created,
        ...rows.filter(row => row.id !== created.id),
      ]);
      setReferenceSetId(created.id);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to create reference preset.");
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
        skillName || undefined,
        referenceSetId || undefined,
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
        <small>PRODUCTION</small>
        <strong>Generate & validate</strong>
        <p>Lock person and product provenance, generate from durable references, then validate the stored output with Supervisor QA.</p>
      </div>
      <span className={capability?.available ? "rrugc-safe-badge" : "rrugc-safe-badge unavailable"}>
        {capability == null
          ? "Checking provider…"
          : capability.available
            ? (capability.provider === "codex" ? "Codex" : "Gemini")
              + " · " + capability.model + " ready"
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

    {campaign.product_id ? <div className="rrugc-generation-binding rrugc-generation-binding-compact">
      <span><small>Bound SKU</small><b>{campaign.product_sku || "—"}</b></span>
      <span><small>Generation</small><b>{generationInputReady ? "Ready" : "Not ready"}</b></span>
      {campaign.product_binding_stale && <span className="stale"><small>Snapshot</small><b>Refresh required</b></span>}
    </div> : <p className="rrugc-generation-note">
      Bind an active SKU before preparing a generation attempt.
    </p>}

    <div className="rrugc-generation-bind">
      <label>
        Generation skill
        <select
          value={skillName}
          onChange={event => setSkillName(event.target.value)}
          disabled={generationSkills.length === 0}
        >
          {generationSkills.length === 0 && <option value="">Default worker skill</option>}
          {generationSkills.map(skill =>
            <option key={skill.skill_name} value={skill.skill_name}>
              {skill.display_name}{skill.recommended ? " · recommended" : ""}
            </option>
          )}
        </select>
      </label>
      <label>
        Reference set
        <select
          value={referenceSetId}
          onChange={event => setReferenceSetId(event.target.value)}
        >
          <option value="">Legacy product references</option>
          {referenceSets.map(referenceSet => {
            const compatible = compatibleReferenceSets.some(row => row.id === referenceSet.id);
            return <option
              key={referenceSet.id}
              value={referenceSet.id}
              disabled={!compatible}
            >
              {referenceSet.name} · {referenceSet.items.length} refs
              {compatible ? "" : " · role mismatch"}
            </option>;
          })}
        </select>
      </label>
    </div>

    {selectedSkill && <div className="rrugc-generation-binding rrugc-generation-binding-compact">
      <span>
        <small>Required roles</small>
        <b>{selectedSkill.required_reference_roles.join(", ") || "none"}</b>
      </span>
      <span>
        <small>Optional roles</small>
        <b>{selectedSkill.optional_reference_roles.join(", ") || "none"}</b>
      </span>
      <span>
        <small>Input mode</small>
        <b>{selectedReferenceSetReady ? "Reference Set" : "Legacy refs"}</b>
      </span>
    </div>}

    {selectedSkill && presetSlots.length > 0 && <details className="rrugc-reference-preset rrugc-compact-disclosure">
      <summary>
        <span>
          <strong>Reference preset</strong>
          <small>Build a role-complete set from the shared Reference Library</small>
        </span>
        <b>{missingPresetRoles.length === 0 ? "ready" : missingPresetRoles.length + " required"}</b>
      </summary>
      <div className="rrugc-reference-preset-body">
        <label className="rrugc-reference-preset-name">
          Set name
          <input
            value={presetName}
            maxLength={200}
            onChange={event => setPresetName(event.target.value)}
          />
        </label>
        <div className="rrugc-reference-role-grid">
          {presetSlots.map(slot => {
            const assets = referenceAssetsForRole(referenceAssets, slot.role, campaign.id);
            return <label key={slot.role}>
              <span>
                {slot.role}
                <small>{slot.required ? "required" : "optional"}</small>
              </span>
              <select
                value={presetRoleAssets[slot.role] || ""}
                onChange={event => setPresetRoleAssets(current => ({
                  ...current,
                  [slot.role]: event.target.value,
                }))}
              >
                <option value="">{slot.required ? "Choose reference…" : "Skip optional role"}</option>
                {assets.map(asset =>
                  <option key={asset.id} value={asset.id}>
                    {(asset.original_filename || asset.source_type + " reference")
                    + " · " + asset.reference_type
                    + (asset.source_campaign_id === campaign.id ? " · this campaign" : "")
                    + (asset.quality_score == null ? "" : " · q" + Math.round(asset.quality_score * 100))}
                  </option>
                )}
              </select>
            </label>;
          })}
        </div>
        <button
          type="button"
          className="rrugc-primary"
          disabled={
            Boolean(busy)
            || !presetName.trim()
            || missingPresetRoles.length > 0
            || referenceAssets.length === 0
          }
          onClick={() => void createSkillPreset()}
        >
          {busy === "preset" ? "Creating preset…" : "Create & select reference set"}
        </button>
        {referenceAssets.length === 0 && <p className="rrugc-generation-note">
          Reference Library has no ready assets yet. Promote or upload reference images first.
        </p>}
      </div>
    </details>}

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
        disabled={Boolean(busy) || !candidateId || !generationInputReady}
        onClick={() => void prepare()}
      >
        {busy === "prepare" ? "Preparing…" : "Prepare generation attempt"}
      </button>
    </div>

    <details className="rrugc-generation-attempts rrugc-compact-disclosure">
      <summary>
        <span><strong>Generation attempts</strong><small>Outputs, Supervisor QA, retries, and provenance</small></span>
        <b>{attempts.length}</b>
      </summary>
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
            {attempt.reference_roles.length > 0 && <span>
              roles {attempt.reference_roles.join(", ")}
            </span>}
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
    </details>
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
