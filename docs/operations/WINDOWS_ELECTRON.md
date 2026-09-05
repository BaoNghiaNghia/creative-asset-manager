# Windows Electron application

## Internal Windows package

The Windows desktop application is an **UNSIGNED INTERNAL BUILD**. It is not a production distribution and Windows SmartScreen may show a warning until code signing is introduced.

### Build prerequisites

Use Windows x64 with Node.js 22.14.0 or later. From `apps/desktop` run:

```powershell
npm ci
npm run package:win
```

The installer is written to:

```
apps/desktop/release/Creative Asset Manager Setup 0.1.0.exe
```

The local package command never publishes a GitHub release.

## Runtime configuration

Development defaults to `http://localhost:5173`. Packaged builds default only to the public HTTPS CAM origin `https://creative-assets.ddns.net`.

`CAM_DESKTOP_URL` may override that origin only with a valid HTTPS absolute URL. Remote HTTP, file URLs, and malformed values fail closed. Do not package secrets or .env files; OAuth and server credentials remain on CAM services.

## Installer behavior

The NSIS installer is per-user, assisted, and installs under the normal LocalAppData Programs location. It creates Start Menu and Desktop shortcuts and registers `cam://` through electron-builder. Uninstall removes installed files, shortcuts, and protocol registration; it does not delete server data or local Electron userData automatically.
The Windows icon is generated from the repository-approved client app-icon-512.png asset.

The app identity is `com.creativeassetmanager.desktop`. The app retains the single-instance lock. A second launch focuses the existing window; a `cam://oauth-complete?ticket=...` callback is delivered to its strict Main-process parser.

No auto-update is included. Future signing must use CI secret storage (for example Windows certificate data and password), never committed keys.

## Required manual Windows smoke test

1. Run the Setup executable and launch Creative Asset Manager from Start Menu.
2. Confirm the configured HTTPS CAM origin loads and a second launch focuses the existing app.
3. Complete non-production Google and Microsoft application login using the system browser and confirm `cam://` focuses Electron.
4. From a Google login connect OneDrive, then from Microsoft login connect Google Drive; verify application identity remains unchanged.
5. Drag ten supported images from Windows Explorer; confirm hashing, duplicate preflight, queueing, upload, and asset creation without exposing absolute paths.
6. Drop a Campaign folder containing nested Meta and TikTok images; verify recursive safe-relative-path scanning and no symlink escape.
7. Drop the same set again and verify tenant-local duplicate skipping.
8. Navigate Assets, Search, Sources, Assets during a multi-file upload; the Main-process queue must continue.
9. Minimize during upload, then test close behavior; no silent destructive termination.
10. Verify a normal browser upload still succeeds.

Native installation, OAuth, drag/drop, and service-backed pipeline smoke tests are required before production distribution. They are not satisfied by Linux or WSL packaging.
