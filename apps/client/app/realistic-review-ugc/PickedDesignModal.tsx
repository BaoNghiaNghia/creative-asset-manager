import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { listSourcePlans, listGenerationOutputVersions, selectStage1ProductDesign } from "./api";
import type { SourcePlan } from "./types";
import "./PickedDesignModal.css";

type PickedItem = { plan: SourcePlan; jobId: string; version: number | null };
export function PickedDesignModal({ onClose, onUnpick }: { onClose: () => void; onUnpick: (jobId: string, version: number) => void }) {
  const [items, setItems] = useState<PickedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [working, setWorking] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      const plans: SourcePlan[] = [];
      let page = 1;
      while (!controller.signal.aborted) {
        const result = await listSourcePlans({ page, pageSize: 500, colorwaySources: true, colorwayWorkflow: "products" }, controller.signal);
        plans.push(...result.items);
        if (plans.length >= result.total || !result.items.length) break;
        page++;
      }
      const groups = new Map<string, SourcePlan[]>();
      for (const plan of plans) {
        const jobId = /^Stage 1\/([^/]+)\//.exec(plan.source_relative_path)?.[1];
        if (jobId) groups.set(jobId, [...(groups.get(jobId) || []), plan]);
      }
      const resolved: PickedItem[] = [];
      const jobs = [...groups.entries()];
      for (let i = 0; i < jobs.length; i += 5) {
        const batch = await Promise.all(jobs.slice(i, i + 5).map(async ([jobId, group]) => {
          try {
            const { versions } = await listGenerationOutputVersions("stage1", jobId, controller.signal);
            return group.map(plan => ({
              plan, jobId,
              version: versions.find(v => v.selected_for_products_stage2 && v.output_name?.split("/").pop() === plan.source_name)?.version
                ?? (group.length === 1 ? versions.find(v => v.selected_for_products_stage2)?.version : undefined) ?? null,
            }));
          } catch { return group.map(plan => ({ plan, jobId, version: null })); }
        }));
        resolved.push(...batch.flat());
      }
      if (!controller.signal.aborted) setItems(resolved);
    }
    void load().catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load picked designs"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", key);
    return () => { controller.abort(); document.removeEventListener("keydown", key); };
  }, [onClose]);
  const unpick = async (item: PickedItem) => {
    if (item.version === null || working) return;
    setWorking(item.plan.id);
    setError("");
    try {
      await selectStage1ProductDesign(item.jobId, item.version, false);
      setItems(previous => previous.filter(row => row.plan.id !== item.plan.id));
      onUnpick(item.jobId, item.version);
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to unpick design"); }
    finally { setWorking(null); }
  };
  return createPortal(<div className="rrugc-picked-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
    <section className="rrugc-picked-dialog" role="dialog" aria-modal="true" aria-label="Picked designs">
      <header><div><h2>Picked design <span>{loading ? "…" : items.length}</span></h2><p>Designs selected in Stage 1 for Products Stage 2</p></div><button type="button" onClick={onClose} aria-label="Close picked designs">✕</button></header>
      {error && <p role="alert" className="rrugc-picked-error">{error}</p>}
      <div className="rrugc-picked-content">{loading ? <p>Loading picked designs…</p> : !items.length ? <p>No picked designs.</p> :
        <div className="rrugc-picked-masonry">{items.map(item => <article key={item.plan.id}>
          <img src={item.plan.source_preview_url} loading="lazy" alt={item.plan.source_name}/>
          <div><strong title={item.plan.source_name}>{item.plan.source_name}</strong><button type="button" disabled={item.version === null || working !== null} onClick={() => void unpick(item)} title={item.version === null ? "Version could not be identified" : "Remove picked design"}>{working === item.plan.id ? "Removing…" : "Unpick"}</button></div>
        </article>)}</div>}</div>
    </section>
  </div>, document.body);
}
