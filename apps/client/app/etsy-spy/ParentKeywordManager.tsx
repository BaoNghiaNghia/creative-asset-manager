import { useEffect, useState, type FormEvent } from "react";
import { spyFetch } from "./api";

export type ParentKeywordEntry = {
  keyword: string;
  keywordKey: string;
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
    if (!canManage || busy || clean.length < 2 || clean.length > 180) return;
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
  const savePriority = async (row: ParentKeywordEntry) => {
    const value = Number(drafts[row.keywordKey] ?? row.priority);
    if (!canManage || busy || !Number.isInteger(value) || value < 1 || value > 100) {
      setRequestError("Priority must be a whole number from 1 to 100");
      return;
    }
    setBusy(row.keywordKey); setNotice(""); setRequestError("");
    try {
      const response = await spyFetch<ParentKeywordResponse>("/parent-keywords", {
        method: "PUT", body: JSON.stringify({ keyword: row.keyword, priority: value }),
      });
      updateItems(response);
      setDrafts(old => { const next = { ...old }; delete next[row.keywordKey]; return next; });
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : "Cannot update priority");
    } finally { setBusy(null); }
  };
  return <section className="etsy-parent-keywords" aria-label="Parent keyword priorities">
    <div className="etsy-parent-keywords-heading">
      <div>
        <h3>Parent Keywords <span>({items.length})</span></h3>
        <p>Add new Scout roots and prioritize existing parents. Higher number runs first, 100 = highest.</p>
      </div>
      <span className="etsy-parent-legend">Priority 1–100</span>
    </div>
    <form className="etsy-parent-create" onSubmit={add}>
      <input aria-label="New parent keyword" value={keyword} maxLength={180}
        onChange={event => setKeyword(event.target.value)}
        placeholder="Add a new parent keyword..."
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
          const draft = drafts[row.keywordKey] ?? String(row.priority);
          const changed = draft !== String(row.priority);
          return <div className="etsy-parent-row" key={row.keywordKey}>
            <div className="etsy-parent-title" title={row.keyword}>
              <strong>{row.keyword}</strong>
              <small>{row.source === "observed"
                ? row.occurrences + " Scout record(s)" : row.occurrences
                  ? row.occurrences + " Scout record(s)" : "New root · awaiting Scout"}
              </small>
            </div>
            <label>Priority
              <input type="number" min={1} max={100} step={1}
                aria-label={"Priority for " + row.keyword}
                value={draft} disabled={!canManage || Boolean(busy)}
                onChange={event => setDrafts(old => ({
                  ...old, [row.keywordKey]: event.target.value,
                }))} />
            </label>
            <button className="etsy-parent-save" type="button"
              aria-label={"Save priority for " + row.keyword}
              disabled={!canManage || Boolean(busy) || !changed ||
                !Number.isInteger(Number(draft)) || Number(draft) < 1 || Number(draft) > 100}
              onClick={() => savePriority(row)}>Save</button>
          </div>;
        })}
      </div>}
    {!canManage && <small className="etsy-parent-readonly">Manage permission is required to edit parent priorities.</small>}
  </section>;
}
