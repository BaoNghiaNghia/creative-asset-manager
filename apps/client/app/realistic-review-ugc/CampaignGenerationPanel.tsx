import { useEffect, useMemo, useState } from "react";
import {
  bindCampaignProduct,
  executeGenerationAttempt,
  generationAttemptOutputUrl,
  getGenerationCapability,
  listGenerationAttempts,
  listProducts,
  prepareGenerationAttempt,
} from "./api";
import type {
  Campaign,
  Candidate,
  GenerationAttempt,
  GenerationCapability,
  Product,
} from "./types";

type Props = {
  campaign: Campaign;
  candidates: Candidate[];
  onCampaignChanged: (campaign: Campaign) => void;
  onError: (message: string) => void;
};

const durableStatuses = new Set(["drive_ready", "rejected_duplicate"]);
const activeAttemptStatuses = new Set(["queued", "running"]);

export function CampaignGenerationPanel({
  campaign,
  candidates,
  onCampaignChanged,
  onError,
}: Props) {
  const [products, setProducts] = useState<Product[]>([]);
  const [attempts, setAttempts] = useState<GenerationAttempt[]>([]);
  const [capability, setCapability] = useState<GenerationCapability | null>(null);
  const [productId, setProductId] = useState(campaign.product_id || "");
  const [candidateId, setCandidateId] = useState("");
  const [busy, setBusy] = useState("");

  const durableCandidates = useMemo(
    () => candidates.filter(candidate => durableStatuses.has(candidate.status)),
    [candidates],
  );
  const hasActiveAttempt = attempts.some(attempt => activeAttemptStatuses.has(attempt.status));

  useEffect(() => {
    setProductId(campaign.product_id || "");
  }, [campaign.id, campaign.product_id]);

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
    ]).then(([productRows, attemptRows, generationCapability]) => {
      setProducts(productRows);
      setAttempts(attemptRows);
      setCapability(generationCapability);
    }).catch(reason => {
      if (!controller.signal.aborted) {
        onError(reason instanceof Error ? reason.message : "Unable to load generation workspace.");
      }
    });
    return () => controller.abort();
  }, [campaign.id]);

  useEffect(() => {
    if (!hasActiveAttempt) return;
    const timer = window.setInterval(() => {
      void listGenerationAttempts(campaign.id)
        .then(setAttempts)
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [campaign.id, hasActiveAttempt]);

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

  return <section className="rrugc-generation-foundation">
    <div className="rrugc-generation-heading">
      <div>
        <small>PHASE 6 · WORKER SKILL</small>
        <strong>Reference-conditioned product generation</strong>
        <p>Freeze the person, SKU geometry and exact product-reference versions, then run a traceable Gemini multi-reference edit.</p>
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
        attempts.slice(0, 8).map(attempt =>
          <article key={attempt.id}>
            <div>
              <strong>{attempt.product_sku}</strong>
              <small>{attempt.worker_skill_version}</small>
            </div>
            <span>person {attempt.candidate_id.slice(0, 8)}</span>
            <span>rev {attempt.product_revision} · variant {attempt.generation_variant}</span>
            <span>{attempt.reference_count} refs</span>
            <em>{attempt.status}</em>
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
          </article>
        )}
    </div>
  </section>;
}
