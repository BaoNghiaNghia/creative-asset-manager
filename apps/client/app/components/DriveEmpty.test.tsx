import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ProviderSessions } from "../types";
import { DriveEmpty, sourceLoginRoute } from "./DriveEmpty";

const sessions: ProviderSessions = {
  "google-drive": { authenticated: false, user: null, checking: false },
  sharepoint: { authenticated: false, user: null, checking: false },
};

describe("public Google sign-in", () => {
  it("uses the application login route and keeps Drive connection separate", () => {
    const markup = renderToStaticMarkup(
      <DriveEmpty
        oauthError={null}
        activeProvider="google-drive"
        authByProvider={sessions}
        onSelectProvider={() => undefined}
      />,
    );

    expect(markup).toContain("Sign in with Google");
    expect(markup).not.toContain("/api/auth/google/connect-drive");
  });

  it("uses the Drive connection route after application authentication", () => {
    const markup = renderToStaticMarkup(
      <DriveEmpty
        oauthError={null}
        activeProvider="google-drive"
        authByProvider={sessions}
        onSelectProvider={() => undefined}
        applicationAuthenticated
      />,
    );

    expect(markup).toContain("Connect Google Drive");
    expect(sourceLoginRoute("google-drive", true)).toBe("/api/auth/google/connect-drive");
    expect(sourceLoginRoute("google-drive", false)).toBe("/api/auth/google/login");
  });

  it("offers account switching through the dedicated Drive route", () => {
    const connected: ProviderSessions = {
      "google-drive": { authenticated: true, user: { id: "google-a" }, checking: false },
      sharepoint: { authenticated: false, user: null, checking: false },
    };
    const markup = renderToStaticMarkup(
      <DriveEmpty
        oauthError={null}
        activeProvider="google-drive"
        authByProvider={connected}
        onSelectProvider={() => undefined}
        applicationAuthenticated
      />,
    );
    expect(markup).toContain("Switch Google account");
    expect(sourceLoginRoute("google-drive", true)).toBe("/api/auth/google/connect-drive");
  });
});
