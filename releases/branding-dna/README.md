# Branding DNA Browser Agent — Windows v0.2.0

**Download:** [BrandingDNA-BrowserAgent-v0.2.0-win-x64.zip](./BrandingDNA-BrowserAgent-v0.2.0-win-x64.zip) (34 KB portable bootstrap).

**SHA-256:** `da3854dc9aeadc6e9c379f5514cd957152695c81ef4fc0458f92c9a37aa32510`.
Checksum text: [ZIP SHA256](./BrandingDNA-BrowserAgent-v0.2.0-win-x64.zip.sha256).

This is a **Windows CMD launcher package**, not a signed/native EXE and not a
fully offline build. Node.js **22.13+** and the Chromium browser still need
installation on Windows via `INSTALL_TOOL.cmd`. The package contains
`QUICK_START_VI.txt` and default-deny configuration examples.

**Verified on Linux builder:** 25/25 worker Node tests, 3/3 release-integrity
tests, deterministic SHA-256 archive, and `npm ci + npm test` from extracted ZIP.
**Not verified:** Windows CMD execution, Chromium installation on the user's
Windows PC, real permitted Amazon/Etsy navigation, end-to-end production
leasing/upload or production deployment. Worker heartbeat and production
scanner dispatch remain separate; scanning is not automatically enabled.

Release archive contains no real user approval, marketplace token, cookie or
Chrome profile. Etsy browser scanning remains gated by the required written
authorization and Amazon by agent policy/authorization review.
