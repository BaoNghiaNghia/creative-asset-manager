interface DesktopIngestionItem {
  id: string; filename: string; relativePath: string; size: number; status: string; bytesUploaded: number; errorCode?: string;
}
interface DesktopIngestionJob {
  id: string; status: string; discovered: number; supported: number; duplicates: number;
  completed: number; failed: number; skipped: number; uploading: number; items: DesktopIngestionItem[];
}
interface Window {
  camDesktop?: {
    isDesktop: true;
    platform: string;
    beginOAuth: (request: { provider?: "google" | "microsoft"; intent?: "google_drive_connect" | "onedrive_connect"; externalSourceId?: string }) => Promise<void>;
    onAuthComplete: (callback: () => void) => () => void;
    ingestion: {
      acceptDrop: (files: FileList, destination: { parentId: string; provider: "google-drive"; externalSourceId?: string }) => Promise<DesktopIngestionJob>;
      chooseFolders: (destination: { parentId: string; provider: "google-drive"; externalSourceId?: string }) => Promise<DesktopIngestionJob | undefined>;
      pause: (jobId: string) => Promise<DesktopIngestionJob>;
      resume: (jobId: string) => Promise<DesktopIngestionJob>;
      cancel: (jobId: string) => Promise<DesktopIngestionJob>;
      retry: (jobId: string, itemId: string) => Promise<DesktopIngestionJob>;
      onProgress: (callback: (job: DesktopIngestionJob) => void) => () => void;
    };
  };
}
