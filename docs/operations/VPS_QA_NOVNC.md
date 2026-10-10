# Dedicated QA noVNC desktop (VPS)

> This is a controlled, on-demand *QA-only* X display. It does not install a full desktop,
> start Google Chrome automatically, touch creative assets, or alter production API/workers.
> Operator runs privileged commands manually on the VPS after reviewing the scripts.

## Preconditions and dry inspection

SSH to the Creative Asset Manager VPS and enter a repository with the updated scripts.
Run as the system administrator, **not inside a WSL shell on the Windows machine**.

```bash
cd /root/creative-asset-manager
git pull --ff-only origin main
bash scripts/cam-qa-disk-audit.sh --audit
bash scripts/cam-qa-desktop-setup.sh --status
```

The disk-audit script only reads data by default. Its **optional** `--clean-safe` mode removes downloaded APT .deb cache and archives of systemd journal logs older than 14 days. It never removes database, Docker data, current/previous frontend/backend releases, media, Google Drive/R2 files, Stage 1/2/3/4/5 artifacts, or pinned/approved Browser QA baselines. Review inventories first.

## Install swap and private desktop

```bash
sudo bash scripts/cam-qa-disk-audit.sh --clean-safe
sudo bash scripts/cam-qa-desktop-setup.sh --install
```

Installer preflights >=5 GiB free disk before allocating its 2 GiB swapfile at `/swapfile-camqa`. It activates swap and records it in fstab. It installs only Xvfb/Openbox/x11vnc/noVNC/websockify/xauth, creates locked non-root user `cam-qa`, and writes systemd units for display `:90`. It does **not** start them automatically or enable them at boot. X display is protected by a private Xauthority cookie, and both VNC TCP sockets listen on loopback only.

Create the private VNC password interactively on the VPS. Never paste it into chat or shell history:

```bash
sudo -u cam-qa x11vnc -storepasswd /home/cam-qa/.vnc/passwd
sudo bash scripts/cam-qa-desktop-setup.sh --start
sudo bash scripts/cam-qa-desktop-setup.sh --status
```

Verify noVNC on `127.0.0.1:6080` and x11vnc on `127.0.0.1:5901`, **never** on `0.0.0.0` or the public VPS IP. Do not add an Nginx/public HTTPS route for noVNC.

## Windows access (no WSLg required)

Open Windows PowerShell, replace USER and VPS_IP:

```powershell
ssh -N -L 127.0.0.1:6080:127.0.0.1:6080 USER@VPS_IP
```

Keep this SSH tunnel running. From any regular Windows browser, open:
`http://127.0.0.1:6080/vnc.html`. This is **noVNC**, not the provider's serial console showing kernel logs.

## Open Chrome and sign in manually

On a **second** VPS SSH terminal:

```bash
sudo -iu cam-qa
export DISPLAY=:90
export XAUTHORITY="$HOME/.Xauthority"
google-chrome-stable --user-data-dir="$HOME/.config/cam-qa-chrome-profile" \
  "https://creative-assets.ddns.net/ai-operations"
```

Chrome must be run as cam-qa (never as root, never with --no-sandbox).
In noVNC, complete Google authentication manually in this regular Chrome instance.
Google may reject data-center IPs despite the normal browser; do not attempt security bypasses.
After AI Operations is visible, **fully close QA Chrome**, including any remaining profile processes, before capture.

The QA capture script `production-ui-auth-capture-chrome.mjs` requires Playwright dependencies. On the VPS, install the repository's locked JavaScript dependencies in its trusted checkout as needed; avoid placing user credentials in the repository.

```bash
# From a trusted checkout where cam-qa has read access and Playwright is installed:
sudo -iu cam-qa
export DISPLAY=:90
export XAUTHORITY="$HOME/.Xauthority"
node /srv/creative-asset-manager-source/apps/client/scripts/production-ui-auth-capture-chrome.mjs \
  --url https://creative-assets.ddns.net \
  --profile-dir "$HOME/.config/cam-qa-chrome-profile" \
  --chrome google-chrome-stable \
  --output "$HOME/cam-production-ui-session.json"
stat -c '%a %n' "$HOME/cam-production-ui-session.json"
```

Confirm mode 600, then as root copy into `/etc/creative-asset-manager/production-ui-storage-state.json` with owner root and mode 600. Delete the transfer copy only after authenticated QA passes, and keep the dedicated Chrome profile private.

```bash
sudo install -o root -g root -m 600 \
  /home/cam-qa/cam-production-ui-session.json \
  /etc/creative-asset-manager/production-ui-storage-state.json
cd /root/creative-asset-manager
sudo env CAM_PRODUCTION_UI_MODE=strict make production-ui-smoke
```

A passed public-only check is not a passed authenticated check. Do not forward or share the JSON session file.

## End the QA session

```bash
sudo bash scripts/cam-qa-desktop-setup.sh --stop
```

Close your SSH tunnel. The desktop units remain disabled at boot by design. Do not stop an unrelated Xvfb session owned by a production/Scout service.

### Resource safety

The VPS previously reported ~7.8 GiB RAM, ~2.1 GiB available, 0 swap, and ~7.2 GiB free disk, plus historical OOM kills. Chrome can use a substantial share of this memory. Run QA during a quiet window, monitor with `free -h`, `swapon --show`, and do not keep Chrome running. The 2 GiB swapfile is a safety buffer, not a substitute for adequate RAM. **No commands in this document reboot the VPS or stop production workers.**
