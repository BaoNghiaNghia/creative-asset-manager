import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ProviderSessions } from "../types";
import { DriveEmpty } from "./DriveEmpty";

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
});
