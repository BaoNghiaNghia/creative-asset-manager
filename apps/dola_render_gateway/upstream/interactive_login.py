"""Manual headed-browser Dola onboarding; provider credentials are never accepted."""
from __future__ import annotations
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

import config
from browser import launch_account_context, verify_dola_session

LOG = logging.getLogger("dola.interactive_login")

class InteractiveLoginState(str, Enum):
    STARTING = "starting"
    WAITING_FOR_USER = "waiting_for_user"
    VERIFYING = "verifying"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

_TERMINAL = {InteractiveLoginState.SUCCESS, InteractiveLoginState.FAILED,
             InteractiveLoginState.CANCELLED, InteractiveLoginState.EXPIRED}

@dataclass
class LoginSession:
    account: str
    login_session_id: str
    started_at: float
    expires_at: float
    state: InteractiveLoginState = InteractiveLoginState.STARTING
    browser_running: bool = False
    error: str | None = None
    verified_at: float | None = None
    context: Any = None
    playwright: Any = None
    task: asyncio.Task | None = None

    def response(self) -> dict:
        return {"account": self.account, "login_session_id": self.login_session_id,
                "state": self.state, "browser_running": self.browser_running,
                "started_at": self.started_at, "expires_at": self.expires_at,
                "verified": self.state == InteractiveLoginState.SUCCESS,
                "verified_at": self.verified_at, "error": self.error}

class InteractiveLoginManager:
    """Owns only headed login contexts. BrowserPool owns account allocation locks."""
    def __init__(self, pool, *, timeout: int | None = None, max_sessions: int | None = None,
                 playwright_factory=None, sleep=asyncio.sleep):
        self.pool = pool
        self.timeout = max(30, timeout or config.INTERACTIVE_LOGIN_TIMEOUT)
        self.max_sessions = max(1, max_sessions or config.MAX_INTERACTIVE_LOGINS)
        self.playwright_factory = playwright_factory
        self.sleep = sleep
        self._sessions: dict[str, LoginSession] = {}

    def status(self, account: str) -> dict:
        session = self._sessions.get(account)
        if session:
            return session.response()
        return {"account": account, "login_session_id": None, "state": "idle",
                "browser_running": False, "started_at": None, "expires_at": None,
                "verified": False, "verified_at": None, "error": None}

    async def start(self, account: str) -> dict:
        current = self._sessions.get(account)
        if current and current.state not in _TERMINAL:
            raise RuntimeError("Interactive login is already active for this account")
        if sum(x.state not in _TERMINAL for x in self._sessions.values()) >= self.max_sessions:
            raise RuntimeError("Interactive login capacity is full")
        self.pool.reserve_account(account, "INTERACTIVE_LOGIN")
        now = time.time()
        session = LoginSession(account, uuid.uuid4().hex, now, now + self.timeout)
        self._sessions[account] = session
        session.task = asyncio.create_task(self._run(session))
        LOG.info("interactive_login_started account=%s login_session_id=%s", account, session.login_session_id)
        return session.response()

    async def _run(self, session: LoginSession) -> None:
        try:
            if self.playwright_factory is None:
                from patchright.async_api import async_playwright
                self.playwright_factory = async_playwright
            session.playwright = await self.playwright_factory().start()
            session.context = await launch_account_context(session.playwright, session.account, headless=False)
            session.browser_running = True
            session.state = InteractiveLoginState.WAITING_FOR_USER
            LOG.info("interactive_login_browser_ready account=%s login_session_id=%s", session.account, session.login_session_id)
            page = session.context.pages[0] if session.context.pages else await session.context.new_page()
            await page.goto("https://www.dola.com/chat", timeout=60000, wait_until="domcontentloaded")
            while time.time() < session.expires_at:
                first = await verify_dola_session(session.context, page)
                if first.verified:
                    session.state = InteractiveLoginState.VERIFYING
                    await self.sleep(2)
                    second = await verify_dola_session(session.context, page)
                    if second.verified:
                        await self._close_browser(session)
                        self.pool.set_login_status(session.account, True)
                        self.pool.release_account(session.account, "INTERACTIVE_LOGIN")
                        session.verified_at = time.time()
                        session.state = InteractiveLoginState.SUCCESS
                        LOG.info("interactive_login_verified account=%s login_session_id=%s", session.account, session.login_session_id)
                        return
                    session.state = InteractiveLoginState.WAITING_FOR_USER
                if not session.context.pages:
                    session.state = InteractiveLoginState.FAILED
                    session.error = "Login browser was closed before verification"
                    return
                await self.sleep(2)
            session.state = InteractiveLoginState.EXPIRED
            session.error = "Login timed out"
            LOG.info("interactive_login_expired account=%s login_session_id=%s", session.account, session.login_session_id)
        except asyncio.CancelledError:
            session.state = InteractiveLoginState.CANCELLED
            LOG.info("interactive_login_cancelled account=%s login_session_id=%s", session.account, session.login_session_id)
            raise
        except Exception:
            session.state = InteractiveLoginState.FAILED
            session.error = "Browser login could not be completed"
            LOG.warning("interactive_login_failed account=%s login_session_id=%s", session.account, session.login_session_id)
        finally:
            if session.state != InteractiveLoginState.SUCCESS:
                self.pool.set_login_status(session.account, False)
                await self._close_browser(session)
                self.pool.release_account(session.account, "INTERACTIVE_LOGIN")

    async def _close_browser(self, session: LoginSession) -> None:
        session.browser_running = False
        if session.context is not None:
            try:
                await session.context.close()
            except Exception:
                pass
        if session.playwright is not None:
            try:
                await session.playwright.stop()
            except Exception:
                pass
        session.context = None
        session.playwright = None

    async def stop(self, account: str) -> dict:
        session = self._sessions.get(account)
        if not session or session.state in _TERMINAL:
            return self.status(account)
        if session.task:
            session.task.cancel()
            try:
                await session.task
            except asyncio.CancelledError:
                pass
        self.pool.release_account(account, "INTERACTIVE_LOGIN")
        return session.response()

    async def verify(self, account: str) -> dict:
        session = self._sessions.get(account)
        if session and session.state == InteractiveLoginState.WAITING_FOR_USER and session.context:
            check = await verify_dola_session(session.context)
            if check.verified:
                session.state = InteractiveLoginState.VERIFYING
            return session.response()
        self.pool.reserve_account(account, "VERIFY")
        try:
            ok = await self.pool.verify_account_owned(account)
            return {**self.status(account), "verified": ok, "state": "success" if ok else "idle"}
        finally:
            self.pool.release_account(account, "VERIFY")

    async def shutdown(self) -> None:
        await asyncio.gather(*(self.stop(x) for x in list(self._sessions)), return_exceptions=True)
