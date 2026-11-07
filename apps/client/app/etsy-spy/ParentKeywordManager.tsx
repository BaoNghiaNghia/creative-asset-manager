import { useEffect, useState, type FormEvent } from "react";
import { spyFetch } from "./api";

export type ParentKeywordEntry = {
  keyword: string;
  keywordKey: string;
  // 0 = no priority (normal queue); 1..100 = prioritized, larger runs first.
  priority: number;
  source: "manual" | "observed";
  occurrences: number;
};
type ParentKeywordResponse = {
  items: ParentKeywordEntry[];
  count: number;
  revision: string;
};

export function ParentKeywordManager({ canManage, refresh }: {
  canManage: boolean;
  refresh: number;
}) {
  const [items, setItems] = useState<ParentKeywordEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [requestError, setRequestError] = useState("");
  const [keyword, setKeyword] = useState("");
  const [priority, setPriority] = useState(50);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [previousPriorities, setPreviousPriorities] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    spyFetch<ParentKeywordResponse>("/parent-keywords", { signal: controller.signal })
      .then(response => {
        if (!controller.signal.aborted) {
          setItems(response.items);
          setLoading(false);
          setRequestError("");
        }
      }).catch(error => {
        if (!controller.signal.aborted) {
          setRequestError(error instanceof Error ? error.message : "Cannot load parent keywords");
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [refresh]);

  const updateItems = (response: ParentKeywordResponse) => {
    setItems(response.items);
    setNotice("Saved. Windows Agent will apply this priority on its next sync.");
    setRequestError("");
  };

  const add = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const clean = keyword.trim().replace(/\s+/g, " ");
    if (!canManage || busy || clean.length < 2 || clean.length > 180 ||
        !Number.isInteger(priority) || priority < 1 || priority > 100) return;
    setBusy("add"); setNotice(""); setRequestError("");
    try {
      const response = await spyFetch<ParentKeywordResponse>("/parent-keywords", {
        method: "POST", body: JSON.stringify({ keyword: clean, priority }),
      });
      updateItems(response);
      setKeyword("");
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : "Cannot add parent keyword");
    } finally { setBusy(null); }
  };

  // Switching mode persists immediately. Numeric priority can still be edited,
  // retaining compatibility with the 1..100 Windows Agent scheduling contract.
  const savePriority = async (row: ParentKeywordEntry, value: number) => {
    if (!canManage || busy || !Number.isInteger(value) || value < 0 || value > 100 ||
        value === row.priority) return;
    setBusy(row.keywordKey); setNotice(""); setRequestError("");
    try {
      const response = await spyFetch<ParentKeywordResponse>("/parent-keywords", {
        method: "PUT", body: JSON.stringify({ keyword: row.keyword, priority: value }),
      });
      if (row.priority > 0 && value === 0) {
        setPreviousPriorities(old => ({ ...old, [row.keywordKey]: row.priority }));
      }
      updateItems(response);
      setDrafts(old => { const next = { ...old }; delete next[row.keywordKey]; return next; });
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : "Cannot update priority");
    } finally { setBusy(null); }
  };

  return <section className="etsy-parent-keywords" aria-label="Parent keyword priorities">
    <div className="etsy-parent-keywords-heading">
      <div>
        <h3><span className="etsy-parent-folder-icon" aria-hidden="true">▣</span>
          Parent Keywords <span className="etsy-parent-heading-count">({items.length})</span></h3>
        <p>Add new Scout roots and set their priority mode. Prioritized roots run first.</p>
      </div>
      <span className="etsy-parent-legend">2 priority modes</span>
    </div>
    <form className="etsy-parent-create" onSubmit={add}>
      <input aria-label="New parent keyword" value={keyword} maxLength={180}
        onChange={event => setKeyword(event.target.value)}
        placeholder="+  Add a new parent keyword..."
        disabled={!canManage || Boolean(busy)} />
      <label>Priority
        <input aria-label="New parent priority" type="number" min={1} max={100} step={1}
          value={priority} onChange={event => setPriority(Number(event.target.value))}
          disabled={!canManage || Boolean(busy)} />
      </label>
      <button type="submit" disabled={!canManage || Boolean(busy) ||
        keyword.trim().length < 2 || !Number.isInteger(priority) ||
        priority < 1 || priority > 100}>
        + Add keyword
      </button>
    </form>
    {requestError && <p className="etsy-parent-error" role="alert">{requestError}</p>}
    {notice && <p className="etsy-parent-notice" role="status">{notice}</p>}
    {loading ? <p className="etsy-parent-loading">Loading parent keywords…</p>
      : items.length === 0 ? <p className="etsy-parent-empty">
        No parent keywords yet. Add one above to queue it for Windows Scout.
      </p> : <div className="etsy-parent-list">
        {items.map(row => {
          const prioritized = row.priority > 0;
          const draft = drafts[row.keywordKey] ?? String(prioritized ? row.priority : 50);
          const changed = prioritized && draft !== String(row.priority);
          const validDraft = Number.isInteger(Number(draft)) &&
            Number(draft) >= 1 && Number(draft) <= 100;
          const disabled = !canManage || Boolean(busy);
          return <div className={"etsy-parent-row" + (!prioritized ? " is-normal" : "")}
            key={row.keywordKey}>
            <div className="etsy-parent-title" title={row.keyword}>
              <strong>{row.keyword}</strong>
              <small><span aria-hidden="true">▤</span> {row.occurrences
                ? row.occurrences + " Scout record(s)" : "New root · awaiting Scout"}</small>
            </div>
            <div className="etsy-parent-row-controls">
              <div className="etsy-parent-priority-field">
                <label htmlFor={"etsy-priority-" + row.keywordKey}>Priority:</label>
                {prioritized ? <input id={"etsy-priority-" + row.keywordKey} type="number"
                  min={1} max={100} step={1}
                  aria-label={"Priority for " + row.keyword}
                  value={draft} disabled={disabled}
                  onChange={event => setDrafts(old => ({
                    ...old, [row.keywordKey]: event.target.value,
                  }))}
                  onKeyDown={event => {
                    if (event.key === "Enter" && changed && validDraft) {
                      event.preventDefault(); void savePriority(row, Number(draft));
                    }
                  }} /> : <span className="etsy-parent-priority-off" aria-label="No priority">—</span>}
                {changed && <button className="etsy-parent-save" type="button"
                  aria-label={"Save priority for " + row.keyword}
                  disabled={disabled || !validDraft}
                  onClick={() => void savePriority(row, Number(draft))}>Save</button>}
              </div>
              <div className="etsy-parent-mode-switch" role="group"
                aria-label={"Priority mode for " + row.keyword}>
                <button type="button" className="etsy-parent-mode-prioritized"
                  aria-pressed={prioritized} disabled={disabled || prioritized}
                  onClick={() => void savePriority(row, previousPriorities[row.keywordKey] ?? 50)}>
                  <span aria-hidden="true">✓</span> Ưu tiên
                </button>
                <button type="button" className="etsy-parent-mode-normal"
                  aria-pressed={!prioritized} disabled={disabled || !prioritized}
                  onClick={() => void savePriority(row, 0)}>Không ưu tiên</button>
              </div>
            </div>
          </div>;
        })}
      </div>}
    {!canManage && <small className="etsy-parent-readonly">
      Manage permission is required to change parent priorities.
    </small>}
    <small className="etsy-parent-help">Không ưu tiên = mức 0, xếp sau các root được ưu tiên; không xóa lịch sử Scout.</small>
  </section>;
}
