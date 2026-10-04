import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { managementApi, type Scope, type Share } from "./api";

export function canManagePublicReview(permissions: string[]) {
  return permissions.includes("public_review.manage");
}

export function defaultShareExpiryLocal(now = new Date()) {
  const value = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60 * 1000);
  return local.toISOString().slice(0, 16);
}

function future(value: string) {
  return !value || new Date(value).getTime() > Date.now();
}

function LinkResult({ url, onDismiss }: { url: string; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false);
  return <div className="public-review-link-result">
    <strong>Anyone with this link can access this review.</strong>
    <code>{url}</code>
    <div className="public-review-link-result-actions">
      <button
        type="button"
        className="primary"
        onClick={async () => {
          await navigator.clipboard?.writeText(url);
          setCopied(true);
        }}
      >{copied ? "Copied" : "Copy link"}</button>
      <button type="button" className="secondary" onClick={onDismiss}>Dismiss</button>
    </div>
  </div>;
}

export function PublicReviewManagementDialog({
  initialScope,
  availableScopes,
  onClose,
}: {
  initialScope: Scope;
  availableScopes: Scope[];
  onClose: () => void;
}) {
  const [shares, setShares] = useState<Share[]>([]);
  const [name, setName] = useState("Review");
  const [scopes, setScopes] = useState<Scope[]>([initialScope]);
  const [comments, setComments] = useState(true);
  const [download, setDownload] = useState(false);
  const [expires, setExpires] = useState(() => defaultShareExpiryLocal());
  const [oneTime, setOneTime] = useState<string>();
  const [editing, setEditing] = useState<Share>();
  const [error, setError] = useState("");

  const load = () => managementApi.list()
    .then(value => {
      setShares(value.items);
      setError("");
    })
    .catch(() => setError("Unable to load review shares."));

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const save = async () => {
    if (!name.trim() || !scopes.length || !future(expires)) {
      setError("Enter a review name, at least one folder, and a future expiration.");
      return;
    }
    if (new Set(scopes.map(scope => scope.external_source_id + ":" + scope.folder_external_id)).size !== scopes.length) {
      setError("Folder scopes must be unique.");
      return;
    }
    try {
      setError("");
      const value = {
        name: name.trim(),
        scopes,
        allow_comments: comments,
        allow_download: download,
        expires_at: expires ? new Date(expires).toISOString() : null,
      };
      const share = editing
        ? await managementApi.update(editing.id, value)
        : await managementApi.create(value);
      setOneTime(share.share_url);
      setEditing(undefined);
      void load();
    } catch {
      setError("Unable to save this review share.");
    }
  };

  const edit = (share: Share) => {
    setEditing(share);
    setName(share.name);
    setScopes(share.scopes);
    setComments(share.allow_comments);
    setDownload(share.allow_download);
    setExpires(share.expires_at ? share.expires_at.slice(0, 16) : "");
    setOneTime(undefined);
    setError("");
  };

  const dialog = <div
    className="public-review-management-backdrop"
    role="presentation"
    onMouseDown={event => {
      if (event.target === event.currentTarget) onClose();
    }}
  >
    <section
      className="public-review-management"
      role="dialog"
      aria-modal="true"
      aria-labelledby="public-review-management-title"
    >
      <header>
        <div>
          <span>PUBLIC REVIEW</span>
          <h2 id="public-review-management-title">{editing ? "Edit review share" : "Share for review"}</h2>
          <p>Create a review link for this folder or manage existing shared links.</p>
        </div>
        <button type="button" className="public-review-management-close" onClick={onClose} aria-label="Close">×</button>
      </header>

      <div className="public-review-management-body">
        {oneTime ? <LinkResult url={oneTime} onDismiss={() => setOneTime(undefined)} /> : <>
          <label className="public-review-management-field">
            <span>Review name</span>
            <input value={name} onChange={event => setName(event.target.value)} maxLength={255} />
          </label>

          <fieldset className="public-review-management-scopes">
            <legend>Folder scopes</legend>
            <div className="public-review-management-scope-list">
              {scopes.map(scope => <div className="public-review-management-scope" key={scope.external_source_id + scope.folder_external_id}>
                <span>{scope.folder_name || scope.folder_external_id}</span>
                <button type="button" onClick={() => setScopes(value => value.filter(item => item !== scope))}>Remove</button>
              </div>)}
            </div>
            <div className="public-review-management-add-scopes">
              {availableScopes
                .filter(option => !scopes.some(scope => scope.external_source_id === option.external_source_id && scope.folder_external_id === option.folder_external_id))
                .map(option => <button
                  type="button"
                  key={option.external_source_id + option.folder_external_id}
                  onClick={() => setScopes(value => [...value, option])}
                >+ Add {option.folder_name || option.folder_external_id}</button>)}
            </div>
          </fieldset>

          <div className="public-review-management-options">
            <label><input type="checkbox" checked={comments} onChange={event => setComments(event.target.checked)} /> <span>Allow comments</span></label>
            <label><input type="checkbox" checked={download} onChange={event => setDownload(event.target.checked)} /> <span>Allow downloads</span></label>
          </div>

          <label className="public-review-management-field">
            <span>Expiration <small>(optional)</small></span>
            <input type="datetime-local" value={expires} onChange={event => setExpires(event.target.value)} />
          </label>

          <button type="button" className="public-review-management-primary" onClick={() => void save()}>
            {editing ? "Save settings" : "Create review link"}
          </button>
        </>}

        {error && <p className="public-review-management-error" role="alert">{error}</p>}

        <section className="public-review-existing-shares">
          <div className="public-review-existing-shares-heading">
            <h3>Existing shares</h3>
            <span>{shares.length}</span>
          </div>
          {shares.length === 0 ? <p className="public-review-existing-shares-empty">No review links yet.</p> : <div className="public-review-existing-share-list">
            {shares.map(share => <article key={share.id}>
              <div className="public-review-existing-share-title">
                <b>{share.name}</b>
                <span data-status={share.status}>{share.status}</span>
              </div>
              <div className="public-review-existing-share-scopes">
                {share.scopes.map(scope => <span key={scope.external_source_id + scope.folder_external_id}>{scope.folder_name || scope.folder_external_id}</span>)}
              </div>
              {share.status !== "revoked" && <div className="public-review-existing-share-actions">
                <button type="button" onClick={() => edit(share)}>Edit</button>
                <button type="button" onClick={async () => {
                  if (confirm("Old link and public sessions will stop. Rotate this link?")) {
                    const value = await managementApi.rotate(share.id);
                    setOneTime(value.share_url);
                    void load();
                  }
                }}>Rotate link</button>
                <button type="button" className="danger" onClick={async () => {
                  if (confirm("Public access and existing sessions will stop. Revoke this link?")) {
                    await managementApi.revoke(share.id);
                    void load();
                  }
                }}>Revoke</button>
              </div>}
            </article>)}
          </div>}
        </section>
      </div>
    </section>
  </div>;

  return typeof document !== "undefined"
    ? createPortal(dialog, document.body)
    : dialog;
}
