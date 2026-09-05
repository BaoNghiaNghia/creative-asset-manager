interface Window {
  camDesktop?: {
    isDesktop: true;
    platform: string;
    beginOAuth: (request: { provider?: "google" | "microsoft"; intent?: "google_drive_connect" | "onedrive_connect"; externalSourceId?: string }) => Promise<void>;
    onAuthComplete: (callback: () => void) => () => void;
  };
}
