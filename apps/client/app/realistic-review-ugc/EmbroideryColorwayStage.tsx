import { useEffect, useMemo, useState } from "react";
import { listStage2Skills } from "./api";
import { RrugcStageHeader } from "./RrugcStageHeader";
import { RrugcSmartSearchInput } from "./RrugcSmartSearchInput";
import type { SourcePlan, SourcePlanPage, Stage2Skill, Stage2SkillCatalog } from "./types";

const COLOR_SLOT_COUNT = 13;
const STAGE1_PAGE_SIZE_OPTIONS = [20, 50, 100, 500] as const;
const EMPTY_CATALOG: Stage2SkillCatalog = {
  openai_configured: false,
  openai_status: "not_configured",
  error_code: null,
  items: [],
};

function cleanDesignName(name: string): string {
  return name
    .replace(/^embroidery_/i, "")
    .replace(/\.[a-z0-9]+$/i, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim() || name;
}

function skillKey(skill: Stage2Skill): string {
  return [skill.source, skill.skill_id || skill.skill_name].join(":");
}

function colorSlotLabel(index: number): string {
  return String(index + 1).padStart(2, "0");
}

function HatSlotIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M5.2 13.1c.5-4.5 3.2-7.2 6.8-7.2s6.3 2.7 6.8 7.2" />
    <path d="M4.1 13.1h14.7c1.4 0 2.4.5 2.8 1.4-2.4 1.7-5.6 2.5-9.6 2.5-4 0-7.2-.8-9.6-2.5.3-.9.9-1.4 1.7-1.4Z" />
    <path d="M12 6v7" />
  </svg>;
}

function DesignSource({ plan }: { plan: SourcePlan }) {
  const groupCount = Math.max(1, plan.source_group_images?.length || plan.embroidery_group_size || 1);
  return <div className="rrugc-colorway-source">
    <button type="button" className="rrugc-colorway-thumb" title={plan.source_name}>
      <img src={plan.source_preview_url} alt={cleanDesignName(plan.source_name)} loading="lazy" />
      <span className="rrugc-colorway-prefix">embroidery_</span>
    </button>
    <div>
      <strong>{cleanDesignName(plan.source_name)}</strong>
      <small title={plan.source_relative_path}>{plan.source_relative_path}</small>
      <div className="rrugc-colorway-source-meta">
        <span>{groupCount} source {groupCount === 1 ? "image" : "images"}</span>
        {plan.source_width && plan.source_height
          ? <span>{plan.source_width}×{plan.source_height}</span>
          : null}
      </div>
    </div>
  </div>;
}

export function EmbroideryColorwayStage({
  data,
  loading,
  syncing,
  query,
  message,
  executionReady = false,
  active = true,
  skillCatalogRevision = 0,
  onSync,
  onQueryChange,
  onPageChange,
  onPageSizeChange,
}: {
  data: SourcePlanPage;
  loading: boolean;
  syncing: boolean;
  query: string;
  message?: string;
  executionReady?: boolean;
  active?: boolean;
  skillCatalogRevision?: number;
  onSync: () => void;
  onQueryChange: (value: string) => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}) {
  const [catalog, setCatalog] = useState<Stage2SkillCatalog>(EMPTY_CATALOG);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogMessage, setCatalogMessage] = useState("");
  const [selectedSkillKey, setSelectedSkillKey] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setCatalogLoading(true);
    void listStage2Skills(false, controller.signal)
      .then(result => {
        setCatalog(result);
        const firstReady = result.items.find(item => item.ready) || result.items[0];
        if (firstReady) setSelectedSkillKey(current => current || skillKey(firstReady));
      })
      .catch(reason => {
        if (!controller.signal.aborted) {
          setCatalogMessage(reason instanceof Error ? reason.message : "Unable to load skills.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setCatalogLoading(false);
      });
    return () => controller.abort();
  }, [active, skillCatalogRevision]);

  const selectedSkill = useMemo(
    () => catalog.items.find(item => skillKey(item) === selectedSkillKey) || null,
    [catalog.items, selectedSkillKey],
  );
  const pageCount = Math.max(1, Math.ceil(data.total / Math.max(1, data.page_size)));
  const plannedOutputs = data.total * COLOR_SLOT_COUNT;
  const pageIds = data.items.map(item => item.id);
  const pageSelected = pageIds.length > 0 && pageIds.every(id => selectedIds.has(id));
  const ready = Boolean(selectedSkill?.ready);
  const canQueue = executionReady && ready && selectedIds.size > 0;
  const searchSuggestions = useMemo(() => data.items.flatMap(plan => {
    const folder = plan.source_relative_path.includes("/")
      ? plan.source_relative_path.split("/").slice(0, -1).join("/")
      : "";
    return [
      { value: cleanDesignName(plan.source_name), meta: folder || "Embroidery design", badge: "Design" },
      ...(folder ? [{ value: folder, meta: cleanDesignName(plan.source_name), badge: "Folder" }] : []),
    ];
  }), [data.items]);

  function togglePage() {
    setSelectedIds(current => {
      const next = new Set(current);
      if (pageSelected) pageIds.forEach(id => next.delete(id));
      else pageIds.forEach(id => next.add(id));
      return next;
    });
  }

  function toggleDesign(id: string) {
    setSelectedIds(current => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return <section className="rrugc-card rrugc-colorway-stage">
    <RrugcStageHeader
      className="rrugc-colorway-header"
      kicker="STAGE 2 · EMBROIDERY_ SOURCE → SKILL → 13 HAT COLORS"
      title="Embroidery design → 13 colorways"
      description={<>Scans only source images whose filename starts with <code>embroidery_</code>. Each design becomes one 13-color batch so the same embroidery can be applied consistently across every hat color before Pinterest reference discovery begins.</>}
      actions={<div className="rrugc-colorway-header-actions">
        <span className="rrugc-colorway-scan-badge"><i aria-hidden="true" />Prefix scan · embroidery_</span>
        <button type="button" className="rrugc-primary" disabled={syncing} onClick={onSync}>
          {syncing ? "Scanning…" : "Scan source"}
        </button>
      </div>}
    />

    {(message || catalogMessage) && <p className="rrugc-editor-product-result" role="status">{message || catalogMessage}</p>}

    <div className="rrugc-colorway-kpis">
      <article><span>Designs found</span><strong>{data.total}</strong><small>embroidery_ files</small></article>
      <article><span>Hat colors</span><strong>{COLOR_SLOT_COUNT}</strong><small>per design</small></article>
      <article><span>Planned outputs</span><strong>{plannedOutputs}</strong><small>design × color</small></article>
      <article><span>Selected</span><strong>{selectedIds.size}</strong><small>{selectedIds.size * COLOR_SLOT_COUNT} outputs</small></article>
    </div>

    <div className="rrugc-colorway-toolbar">
      <div className="rrugc-colorway-search">
        <RrugcSmartSearchInput
          stageId="stage2"
          query={query}
          onQueryChange={onQueryChange}
          suggestions={searchSuggestions}
          placeholder="Search embroidery design or folder…"
          label="Search Stage 2 embroidery designs"
        />
      </div>
      <label className="rrugc-colorway-skill">
        <span>Generation skill</span>
        <select
          value={selectedSkillKey}
          disabled={catalogLoading || catalog.items.length === 0}
          onChange={event => setSelectedSkillKey(event.target.value)}
        >
          {catalog.items.length === 0 && <option value="">{catalogLoading ? "Loading skills…" : "No skill available"}</option>}
          {catalog.items.map(skill => (
            <option key={skillKey(skill)} value={skillKey(skill)}>
              {skill.display_name}{skill.ready ? "" : " · needs sync"}
            </option>
          ))}
        </select>
      </label>
      <button type="button" className="rrugc-colorway-select" onClick={togglePage} disabled={loading || data.items.length === 0}>
        {pageSelected ? "Clear page" : "Select page"}
      </button>
      <button
        type="button"
        className="rrugc-colorway-run"
        disabled={!canQueue}
        title={executionReady ? (ready ? "Queue selected designs" : "Selected skill is not ready") : "Execution wiring will use the selected skill and the configured 13-color hat base set."}
      >
        Run selected · {selectedIds.size * COLOR_SLOT_COUNT}
      </button>
    </div>

    <div className="rrugc-colorway-table-wrap">
      <table className="rrugc-colorway-table" aria-busy={loading}>
        <thead>
          <tr>
            <th aria-label="Select design" />
            <th>Embroidery design</th>
            <th>13-color batch</th>
            <th>Progress</th>
            <th>Skill</th>
          </tr>
        </thead>
        <tbody>
          {loading ? Array.from({ length: 5 }, (_, index) => (
            <tr key={index} className="rrugc-colorway-skeleton-row">
              <td><span /></td><td><span /></td><td><span /></td><td><span /></td><td><span /></td>
            </tr>
          )) : data.items.map(plan => {
            const selected = selectedIds.has(plan.id);
            return <tr key={plan.id} className={selected ? "is-selected" : ""}>
              <td className="rrugc-colorway-check-cell">
                <input
                  type="checkbox"
                  checked={selected}
                  aria-label={"Select " + cleanDesignName(plan.source_name)}
                  onChange={() => toggleDesign(plan.id)}
                />
              </td>
              <td><DesignSource plan={plan} /></td>
              <td>
                <div className="rrugc-colorway-slots" aria-label="13 hat color output slots">
                  {Array.from({ length: COLOR_SLOT_COUNT }, (_, index) => (
                    <span className="rrugc-colorway-slot is-pending" key={index} title={"Hat color " + colorSlotLabel(index)}>
                      <HatSlotIcon />
                      <b>{colorSlotLabel(index)}</b>
                    </span>
                  ))}
                </div>
              </td>
              <td>
                <div className="rrugc-colorway-progress">
                  <div><span style={{ width: "0%" }} /></div>
                  <strong>0 / 13</strong>
                  <small>Ready to queue</small>
                </div>
              </td>
              <td>
                <div className="rrugc-colorway-skill-state">
                  <span className={ready ? "is-ready" : "is-warning"}><i aria-hidden="true" />{ready ? "Ready" : "Needs skill"}</span>
                  <strong>{selectedSkill?.display_name || "Select skill"}</strong>
                  <small>{executionReady ? "13 jobs per design" : "Runner wiring pending"}</small>
                </div>
              </td>
            </tr>;
          })}
          {!loading && data.items.length === 0 && <tr>
            <td colSpan={5} className="rrugc-source-plan-empty">
              No <code>embroidery_</code> source images found. Add embroidery detail files to the source tree, then scan again.
            </td>
          </tr>}
        </tbody>
      </table>
    </div>

    <footer className="rrugc-colorway-footer">
      <span>
        Page {data.page} / {pageCount} · {data.total} designs · {plannedOutputs} planned outputs
      </span>
      <div>
        <button type="button" disabled={loading || data.page <= 1} onClick={() => onPageChange(Math.max(1, data.page - 1))}>Previous</button>
        <button type="button" disabled={loading || data.page >= pageCount} onClick={() => onPageChange(Math.min(pageCount, data.page + 1))}>Next</button>
        <label>Rows <select aria-label="Stage 2 rows per page" value={data.page_size} disabled={loading} onChange={event => onPageSizeChange(Number(event.target.value))}>
          {STAGE1_PAGE_SIZE_OPTIONS.map(value => <option key={value} value={value}>{value}</option>)}
        </select></label>
      </div>
    </footer>
  </section>;
}
