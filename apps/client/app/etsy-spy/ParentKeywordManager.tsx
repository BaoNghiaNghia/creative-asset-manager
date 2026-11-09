import { useEffect, useState, type FormEvent } from "react";
import { spyFetch } from "./api";

export type ParentKeywordEntry = {
  keyword: string;
  keywordKey: string;
  // Existing API contract: 0 = normal; 1..100 = prioritized.
  priority: number;
  source: "manual" | "observed";
  occurrences: number;
};

type ParentKeywordResponse = {
  items: ParentKeywordEntry[];
  count: number;
  revision: string;
};

const DEFAULT_PRIORITY = 50;

export function ParentKeywordManager({ canManage, refresh }: {
  canManage: boolean;
  refresh: number;
}) {
  const [items, setItems] = useState<ParentKeywordEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [requestError, setRequestError] = useState("");
  const [keyword, setKeyword] = useState("");
  const [previousPriorities, setPreviousPriorities] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    spyFetch<ParentKeywordResponse>("/parent-keywords", { signal: controller.signal })
      .then(response => {
        if (controller.signal.aborted) return;
        setItems(response.items);
        setLoading(false);
        setRequestError("");
      })
      .catch(error => {
        if (controller.signal.aborted) return;
        setRequestError(error instanceof Error ? error.message : "Cannot load parent keywords");
        setLoading(false);
      });
    return () => controller.abort();
  }, [refresh]);

  const applyResult = (response: ParentKeywordResponse) => {
    setItems(response.items);
    setRequestError("");
  };

  const add = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const clean = keyword.trim().replace(/\s+/g, " ");
    if (!canManage || busy || clean.length < 2 || clean.length > 180) return;
    setBusy("add");
    setRequestError("");
    try {
      const response = await spyFetch<ParentKeywordResponse>("/parent-keywords", {
        method: "POST",
        body: JSON.stringify({ keyword: clean, priority: DEFAULT_PRIORITY }),
      });
      applyResult(response);
      setKeyword("");
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : "Cannot add parent keyword");
    } finally {
      setBusy(null);
    }
  };

  // The UI exposes only two modes; numeric values stay private to preserve
  // Windows Agent ordering and existing priorities if a mode is toggled back.
  const saveMode = async (row: ParentKeywordEntry, prioritized: boolean) => {
    const enabled = row.priority > 0;
    if (!canManage || busy || enabled === prioritized) return;
    const nextPriority = prioritized
      ? previousPriorities[row.keywordKey] ?? DEFAULT_PRIORITY
      : 0;
    setBusy(row.keywordKey);
    setRequestError("");
    try {
      const response = await spyFetch<ParentKeywordResponse>("/parent-keywords", {
        method: "PUT",
        body: JSON.stringify({ keyword: row.keyword, priority: nextPriority }),
      });
      if (!prioritized) {
        setPreviousPriorities(old => ({ ...old, [row.keywordKey]: row.priority }));
      }
      applyResult(response);
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : "Cannot update priority mode");
    } finally {
      setBusy(null);
    }
  };

  return <section className="etsy-parent-keywords" aria-label="Parent keyword priorities">
    <div className="etsy-parent-keywords-heading">
      <h3>Parent Keywords</h3>
    </div>
    <form className="etsy-parent-create" onSubmit={add}>
      <input aria-label="New parent keyword" value={keyword} maxLength={180}
        onChange={event => setKeyword(event.target.value)}
        placeholder="Nhập parent keyword..."
        disabled={!canManage || Boolean(busy)} />
      <button type="submit" disabled={!canManage || Boolean(busy) ||
        keyword.trim().length < 2}>+ Thêm</button>
    </form>
    {requestError && <p className="etsy-parent-error" role="alert">{requestError}</p>}
    {loading ? <p className="etsy-parent-loading">Đang tải keyword…</p>
      : items.length === 0 ? <p className="etsy-parent-empty">
        Chưa có keyword. Nhập keyword ở trên để thêm.
      </p> : <div className="etsy-parent-list">
        {items.map(row => {
          const prioritized = row.priority > 0;
          const disabled = !canManage || Boolean(busy);
          return <div className="etsy-parent-row" key={row.keywordKey}>
            <strong className="etsy-parent-title" title={row.keyword}>{row.keyword}</strong>
            <div className="etsy-parent-mode-switch" role="group"
              aria-label={"Priority mode for " + row.keyword}>
              <button type="button" className="etsy-parent-mode-prioritized"
                aria-pressed={prioritized} disabled={disabled || prioritized}
                onClick={() => void saveMode(row, true)}>Ưu tiên</button>
              <button type="button" className="etsy-parent-mode-normal"
                aria-pressed={!prioritized} disabled={disabled || !prioritized}
                onClick={() => void saveMode(row, false)}>Không ưu tiên</button>
            </div>
          </div>;
        })}
      </div>}
  </section>;
}
