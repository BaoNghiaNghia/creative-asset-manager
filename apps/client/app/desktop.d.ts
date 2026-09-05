interface Window {
  camDesktop?: {
    isDesktop: true;
    platform: string;
    beginOAuth: (request: { provider: "google" | "microsoft" }) => Promise<void>;
    onAuthComplete: (callback: () => void) => () => void;
  };
}
