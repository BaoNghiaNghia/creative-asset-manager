import { useEffect, useMemo, useState } from "react";
import {
  bindCampaignProduct,
  listGenerationAttempts,
  listProducts,
  prepareGenerationAttempt,
} from "./api";
import type { Campaign, Candidate, GenerationAttempt, Product } from "./types";

type Props = {
  campaign: Campaign;
  candidates: Candidate[];
  onCampaignChanged: (campaign: Campaign) => void;
  onError: (message: string) => void;
};

const durableStatuses = new Set(["drive_ready", "rejected_duplicate"]);

export function CampaignGenerationPanel({
  campaign,
  candidates,
  onCampaignChanged,
  onError,
}: Props) {
  const [products, setProducts] = useState<Product[]>([]);
  const [attempts, setAttempts] = useState<GenerationAttempt[]>([]);
  const [productId, setProductId] = useState(campaign.product_id || "");
  const [candidateId, setCandidateId] = useState("");
  const [busy, setBusy] = useState("");

  const durableCandidates = useMemo(
    () => candidates.filter(candidate => durableStatuses.has(candidate.status)),
    [candidates],
  );

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
    ]).then(([productRows, attemptRows]) => {
      setProducts(productRows);
      setAttempts(attemptRows);
    }).catch(reason => {
      if (!controller.signal.aborted) {
        onError(reason instanceof Error ? reason.message : "Unable to load generation foundation.");
      }
    });
    return () => controller.abort();
  }, [campaign.id]);

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
    setBusy("prepare");
    onError("");
    try {
      const result = await prepareGenerationAttempt(campaign.id, candidateId);
      setAttempts(rows => {
        if (rows.some(row => row.id === result.attempt.id)) return rows;
        return [result.attempt, ...rows];
      });
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to prepare generation.");
    } finally {
      setBusy("");
    }
  }

  const selectedProduct = products.find(product => product.id === productId);
  const latestByCandidate = new Map<string, GenerationAttempt>();
  for (const attempt of attempts) {
    if (!latestByCandidate.has(attempt.candidate_id)) {
      latestByCandidate.set(attempt.candidate_id, attempt);
    }
  }

  return <section className="rrugc-generation-foundation">
    <div className="rrugc-generation-heading">
      <div>
        <small>PHASE 6 FOUNDATION</small>
        <strong>Campaign product + generation provenance</strong>
        <p>Freeze the exact SKU geometry and product-reference versions before a generation provider is queued.</p>
      </div>
      <span className="rrugc-safe-badge">Prepared only</span>
    </div>

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
        <span>Prepared attempts</span>
        <b>{attempts.length}</b>
      </div>
      {attempts.length === 0 ? <p>No generation attempt prepared yet.</p> :
        attempts.slice(0, 8).map(attempt =>
          <article key={attempt.id}>
            <div><strong>{attempt.product_sku}</strong><small>{attempt.worker_skill_version}</small></div>
            <span>person {attempt.candidate_id.slice(0, 8)}</span>
            <span>product rev {attempt.product_revision}</span>
            <span>{attempt.reference_count} refs</span>
            <em>{attempt.status}</em>
          </article>
        )}
    </div>
  </section>;
}
