export interface CamDesktopApi {
  isDesktop: true;
  platform: string;
}

declare global {
  interface Window {
    camDesktop?: CamDesktopApi;
  }
}

export {};
