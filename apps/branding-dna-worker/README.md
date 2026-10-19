# Branding DNA Windows Worker — Windows Portable v0.2.6"

## Recommended: use the existing Scan Pinterest tool

This worker is now **integrated in Scout Manager v0.1.21** using the
`D:\Bot_Tool_Auto_Game\scan_pinterest` checkout. Do not install a second
tool. Open `START_SCOUT_TAURI.cmd`, use the Branding DNA card's
Install/Config/Check/Run/Stop and dedicated Live logs. All Pinterest modes
remain unaffected. The v0.2.0 ZIP is retained only as a fallback.
The v0.2.4 source adds optional visible UI clicks and per-shop durable
SQLite progress to the same Manager,
without installing any Chromium/Scout runtime on CAM's VPS.

## Release / build mới (Windows)

**Bản Windows Portable Bootstrap v0.2.4**, dự kiến GitHub Windows CI build
`BrandingDNA-BrowserAgent-v0.2.4-win-x64.zip`. Chưa được cài trên máy Windows. Đây là
gói cài + launcher `.cmd`, **không phải EXE đã kèm Node/Chromium**. Đọc
`QUICK_START_VI.txt` trong ZIP trước khi cài. ZIP và file `.zip.sha256`
chỉ có thể dùng khi workflow build/release trên GitHub hoàn thành;
source code và phiên bản trong repo không tự chứng minh artefact đã xuất bản.

- Giải nén ZIP vào thư mục Windows riêng; không trộn với Pinterest Scout.
- Cài **Node.js 22.13+**, chạy `INSTALL_TOOL.cmd` (lần đầu tải đúng
  Playwright 1.56.1 và Chromium, kiểm tra runtime). Đây là hoạt động cài
  phần mềm, không truy cập marketplace hoặc kích hoạt scan.
- Sửa file `config/pilot.local.json` từ bản mẫu; chỉ chứa shop URL, nền tảng,
  đường dẫn *tuyệt đối* tới file quyền riêng, profile và reports ở **bên ngoài**
  thư mục tool. `config/approval.example.json` chỉ là mẫu tất cả các quyền
  đều tắt, tuyệt đối không nhầm với giấy phép của Etsy/Amazon.
- `CHECK_TOOL.cmd` kiểm tra máy, không duyệt web; `START_BROWSER_PILOT.cmd`
  chỉ chạy sau khi xác minh đủ điều kiện đúng shop, bản phê duyệt còn hiệu lực.
  Mỗi job pilot tối đa 5 trang sản phẩm, dừng khi có CAPTCHA/login/block.
- `START_BRANDING_DNA_WORKER.cmd` giữ nguyên chế độ **heartbeat only**. Không
  có phát job production hay kết nối API của Etsy/Amazon.
- Nâng cấp: giải nén bản mới trong thư mục riêng. Đừng xóa profile, reports,
  quyền và cấu hình cũ; sao chép cấu hình cần thiết thủ công, không copy token
  hoặc file quyền vào Git/release.

Build reproducible từ Linux/Windows có Python 3:
`python3 apps/branding-dna-worker/scripts/build_windows_release.py`.
Lệnh tự verify SHA-256 từng member. Có thể kiểm lại bằng `--verify ZIP_PATH`.
Bản đóng gói từ Linux cần thêm **Browser QA thực tế trên Windows** trước khi
coi là bản chạy production.

# Branding DNA Windows Worker — Heartbeat + manual Browser Pilot

The default process sends signed **heartbeats and restricted lifecycle telemetry** with a durable SQLite outbox. A separate **manual local browser pilot** can navigate an individually approved Amazon/Etsy shop with Chromium/Playwright. `npm start` does not run it. Production scanner dispatch remains disabled; the pilot does not upload evidence to CAM.

## Setup (on an authorized dedicated Windows machine)

1. Install Node.js **22.13+** (required for built-in `node:sqlite`), pin the runtime, and copy this worker directory into its own Windows directory. Avoid using Pinterest Scout's Chrome profile or process.
2. In CAM Branding DNA → Dedicated Windows Worker, register an agent. The API shows an **agent ID and token once**. Store them in an OS-protected secret vault. Do not commit or log the token.
3. Inject the three environment variables only into the worker process: `BRANDING_DNA_SERVER_URL`, `BRANDING_DNA_AGENT_ID`, `BRANDING_DNA_AGENT_TOKEN`. Do not place secrets in the .cmd, repository or public configuration.
4. Run `npm test`, then `npm start` or the .cmd. CAM should show heartbeat/online within ~1 minute. To stop immediately revoke the token via Disable in CAM.

## Browser pilot v0.2.4 — Conservative bounded navigation cooldown

The agent uses **traffic pacing**, not human impersonation: configure
`minimum_delay_ms: 10000` and `maximum_delay_ms: 18000` for a
bounded 10–18 second pause before landing-page navigation, product clicks
and return navigation. The cryptographically sampled cooldown never goes
below the minimum or above the maximum. If no maximum is specified, the
pilot defaults to `minimum + 8000` (capped at 60 seconds) to remain
compatible with existing Scout Manager shop-library configs.
Do not automate keystroke patterns, movement, CAPTCHA or throttling
circumvention. The Agent user-agent stays disclosed and HTTP 403/429
stops the pilot for manual review.

## Browser pilot v0.2.4 — Visible UI + durable local resume, Windows only

Repeated **explicitly started**, individually authorized pilots resume via a
per-shop SQLite checkpoint in `<report_dir>/.bdna-local-progress/`. Each
pilot remains limited to 1–5 product pages on the *visible landing page*.
A valid saved report is written before recording its product keys. A restart
reconciles immutable reports and skips successfully observed product keys.
Blocked pages and failed product details are **not** counted as completed;
a missing new visible link prompts operator review, not a false claim of
complete shop coverage. The local ledger holds IDs and hashes, never image
bytes, credentials, cookies, or training rights. Separate shops stay isolated
even when legacy configurations use one report folder.

Do not delete `%LOCALAPPDATA%\\CreativeAssetManager\\BrandingDNA` during
updates. The existing Scout Manager remains the single interface. No
scheduled scanning, multi-page pagination or CAM production dispatcher is
enabled; Amazon/Etsy authorization is checked again **each** manual run.

## Browser pilot v0.2.1 — Visible UI agent, Windows only

- Trong Scout Manager hiện tại, bật **Show Chromium browser + click product links**.
  Node pilot tự chọn `visible_click` khi `headless=false` (không sửa config cũ).
- Nếu chạy gói portable trực tiếp, đặt `"headless": false` và
  `"interaction_mode": "visible_click"` trong file config riêng trên Windows.
- Agent quan sát các link sản phẩm hiển thị qua accessibility tree, bấm
  link bằng Playwright, quay lại trang shop và tiếp tục; không gọi trang
  chi tiết bằng `page.goto` trong chế độ này.
- Sử dụng khoảng chờ ngẫu nhiên có giới hạn để giãn tải, KHÔNG giả lập
  nhịp thao tác người thật nhằm tránh phát hiện; giữ định danh
  `Agent/CreativeAssetManagerBrandDNA`, không bypass CAPTCHA.
- Windows lưu Chromium, profile, session và báo cáo dưới
  `%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA`; CAM VPS chỉ giữ
  Shop Registry, heartbeat và dữ liệu đã kiểm chứng. Backend release bỏ
  toàn bộ thư mục ứng dụng Windows; GitHub giữ source/CI.
- Không chạy browser thật trên Linux VPS; chỉ cho phép test mock browser.
  Một pilot vẫn tối đa 5 sản phẩm, không có full-shop dispatch.
- **Etsy**: cần quyền tự động hóa bằng văn bản của Etsy; **Amazon**: tuân thủ
  Agent Policy, công khai danh tính và dừng khi bị hạn chế truy cập.

## Browser pilot — opt-in, local only

The package now pins Playwright **1.56.1**. On a dedicated authorized Windows host run `npm ci` and `npx playwright install chromium`; no browser binary is shipped or installed by the production heartbeat command. Launch manually via `node src/browser_pilot.mjs` only with an exact-shop, freshly approved authorization file and a dedicated profile. **Etsy requires express written authorization; Amazon requires Agent Policy compliance and agent identification.** Bounded to 5 product pages, no login/CAPTCHA bypass, no marketplace API calls. See `docs/features/branding-dna/BROWSER_AGENT_PILOT.md` for permission requirements, setup and example.

Node 22 marks `node:sqlite` experimental; validate the pinned runtime on the dedicated machine. Heartbeat success does not imply a productive scanner.

## Durable lifecycle telemetry (not scanner evidence)

- Windows database: `%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA\<agent-id>\worker-outbox.sqlite`. On Linux QA hosts, `$XDG_STATE_HOME` or `~/.local/state`. Dedicated per-agent directory, symlink file rejection.
- Local SQLite WAL + `synchronous=FULL` writes; each lifecycle event is committed before delivery and deleted only after an **exact complete ACK** from CAM. Max 2,000 pending rows, batch up to 50; outbox-full fails closed without discarding old events.
- Accepted events are `started`, `stopped`, `heartbeat_failure`, `heartbeat_recovered` with fixed detail codes. No arbitrary messages, credentials, shop URLs, cookies, images, or user content can be sent.
- Server event endpoint `POST /api/v1/branding-dna/workers/{agent_id}/events` authenticates the existing agent Bearer token, tenant-scopes events, handles replay via event ID + digest, rejects conflicting duplicates, limits new events to 1,000 per agent/day and accepts timestamps at most 30 days old.
- Connectivity failures and invalid ACKs leave rows pending. HTTP redirect following disabled and 15s request timeout. Revoked enrollment fails closed.
- **Do not confuse this lifecycle outbox with the future per-task result outbox**. No scanner jobs are dispatched. Production database migration and Windows installer/update have not been performed. Node's built-in SQLite carries an experimental warning on Node 22 and requires a pinned runtime QA pass.

## Required upgrades for scanning

Tenant-bound lease-to-worker dispatch, browser selector fixtures against permitted live sources, task/result SQLite outbox, media rights and signed upload, operator pause/kill, retention, Windows Browser QA and canary rollback. The browser pilot and lifecycle outbox are implemented separately, but production job dispatch is intentionally disabled. See `docs/features/branding-dna/WORKER_ARCHITECTURE.md`.
