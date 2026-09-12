"""Interactive Google OAuth login for account profile setup.

Usage: python add_account.py [account_name]

Only an account name may be supplied on argv. Credentials are collected through
protected interactive prompts and are never printed.
"""
import asyncio
import base64
import hashlib
import hmac
import getpass
import struct
import sys
import time
from pathlib import Path

from patchright.async_api import async_playwright

from browser import LAUNCH_ARGS
import config


def totp(secret: str, period: int = 30, digits: int = 6) -> str:
    """Standard TOTP (RFC 6238), Google Authenticator compatible."""
    secret = secret.replace(" ", "").upper()
    key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
    counter = int(time.time() // period)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[19] & 15
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7fffffff) % (10 ** digits)
    return str(code).zfill(digits)


async def google_login(g, email: str, password: str, secret: str):
    """Executes Google OAuth login state machine until callback."""
    for step in range(12):
        await g.wait_for_timeout(2500)
        if "accounts.google.com" not in g.url:
            print("[google] Redirected out of Google domain (OAuth callback)", flush=True)
            return
        # 1) Account chooser page
        if "accountchooser" in g.url:
            acc = g.locator(f"text={email}").first
            if await acc.count() and await acc.is_visible():
                await acc.click(timeout=5000)
                print("[google] Account chooser page -> clicked account", flush=True)
                continue
        # 2) Email page
        identifier = g.locator("#identifierId").first
        if await identifier.count() and await identifier.is_visible():
            await identifier.fill(email)
            # Use DOM click to safely trigger submit event
            await g.locator("#identifierNext").evaluate("e => e.click()")
            print("[google] Email page -> submit", flush=True)
            await g.wait_for_timeout(1500)
            continue
        # 3) Password page
        pwd = g.locator('input[name="Passwd"]').first
        if await pwd.count() and await pwd.is_visible():
            await g.wait_for_timeout(600)
            await pwd.fill(password)
            await g.locator("#passwordNext").evaluate("e => e.click()")
            print("[google] Password page -> submit", flush=True)
            await g.wait_for_timeout(1500)
            continue
        # 4) Consent page
        clicked = False
        for sel in ["#submit_button",
                    "[role='button']:has-text('続行')", "button:has-text('続行')",
                    "[role='button']:has-text('继续')", "button:has-text('继续')",
                    "[role='button']:has-text('Continue')", "button:has-text('Continue')"]:
            try:
                loc = g.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=3000)
                    print("[google] Consent page -> submitted", flush=True)
                    clicked = True
                    break
            except Exception:
                continue
        if clicked:
            continue
        # 5) 2FA page
        if await g.locator('input[type="tel"]').count():
            if not secret:
                raise RuntimeError("Google requested 2FA, but no TOTP secret was provided")
            code = totp(secret)
            print("[google] 2FA page -> submitted", flush=True)
            await g.fill('input[type="tel"]', code)
            await g.click("#totpNext")
            continue
        # 6) Dola 18+ age confirmation popup
        if await g.locator("text=18").count():
            ok = await g.evaluate("""() => {
                const els = [...document.querySelectorAll('button, [role="button"], div, span')];
                const t = els.find(e => (e.textContent || '').trim() === 'OK' && e.childElementCount === 0);
                if (t) { t.click(); return true; }
                return false;
            }""")
            print(f"[dola] Age confirmation -> JS click OK = {ok}", flush=True)
            await g.wait_for_timeout(1500)
            continue
        print(f"[google] step{step} unrecognized authentication page", flush=True)
    if "accounts.google.com" in g.url:
        await _save_debug_screenshot(g, "dbg_google2.png")
        raise RuntimeError("Google login did not complete within 12 steps (saved dbg_google2.png)")


async def _save_debug_screenshot(page, name: str) -> None:
    """Persist diagnostics privately without exposing page contents in logs."""
    target = Path(config.ARTIFACTS_DIR) / name
    await page.screenshot(path=str(target))
    target.chmod(0o600)


async def add_account_flow(account: str, email: str, password: str, secret: str) -> bool:
    """Full account addition flow; returns True on success."""
    profile_dir = Path(config.PROFILE_DIR) / account
    profile_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
    profile_dir.chmod(0o750)

    async with async_playwright() as p:
        kwargs = {"headless": False, "args": LAUNCH_ARGS,
                  "locale": "ja-JP", "timezone_id": "Asia/Tokyo"}
        if config.PROXY:
            kwargs["proxy"] = {"server": config.PROXY}
        context = await p.chromium.launch_persistent_context(str(profile_dir), **kwargs)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://www.dola.com/chat", timeout=60000)
            await page.wait_for_timeout(4000)

            cookies = await context.cookies("https://www.dola.com")
            if any(c["name"] == "sessionid" and c["value"] for c in cookies):
                print(f"[{account}] Active session exists, login not required", flush=True)
                return True

            # Click login entry -> Google option
            try:
                await page.wait_for_selector(".semi-modal-wrap", timeout=10000)
            except Exception:
                await page.locator("text=ログイン").first.click(timeout=10000)
                await page.wait_for_selector(".semi-modal-wrap", timeout=10000)
            await page.locator("text=Googleで続ける").first.click(timeout=10000)

            # Google page may open in popup or active tab
            await page.wait_for_timeout(3000)
            g = next((pg for pg in context.pages if "accounts.google.com" in pg.url), None)
            if g is None and "accounts.google.com" in page.url:
                g = page
            if g is None:
                await _save_debug_screenshot(page, "dbg_add_account.png")
                raise RuntimeError("Failed to redirect to Google login page (saved dbg_add_account.png)")

            await google_login(g, email, password, secret)

            # Wait for Dola sessionid after OAuth callback
            for _ in range(60):
                await page.wait_for_timeout(3000)
                cookies = await context.cookies("https://www.dola.com")
                if any(c["name"] == "sessionid" and c["value"] for c in cookies):
                    print(f"[{account}] ✓ Login successful, sessionid saved to {profile_dir}", flush=True)
                    await page.wait_for_timeout(3000)
                    return True
            await _save_debug_screenshot(page, "dbg_add_account.png")
            raise RuntimeError("sessionid not acquired within 3 minutes (saved dbg_add_account.png)")
        finally:
            await context.close()


def read_provisioning_inputs(
    argv: list[str], *, input_fn=input, secret_prompt=getpass.getpass,
) -> tuple[str, str, str, str]:
    if len(argv) > 2:
        raise SystemExit("Only account name may be supplied on argv.")
    account = argv[1] if len(argv) == 2 else input_fn("Account name: ").strip()
    email = input_fn("Google email: ").strip()
    password = secret_prompt("Google password: ")
    secret = secret_prompt("TOTP secret: ")
    return account, email, password, secret


async def main():
    account, email, password, secret = read_provisioning_inputs(sys.argv)
    await add_account_flow(account, email, password, secret)
    print(f"[{account}] Account added successfully!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        print("Account provisioning failed; inspect protected DOLA_ARTIFACTS_DIR diagnostics.")
        sys.exit(1)