import type { OAuthErrorState, Provider } from "../types";

export function DriveEmpty({ oauthError, provider }: { oauthError: OAuthErrorState; provider: Provider }) {
  const sharepoint = provider === "sharepoint";
  const sourceName = sharepoint ? "SharePoint" : "Google Drive";
  const loginUrl = sharepoint ? "/api/auth/microsoft/login" : "/api/auth/google/login";
  return <div className="drive-empty">
    {oauthError && <div className="oauth-error">
      <strong>{sourceName} sign-in failed</strong><span>{oauthError.message}</span>
      {oauthError.requestId && <small>Request ID: {oauthError.requestId}</small>}
    </div>}
    <span className="drive-empty-icon">{sharepoint ? "S" : "◆"}</span>
    <h1>Connect your {sourceName}</h1>
    <p>Sign in to browse sites, document libraries, folders and files.</p>
    <button onClick={() => window.location.assign(loginUrl)}>Sign in with {sharepoint ? "Microsoft" : "Google"}</button>
  </div>;
}
