# Branding DNA Windows Worker — Windows Portable v0.2.13

## v0.2.13 — No per-shop owner authorization for CAM public storefront pilots

A CAM Shop Library record (including a draft competitor shop) no longer
requires a separate approval from that shop owner or one local approval.json
per shop. Shop status `paused` still blocks the pilot. Source metadata is
not a grant of platform access or of image/artwork reuse rights.

For manually initiated, visible CAM shop pilots, one **marketplace policy
review** can be used for multiple public shops. Keep the private, reviewed
policy files outside the checkout:

```text
%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA\marketplace-access\amazon.json
%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA\marketplace-access\etsy.json
```

Copy the corresponding default-deny `config/marketplace-policy.*.example.json`
file as a starting point. The reviewer must document the marketplace's
current terms and robots policy, a real reviewer and a recent review date.
Amazon additionally requires review of its current Agent Policy for each
listed host in `amazon_reviewed_hosts`; Etsy still
requires actual written authorization **from Etsy** and an explicit list
(`etsy_authorized_shop_urls`) of canonical shop URLs covered by that
authorization. A letter covering one Etsy shop cannot authorize another.
These are platform permissions, **not shop-owner consent**.

The serial `--inspect` command prefers the shared marketplace policy;
older exact-shop approvals are supported as a fallback if no shared file
exists. Invalid shared policies fail closed rather than silently reverting
to an older individual approval. Direct `--approval-file` pilots may point
to the shared policy, but still need an exact CAM shop UUID, separate profile
and report paths. Nothing auto-scans or auto-uploads evidence; production
dispatch remains disabled and 403/429, CAPTCHA and login challenges still
halt immediately. Unverified image rights and sales remain unknown.

## v0.2.12 — Halt safely and preserve local progress

Parser/navigation failures stop the current pilot and serial batch with
`needs_operator`. Only successfully parsed product details count as observations.
Their immutable report is saved before the per-shop SQLite checkpoint; the next
explicit run skips that successful prefix. Stopped reports cannot be prepared
as CAM evidence.

Same-tab and popup products require a fresh main-document response. HTTP 403/429
also stops the run if it arrives during extraction or the visible display hold.
Image and child-frame errors do not replace the product document's status.
Errors use bounded reason codes without saving raw browser exception text.

If Chromium cleanup fails, the report keeps successful observations and records
`browser_cleanup_review_required` / `profile_lock_retained:true`. The profile
lock is kept. An operator must verify that the browser process has stopped
before removing that exact lock and explicitly resuming.

Report and heartbeat versions now come from the shipped package metadata.
The accompanying Scout Manager 0.1.43 source serializes Start/Stop with its
standby supervisor and limits rapid restart attempts. Native Windows tests and
a real approved browser pilot remain separate release acceptance gates.


## v0.2.11 — Remove CAM ownership authorization gating

CAM shop ownership declarations and second-person documentary reviews no longer
gate Shop Registry activation, browser pilot eligibility or access to the normal
public storefront browser. A `draft` shop is a saved source, not an authorization
failure; a deliberately `paused` shop is still withheld from automated pilots.
Existing source labels and documentary history are preserved without granting
any browsing privilege.

External browser automation requirements remain independent: each pilot must
have appropriate marketplace access approval and honor platform-specific
terms; stop on a challenge or access refusal. This change does not enable
production dispatch, marketplace scraping without permission, or bypass tools.



## v0.2.10 — Bounded top-to-bottom browser scroll

For a **separately authorized** Amazon/Etsy shop, the visible Chromium pilot
now scrolls down in up to eight controlled viewport steps to discover product
cards loaded below the fold. It deduplicates observed listing keys, skips
previously completed checkpoint keys, returns to the shop top, clicks visible
product links (same-tab or popup) and reads each bounded product detail.
Only at most 3 shop products per serial batch and 5 per direct pilot invocation
are handled. Each call is finite and must be deliberately started.

These are normal browser UI interactions; the Agent user-agent is always
disclosed. Browsing halts on CAPTCHA, access denied, login or unusual-traffic
warnings. No stealth or proxy rotation, internal APIs, background full-shop
crawl, auto-approval, remote job lease or media download is added. A published
shop URL does not establish authority to automate access.
Etsy automation needs **express written authorization from Etsy**, whether
the shop belongs to the operator or a competitor; Amazon Agent Policy must
be reviewed and respected. A shop with no verified permission remains blocked.



## v0.2.9 — Serial CAM shop browser operator runner

The existing **CAM Shop Library** supplies shop IDs, URLs and their ownership
status. `INSPECT_CAM_SHOPS.cmd` checks each shop without opening any marketplace.
`RUN_CAM_APPROVED_SHOPS.cmd` starts a **finite, explicit, sequential** series
of visible Chromium pilots, up to five independently approved shops per call
(default one shop), max three product pages per shop. It stops the series on
a challenge, redirect error or parser failure rather than moving to the next shop.
Reports and SQLite progress remain isolated by shop and are never uploaded.

Windows checkout `D:\\Desify_Agentic\\scan_pinterest`:
```cmd
node apps\branding-dna-worker\src\cam_shop_batch.mjs --inspect --env-file "scout.local.env"
apps\branding-dna-worker\RUN_CAM_APPROVED_SHOPS.cmd --shop-id SHOP_UUID --env-file "D:\Desify_Agentic\scan_pinterest\scout.local.env"
apps\branding-dna-worker\RUN_CAM_APPROVED_SHOPS.cmd --max-shops 5 --env-file "D:\Desify_Agentic\scan_pinterest\scout.local.env"
```

Private approval file for each exact shop must be placed at
`%LOCALAPPDATA%\\CreativeAssetManager\\BrandingDNA\\cam-shop-pilots\\SHOP_UUID\\approval.json`.
It must reflect reviewed **actual** platform permission, not merely checking
boxes; Etsy requires Etsy's express written permission for browser automation,
and Amazon requires a reviewed current Agent Policy. The runner does not
create approval files, mark any shop as authorized, or bypass an access block.
Current eligibility follows v0.2.11: CAM excludes deliberately paused shops;
draft status and ownership declarations are not exclusion gates. Exact-shop
local platform approval must still be present, valid and unexpired.
The approval file remains private and outside the repository. This operator
runner is NOT the production CAM lease dispatcher, and Start Agent continues
to be standby-only. No scheduled mass scan or remote task execution is enabled.



## v0.2.8 — Visible Chromium product inspection

The manual `visible_click` browser pilot follows product links using
Chromium's actual accessibility/UI links. It handles both same-tab navigation
and new tabs (`target=_blank`), safely closes product popups, and leaves each
product on screen for 4 seconds after inspection. Login/invalid-domain
redirects are refused without bypass or direct HTTP fallback. Browser identity
is still explicitly `Agent/CreativeAssetManagerBrandDNA`; no human
impersonation, anti-bot evasion or hidden API.

The `Start agent` action in Scout Manager is a CAM heartbeat and does not
automatically run this manual browser pilot. The latter still needs
shop-specific authorization, `headless:false`,
`interaction_mode:"visible_click"`, and is limited to 1–5 products per
manual run. Production dispatch is not enabled.

## CAM paired Agent readiness diagnostics (2026-10-21)

Run `node src/start_cam_paired_agent.mjs --once` from the existing Windows
`scan_pinterest` checkout to check the paired CAM Shop Library and its
read-only dispatch blockers. The output includes `blocked_reasons`, a
count of shops per validated blocker code. In continuous `--run` mode,
the `cam_heartbeat` log includes the same aggregate counts. Shop URLs,
bearer tokens, approval documents, and listing details are never logged.
The existing Scout pairing is reused; no second local shop registry is
introduced.

## CAM Shop Library → resumable browser pilot (2026-10-21)

A separately and explicitly approved shop can use the existing Windows browser
pilot with durable progress while taking its URL exclusively from CAM:

```powershell
node apps/branding-dna-worker/src/cam_shop_pilot.mjs --resume `
  --shop-id "SHOP_UUID_FROM_CAM" `
  --approval-file "C:\\CAM-Private\\amazon-shop-approval.json" `
  --profile-dir "C:\\CAM-Private\\chrome-amazon-shop" `
  --report-dir "C:\\CAM-Private\\reports-amazon-shop" `
  --env-file "scout.local.env"
```

Run only on an authorized **Windows** machine with Node 22.13+, pinned
Playwright/Chromium and an individually reviewed approval document. This mode
reuses the immutable report + per-shop SQLite checkpoint from the preexisting
local pilot. Successfully observed listing keys are skipped on the next
*explicit manual* run. Partial/blocked observations are never treated as
completed. Reports are saved locally before checkpoint acknowledgment; after a
crash, the next manual run reconciles them without deleting previous reports.
Nothing uploads product media or unverified observations to CAM. The local
approval remains mandatory for every invocation (Etsy requires documented
written permission; Amazon requires Agent Policy review).

The legacy `--report ABS_PATH` behavior remains available for one-off
observation handoffs; `--resume --report-dir ABS_PATH` is a distinct opt-in
mode and never schedules itself. One run stays capped at five product pages;
it does not claim full-shop coverage.

These diagnostics **do not create a scan job or open Amazon/Etsy**. As long
as CAM reports `scan_task_dispatch_not_deployed` or
`marketplace_authorization_not_verified`, the worker remains
`connected_waiting`. A real production scan requires a separate
audited dispatcher and platform-specific authorization; an operator's
request to start the agent is not permission to bypass those gates.

## Recommended: use the existing Scan Pinterest tool

Use the existing paired Scout Manager checkout. Open `START_SCOUT_TAURI.cmd`
and use the Branding DNA card's Start/Stop and dedicated live logs.
Start maintains CAM standby; use the explicit shop commands above for a finite
browser pilot. The 0.1.43 source adds bounded standby supervision and still
requires native Windows validation before upgrading the installed Manager.

The pilot keeps its own Chromium profile and per-shop SQLite progress,
separate from Pinterest. Browser runtime remains on the authorized Windows
host; it is not installed on CAM's VPS.

## Release / build mới (Windows)

**Mã nguồn Windows Portable Bootstrap v0.2.12**, đóng gói thành
`BrandingDNA-BrowserAgent-v0.2.12-win-x64.zip`. Bản sửa cần qua kiểm thử
Windows trước khi xác nhận đã triển khai. Đây là
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
- `CHECK_TOOL.cmd` kiểm tra runtime. `START_CAM_AGENT.cmd` và tên tương thích
  `START_BROWSER_PILOT.cmd` chỉ giữ kết nối CAM bằng pairing hiện có.
  Chúng không duyệt sản phẩm. Dùng `RUN_CAM_APPROVED_SHOPS.cmd` hoặc
  `cam_shop_pilot.mjs --resume` để chủ động chạy pilot của shop đủ điều kiện.
  Mỗi batch tối đa 3 sản phẩm/shop; một pilot trực tiếp tối đa 5 sản phẩm.
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