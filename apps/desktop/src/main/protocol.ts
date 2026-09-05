const TICKET_RE = /^[A-Za-z0-9_-]{32,256}$/;

export type OAuthDeepLink = { ticket: string };

export function parseOAuthDeepLink(value: string): OAuthDeepLink | undefined {
  try {
    const url = new URL(value);
    if (
      url.protocol !== "cam:" ||
      url.hostname !== "oauth-complete" ||
      (url.pathname !== "" && url.pathname !== "/") ||
      url.hash ||
      [...url.searchParams.keys()].length !== 1
    ) {
      return undefined;
    }
    const ticket = url.searchParams.get("ticket");
    return ticket && TICKET_RE.test(ticket) ? { ticket } : undefined;
  } catch {
    return undefined;
  }
}

export function findOAuthDeepLink(argumentsList: readonly string[]): OAuthDeepLink | undefined {
  for (const argument of argumentsList) {
    const parsed = parseOAuthDeepLink(argument);
    if (parsed) return parsed;
  }
  return undefined;
}
