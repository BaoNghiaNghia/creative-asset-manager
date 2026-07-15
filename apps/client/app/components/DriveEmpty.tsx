import type { OAuthErrorState } from "../types";

export function DriveEmpty({ oauthError }: { oauthError: OAuthErrorState }) {
  return <div className="drive-empty">
    {oauthError && <div className="oauth-error">
      <strong>Google sign-in failed</strong>
      <span>{oauthError.message}</span>
      {oauthError.requestId && <small>Request ID: {oauthError.requestId}</small>}
    </div>}
    <span className="drive-empty-icon">◆</span>
    <h1>Connect your Google Drive</h1>
    <p>Sign in with Google to browse your complete folder tree and files.</p>
    <button onClick={() => window.location.assign("/api/auth/google/login")}>Sign in with Google</button>
  </div>;
}
