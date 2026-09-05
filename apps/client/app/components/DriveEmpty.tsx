import type { OAuthErrorState, Provider, ProviderSessions } from "../types";
import { DriveIcon, SharePointIcon } from "./Icons";

type Props = {
  oauthError: OAuthErrorState;
  activeProvider: Provider;
  authByProvider: ProviderSessions;
  onSelectProvider: (provider: Provider) => void;
  applicationAuthenticated?: boolean;
};

const sources: Array<{
  provider: Provider;
  name: string;
  description: string;
  login: string;
}> = [
  {
    provider: "google-drive",
    name: "Google Drive",
    description: "Browse My Drive folders, creative files and shared assets.",
    login: "/api/auth/google/login",
  },
  {
    provider: "onedrive",
    name: "OneDrive",
    description: "Browse My Files and folders from the connected OneDrive.",
    login: "/api/auth/microsoft/connect-onedrive",
  },
  {
    provider: "sharepoint",
    name: "SharePoint",
    description: "Browse SharePoint sites, document libraries and team assets.",
    login: "/api/auth/microsoft/connect-sharepoint",
  },
];

export function sourceLoginRoute(provider: Provider, applicationAuthenticated: boolean): string {
  if (provider === "google-drive") return applicationAuthenticated ? "/api/auth/google/connect-drive" : "/api/auth/google/login";
  if (!applicationAuthenticated) return "/api/auth/microsoft/login";
  return provider === "onedrive" ? "/api/auth/microsoft/connect-onedrive" : "/api/auth/microsoft/connect-sharepoint";
}

function beginApplicationLogin(provider: Provider, applicationAuthenticated: boolean): boolean {
  if (!window.camDesktop || provider === "sharepoint") return false;
  void window.camDesktop.beginOAuth(applicationAuthenticated
    ? { intent: provider === "google-drive" ? "google_drive_connect" : "onedrive_connect" }
    : { provider: provider === "google-drive" ? "google" : "microsoft" });
  return true;
}

export function DriveEmpty({ oauthError, activeProvider, authByProvider, onSelectProvider, applicationAuthenticated = false }: Props) {
  const activeName = activeProvider === "onedrive" ? "OneDrive" : activeProvider === "sharepoint" ? "SharePoint" : "Google Drive";

  return <div className="drive-empty source-onboarding">
    {oauthError && <div className="oauth-error">
      <strong>{activeName} sign-in failed</strong>
      <span>{oauthError.message}</span>
      {oauthError.requestId && <small>Request ID: {oauthError.requestId}</small>}
    </div>}

    <span className="onboarding-kicker">CREATIVE ASSET SOURCES</span>
    <h1>Connect a cloud source</h1>
    <p>Choose where your assets live. You can connect both sources and switch between them anytime.</p>

    <div className="source-cards">
      {sources.map(source => {
        const connected = authByProvider[source.provider].authenticated;
        return <article className="source-card" key={source.provider}>
          <span className={"source-card-icon " + source.provider}>
            {source.provider === "sharepoint" ? <SharePointIcon /> : <DriveIcon />}
          </span>
          <div>
            <strong>{source.name}</strong>
            <small>{source.description}</small>
          </div>
          <button onClick={() => {
            if (connected) onSelectProvider(source.provider);
            else if (!beginApplicationLogin(source.provider, applicationAuthenticated)) {
              window.location.assign(sourceLoginRoute(source.provider, applicationAuthenticated));
            }
          }}
          >
            {connected ? "Open source" : applicationAuthenticated ? "Connect " + source.name : "Sign in with " + (source.provider === "google-drive" ? "Google" : "Microsoft")}</button>
          {connected && source.provider === "google-drive" && applicationAuthenticated && <button
            className="source-card-secondary-action"
            onClick={() => window.location.assign("/api/auth/google/connect-drive")}
          >Switch Google account</button>}
          {connected && <span className="source-card-status"><i /> Connected</span>}
        </article>;
      })}
    </div>
  </div>;
}
