"""Patchright persistent context launcher: Explicit proxy and anti-detection parameters."""
from dataclasses import dataclass
from pathlib import Path

import config

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
]


async def launch_account_context(p, account: str, headless: bool = None, use_extension: bool = False):
    """Launches accounts/<account> profile, returns BrowserContext. Caller must close.

    p: async_playwright() instance
    headless: None = uses config.HEADLESS
    """
    profile_dir = Path(config.PROFILE_DIR) / account
    if not profile_dir.exists():
        raise FileNotFoundError(
            f"Account profile does not exist: {profile_dir} (run python add_account.py {account} first)"
        )
    launch_headless = config.HEADLESS if headless is None else headless
    args = list(LAUNCH_ARGS)
    if use_extension:
        if not config.EXTENSION_ENABLED:
            raise RuntimeError("Dola extension is disabled (DOLA_EXTENSION_ENABLED=0)")
        extension_dir = Path(config.EXTENSION_DIR).resolve()
        if not extension_dir.exists():
            raise FileNotFoundError(f"Dola extension directory does not exist: {extension_dir}")
        # Chromium debugger extension requires headed window to intercept skill/action-bar responses
        launch_headless = False
        args.extend([
            f"--disable-extensions-except={extension_dir}",
            f"--load-extension={extension_dir}",
        ])
    kwargs = {
        "headless": launch_headless,
        "args": args,
        "locale": "ja-JP",
        "timezone_id": "Asia/Tokyo",
    }
    if config.PROXY:
        kwargs["proxy"] = {"server": config.PROXY}
    return await p.chromium.launch_persistent_context(str(profile_dir), **kwargs)


def cookie_value(cookies: list, name: str) -> str:
    """Extracts cookie value from context.cookies() result."""
    return next((c["value"] for c in cookies if c["name"] == name and c["value"]), "")


@dataclass(frozen=True)
class SessionVerificationResult:
    session_cookie_present: bool
    authenticated_ui_present: bool
    login_page_detected: bool
    verified: bool

async def verify_dola_session(context, page=None) -> SessionVerificationResult:
    """Validate session cookie and authenticated UI; never return cookie data."""
    try:
        page = page or (context.pages[0] if context.pages else await context.new_page())
        await page.goto("https://www.dola.com/chat", timeout=60000, wait_until="domcontentloaded")
        await page.wait_for_timeout(1000)
        cookies = await context.cookies("https://www.dola.com")
        cookie = bool(cookie_value(cookies, "sessionid"))
        authenticated = bool(await page.evaluate("""() => !!(
            document.querySelector('textarea') || document.querySelector('[contenteditable="true"]'))"""))
        login_page = bool(await page.evaluate("""() => !!(
            document.querySelector('input[type="password"]') || document.querySelector('[data-testid*="login"]'))"""))
        return SessionVerificationResult(cookie, authenticated, login_page, cookie and authenticated)
    except Exception:
        return SessionVerificationResult(False, False, False, False)

async def check_login_state(account: str) -> bool:
    """Independent headless verification using the canonical persistent profile."""
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        context = await launch_account_context(p, account)
        try:
            return (await verify_dola_session(context)).verified
        finally:
            await context.close()
