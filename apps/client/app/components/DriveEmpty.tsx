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
    provider: "sharepoint",
    name: "SharePoint",
    description: "Browse SharePoint sites, document libraries and team assets.",
    login: "/api/auth/microsoft/login",
  },
];

export function DriveEmpty({ oauthError, activeProvider, authByProvider, onSelectProvider, applicationAuthenticated = false }: Props) {
  const activeName = activeProvider === "sharepoint" ? "SharePoint" : "Google Drive";

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
          <button onClick={() => connected
            ? onSelectProvider(source.provider)
            : window.location.assign(source.login)}
          >
            {connected ? "Open source" : applicationAuthenticated ? "Connect " + (source.provider === "sharepoint" ? "SharePoint" : "Google Drive") : "Sign in with " + (source.provider === "sharepoint" ? "Microsoft" : "Google")}</button>
          {connected && <span className="source-card-status"><i /> Connected</span>}
        </article>;
      })}
    </div>
  </div>;
}
