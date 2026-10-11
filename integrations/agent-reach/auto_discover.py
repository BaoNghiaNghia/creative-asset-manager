#!/usr/bin/env python3
"""Independent, single-worker public marketplace discovery for RRUGC Stage 0.

Never starts/controls Windows Scouts. Research uses official fixed-host
Tavily Keyless, Exa MCP or Brave Search APIs; product pages are NOT fetched.
Durable SQLite outbox and CAM's tenant-scoped Scout Agent import make replays
idempotent.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit

import requests

from research_intelligence import (
    ROOT, DB_DEFAULT, MAX_BATCH, MARKETPLACE_HOSTS,
    canonical_marketplace_url, ingest, init_db, normalize_text, sync_to_cam,
)

ENDPOINT = "https://mcp.exa.ai/mcp"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
TAVILY_ENDPOINT = "https://api.tavily.com/search"
PROVIDER = "exa-mcp"
BRAVE_PROVIDER = "brave-search"
TAVILY_PROVIDER = "tavily-keyless"
SCOUT_NAME = "Agent-Reach Auto Discover"
MAX_RESPONSE_BYTES = 524288
# Query diversity for quotes on real hats; each index is attempted at most once
# per UTC day. Keep provider calls bounded by --max-queries and --daily-limit.
SEARCH_QUERIES = (
    ("etsy", "site:etsy.com/listing funny saying embroidered trucker hat"),
    ("amazon", "site:amazon.com/dp funny saying embroidered trucker hat"),
    ("pinterest", "site:pinterest.com/pin embroidered trucker hat quote"),
    ("etsy", "site:etsy.com/listing embroidered retro saying baseball cap -inurl:market"),
    ("amazon", "site:amazon.com/dp embroidered baseball cap saying"),
    ("pinterest", "site:pinterest.com/pin trucker hat words embroidery"),
    ("etsy", "site:etsy.com/listing vintage washed dad hat embroidered phrase"),
    ("amazon", "site:amazon.com/dp funny embroidered trucker hat lettering"),
    ("pinterest", "site:pinterest.com/pin hand holding embroidered trucker hat quote"),
    ("etsy", "site:etsy.com/listing faith saying embroidered baseball cap"),
    ("amazon", "site:amazon.com/dp religious saying embroidered trucker hat"),
    ("pinterest", "site:pinterest.com/pin western trucker hat embroidered saying"),
    ("etsy", "site:etsy.com/listing sarcastic quote embroidered baseball cap"),
    ("amazon", "site:amazon.com/dp western country slogan embroidered cap"),
    ("pinterest", "site:pinterest.com/pin retro typography cap embroidery"),
    ("etsy", "site:etsy.com/listing minimal text custom trucker cap saying"),
    ("amazon", "site:amazon.com/dp novelty funny phrase snapback cap"),
    ("pinterest", "site:pinterest.com/pin selfie wearing embroidered slogan hat"),
    ("etsy", "site:etsy.com/listing motivational saying stitched baseball cap"),
    ("amazon", "site:amazon.com/dp funny quote embroidered women's cap"),
    ("pinterest", "site:pinterest.com/pin five panel embroidered text cap"),
    ("etsy", "site:etsy.com/listing funny embroidered rope cap saying"),
    ("amazon", "site:amazon.com/dp funny saying mesh back trucker hat"),
    ("pinterest", "site:pinterest.com/pin baseball cap phrase embroidery lettering"),
)
PUBLIC_ITEM_RE = re.compile(r"https://(?:www\.)?(?:etsy|amazon|pinterest)\.com/[^\s<>\]\[\"']+", re.I)
CAP_RE = re.compile(r"\b(hats?|caps?|trucker|embroider(?:ed|y|ing)|baseball|snapback)\b", re.I)


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def prepare_state(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS discovery_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS discovery_attempt (day TEXT NOT NULL, query_index INTEGER NOT NULL, "
               "run_at TEXT NOT NULL, PRIMARY KEY (day,query_index))")
    db.commit()


def meta(db: sqlite3.Connection, name: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM discovery_state WHERE key=?", (name,)).fetchone()
    return str(row[0]) if row else default


def save_meta(db: sqlite3.Connection, **values: object) -> None:
    db.executemany("INSERT INTO discovery_state(key,value) VALUES(?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   [(key, str(value)) for key, value in values.items()])
    db.commit()


@contextmanager
def exclusive(lock_path: Path):
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def decode_rpc(response: requests.Response) -> dict:
    if response.status_code == 429:
        raise RuntimeError("exa_rate_limited")
    if response.status_code != 200:
        raise RuntimeError("exa_http_" + str(response.status_code))
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise RuntimeError("exa_response_too_large")
    content_type = response.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("exa_invalid_response")
        return data
    if "text/event-stream" in content_type:
        for event in reversed(response.text.split("\n\n")):
            for line in event.splitlines():
                if line.startswith("data: "):
                    try:
                        parsed = json.loads(line[6:])
                        if isinstance(parsed, dict) and ("result" in parsed or "error" in parsed):
                            return parsed
                    except ValueError:
                        pass
    raise RuntimeError("exa_invalid_response")


class ExaMcpSearch:
    """Small, fixed-host Streamable HTTP JSON-RPC client; no mcporter install."""

    def __init__(self):
        self.http = requests.Session()
        self.http.trust_env = False
        self.session_id = ""
        self.initialized = False

    def rpc(self, method: str, params: dict, request_id: int | None) -> dict:
        headers = {"Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json", "MCP-Protocol-Version": "2025-03-26"}
        # Optional single provider credential for higher quotas; never embed
        # in URL, query, JSON body, status, exception, logs or search results.
        if os.getenv("CAM_RESEARCH_EXA_API_KEY"):
            headers["x-api-key"] = os.environ["CAM_RESEARCH_EXA_API_KEY"]
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            payload["id"] = request_id
        response = self.http.post(ENDPOINT, headers=headers, json=payload,
                                  timeout=(5, 25), allow_redirects=False)
        if request_id is None:
            if response.status_code == 429:
                raise RuntimeError("exa_rate_limited")
            if response.status_code not in (200, 202, 204):
                raise RuntimeError("exa_notify_http_" + str(response.status_code))
            return {}
        self.session_id = response.headers.get("mcp-session-id", self.session_id)
        data = decode_rpc(response)
        if data.get("error"):
            error = data["error"]
            message = str(error.get("message", "")).lower() if isinstance(error, dict) else ""
            if isinstance(error, dict) and (error.get("code") == 429 or
                 ("rate" in message and "limit" in message) or
                 ("quota" in message and "exceed" in message) or
                 "too many requests" in message):
                raise RuntimeError("exa_rate_limited")
            raise RuntimeError("exa_rpc_error")
        return data.get("result") or {}

    def search(self, query: str) -> list[dict]:
        if not self.initialized:
            self.rpc("initialize", {"protocolVersion": "2025-03-26",
                    "capabilities": {}, "clientInfo": {"name": "cam-research-discovery", "version": "1.0"}}, 1)
            self.rpc("notifications/initialized", {}, None)
            self.initialized = True
        result = self.rpc("tools/call", {"name": "web_search_exa",
                         "arguments": {"query": query, "numResults": 8}}, 2)
        blocks = result.get("content") or []
        if (result.get("_meta") or {}).get("ai.exa/rateLimited") or any(
            isinstance(block, dict) and (
                "rate limit" in str(block.get("text", "")).lower() or
                "free limit" in str(block.get("text", "")).lower() or
                "quota exceeded" in str(block.get("text", "")).lower())
            for block in blocks):
            raise RuntimeError("exa_rate_limited")
        if result.get("isError"):
            raise RuntimeError("exa_tool_error")
        return blocks


class TavilyKeylessSearch:
    """Official Tavily keyless Search API: no login, no key, no billing setup.

    Uses the provider's published keyless headers and strictly filters
    returned result URLs. The caller keeps a bounded daily budget.
    """

    provider = TAVILY_PROVIDER

    def __init__(self):
        self.http = requests.Session()
        self.http.trust_env = False

    def search(self, query: str) -> list[dict]:
        # Search is restricted to our static marketplace queries. This is
        # NOT a general-purpose URL fetch or arbitrary browsing agent.
        source = next((host for host in ("etsy", "amazon", "pinterest")
                       if "site:" + host + ".com" in query.lower()), None)
        if source is None:
            raise ValueError("tavily_source_invalid")
        response = self.http.post(
            TAVILY_ENDPOINT,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Tavily-Access-Mode": "keyless",
                "X-Client-Source": "cam-research-keyless",
            },
            json={
                "query": query,
                "search_depth": "basic",
                "max_results": 8,
                "include_domains": [source + ".com"],
            },
            timeout=(5, 25),
            allow_redirects=False,
        )
        if response.status_code in (402, 429):
            raise RuntimeError("tavily_rate_limited")
        if response.status_code in (401, 403):
            raise RuntimeError("tavily_keyless_denied")
        if response.status_code != 200:
            raise RuntimeError("tavily_http_" + str(response.status_code))
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise RuntimeError("tavily_response_too_large")
        if "application/json" not in response.headers.get("content-type", "").lower():
            raise RuntimeError("tavily_invalid_response")
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError("tavily_invalid_response") from None
        if not isinstance(data, dict):
            raise RuntimeError("tavily_invalid_response")
        if data.get("error") or data.get("detail"):
            raise RuntimeError("tavily_keyless_denied")
        results = data.get("results")
        if not isinstance(results, list):
            raise RuntimeError("tavily_invalid_response")
        blocks = []
        for result in results[:8]:
            if not isinstance(result, dict):
                continue
            url = str(result.get("url") or "")[:2048]
            if not PUBLIC_ITEM_RE.fullmatch(url):
                continue
            title = normalize_text(str(result.get("title") or ""), 200)
            description = normalize_text(str(result.get("content") or ""), 240)
            blocks.append({"type": "text", "text": (
                "Title: " + title + "\nURL: " + url + "\nDescription: " + description)})
        return blocks


class BraveWebSearch:
    """Official Brave Search API; never scrape search HTML or marketplace pages."""

    provider = BRAVE_PROVIDER

    def __init__(self):
        self.http = requests.Session()
        self.http.trust_env = False

    def search(self, query: str) -> list[dict]:
        key = os.getenv("CAM_RESEARCH_BRAVE_API_KEY", "")
        if not key:
            raise RuntimeError("brave_not_configured")
        response = self.http.get(
            BRAVE_ENDPOINT,
            headers={"Accept": "application/json", "X-Subscription-Token": key},
            params={"q": query, "count": 10, "country": "US", "search_lang": "en"},
            timeout=(5, 25), allow_redirects=False,
        )
        if response.status_code == 429:
            raise RuntimeError("brave_rate_limited")
        if response.status_code in (401, 403):
            raise RuntimeError("brave_auth_rejected")
        if response.status_code != 200:
            raise RuntimeError("brave_http_" + str(response.status_code))
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise RuntimeError("brave_response_too_large")
        if "application/json" not in response.headers.get("content-type", "").lower():
            raise RuntimeError("brave_invalid_response")
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError("brave_invalid_response") from None
        if not isinstance(data, dict) or not isinstance(data.get("web"), dict):
            raise RuntimeError("brave_invalid_response")
        results = data["web"].get("results")
        if not isinstance(results, list):
            raise RuntimeError("brave_invalid_response")
        blocks = []
        for result in results[:10]:
            if not isinstance(result, dict):
                continue
            url = str(result.get("url") or "")[:2048]
            # No requests to arbitrary result URLs; canonicalization below
            # rejects unsupported domains and listing/search paths.
            if not PUBLIC_ITEM_RE.fullmatch(url):
                continue
            title = normalize_text(str(result.get("title") or ""), 200)
            description = normalize_text(str(result.get("description") or ""), 240)
            blocks.append({"type": "text", "text": (
                "Title: " + title + "\nURL: " + url + "\nDescription: " + description)})
        return blocks


def _future_cooldown(db: sqlite3.Connection, key: str) -> bool:
    raw = meta(db, key)
    if not raw:
        return False
    try:
        return datetime.fromisoformat(raw) > datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def _cooldown_key_for(provider: str) -> str:
    if provider == TAVILY_PROVIDER:
        return "tavily_cooldown_until"
    if provider == BRAVE_PROVIDER:
        return "brave_cooldown_until"
    # A key upgrades the Exa billing identity, so an old anonymous IP
    # cooldown must not block the newly authenticated account.
    return "exa_keyed_cooldown_until" if os.getenv("CAM_RESEARCH_EXA_API_KEY") else "cooldown_until"


def configured_search(db: sqlite3.Connection):
    """Prefer the real, free Tavily keyless tier with no credentials.

    Auto switches to an existing keyed provider after Tavily's own quota
    cooldown. Never rotates IPs or starts Windows/browser workers.
    """
    mode = os.getenv("CAM_RESEARCH_SEARCH_PROVIDER", "auto").strip().lower()
    has_brave = bool(os.getenv("CAM_RESEARCH_BRAVE_API_KEY"))
    has_exa = bool(os.getenv("CAM_RESEARCH_EXA_API_KEY"))
    if mode not in ("auto", "tavily", "exa", "brave"):
        raise RuntimeError("search_provider_invalid")
    if mode == "tavily":
        return TavilyKeylessSearch()
    if mode == "brave":
        return BraveWebSearch()
    if mode == "exa":
        return ExaMcpSearch()
    if not _future_cooldown(db, "tavily_cooldown_until"):
        return TavilyKeylessSearch()
    if has_brave and not _future_cooldown(db, "brave_cooldown_until"):
        return BraveWebSearch()
    if has_exa and not _future_cooldown(db, "exa_keyed_cooldown_until"):
        return ExaMcpSearch()
    # If every optional provider is unavailable, report Tavily's cooldown
    # instead of consuming a fresh request against exhausted anonymous Exa.
    return TavilyKeylessSearch()


def parse_marketplace_results(source: str, content: list[dict], query: str, provider: str = PROVIDER) -> list[dict]:
    """Accept only exact official item paths plus cap-related search evidence."""
    found: dict[str, dict] = {}
    for block in content[:16]:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = str(block.get("text") or "")[:30000]
        # Exa typically returns "Title: ... URL: ..." blocks. Split on URLs to
        # limit context to the listing, not adjacent unrelated search results.
        matches = list(PUBLIC_ITEM_RE.finditer(text))
        for index, match in enumerate(matches[:35]):
            before = text[max(0, match.start() - 350):match.start()]
            after = text[match.end():min(len(text), match.end() + 280)]
            context = before + " " + after
            if not CAP_RE.search(context):
                continue
            raw = match.group(0).rstrip(".,;):")
            try:
                canonical = canonical_marketplace_url(source, raw)
            except ValueError:
                continue
            # Use only a search-provider snippet, never claim source page was read.
            title_match = re.search(r"(?:^|\n)\s*(?:Title:|#)\s*([^\n]{4,200})", before, re.I)
            title = normalize_text(title_match.group(1) if title_match else context[-160:], 240)
            if not title:
                title = "Embroidered cap listing"
            found[canonical] = {"source": source, "channel": "auto-discover",
                 "title": title, "url": canonical,
                 "excerpt": normalize_text("Public " + provider + " search result; unverified. " + query, 480)}
    return list(found.values())[:20]


def status_dict(db: sqlite3.Connection) -> dict:
    prepare_state(db)
    day = today()
    attempts = db.execute("SELECT COUNT(*) FROM discovery_attempt WHERE day=?", (day,)).fetchone()[0]
    pending = db.execute("SELECT COUNT(*) FROM research_signal WHERE status='pending'").fetchone()[0]
    return {"provider": meta(db, "active_provider", PROVIDER), "last_run_at": meta(db, "last_run_at") or None,
            "last_success_at": meta(db, "last_success_at") or None,
            "last_error": meta(db, "last_error") or None,
            "queries_today": attempts, "pending_outbox": pending,
            "added_last_run": int(meta(db, "added_last_run", "0")),
            "duplicates_last_run": int(meta(db, "duplicates_last_run", "0"))}


def service_gate() -> bool:
    """Dedicated agent token only; Windows Scout credentials never reused."""
    agent = os.getenv("CAM_RESEARCH_SCOUT_AGENT_ID", "")
    token = os.getenv("CAM_RESEARCH_SCOUT_TOKEN", "")
    if not agent or not token:
        return True  # Discovery can collect locally until paired, then sync.
    url = f"https://creative-assets.ddns.net/api/v1/realistic-review-ugc/research/scout-agents/{agent}/auto-discover/config"
    session = requests.Session()
    session.trust_env = False
    response = session.get(url, headers={"Authorization": "Bearer " + token},
                           timeout=(5, 12), allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError("cam_discovery_config_unavailable")
    return response.json().get("enabled") is True


def heartbeat(status: str, error_code: str = "") -> None:
    agent = os.getenv("CAM_RESEARCH_SCOUT_AGENT_ID", "")
    token = os.getenv("CAM_RESEARCH_SCOUT_TOKEN", "")
    if not agent or not token:
        return
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.post(
            f"https://creative-assets.ddns.net/api/v1/realistic-review-ugc/scout-agents/{agent}/heartbeat",
            headers={"Authorization": "Bearer " + token},
            json={"status": status, "client_version": "rrugc-research-v1",
                  "machine_label": "CAM VPS · Agent-Reach", "error_code": error_code or None},
            timeout=(5, 12), allow_redirects=False)
        if response.status_code != 200:
            raise RuntimeError("heartbeat_rejected")
    except (requests.RequestException, RuntimeError):
        pass  # Best-effort heartbeat never jeopardizes the durable outbox.


def flush_outbox(db: sqlite3.Connection) -> str:
    """Retry durable CAM delivery even when Exa discovery is on cooldown.

    Never use Windows Scout credentials; leave every signal pending on failure.
    Limit one timer invocation to four acknowledged batches.
    """
    if not (os.getenv("CAM_RESEARCH_SCOUT_AGENT_ID") and os.getenv("CAM_RESEARCH_SCOUT_TOKEN")):
        return ""
    try:
        for _ in range(4):
            receipt = sync_to_cam(db)
            if receipt["status"] == "idle":
                break
    except (ValueError, requests.RequestException):
        save_meta(db, last_error="cam_sync_retryable")
        return "cam_sync_retryable"
    return ""


def run_once(db: sqlite3.Connection, *, max_queries: int = 2, daily_limit: int = 20,
             search_provider=None) -> dict:
    prepare_state(db)
    if not service_gate():
        # The server owns the paused state; do not overwrite it via heartbeat.
        save_meta(db, last_run_at=now_iso(), last_error="")
        return {"status": "paused", **status_dict(db)}
    search = search_provider or configured_search(db)
    provider = getattr(search, "provider", PROVIDER)
    cooldown_key = _cooldown_key_for(provider)
    cooldown_error = {
        BRAVE_PROVIDER: "brave_rate_limited",
        TAVILY_PROVIDER: "tavily_rate_limited",
    }.get(provider, "exa_rate_limited")
    save_meta(db, active_provider=provider)
    if _future_cooldown(db, cooldown_key):
        sync_error = flush_outbox(db)
        heartbeat("error", sync_error or cooldown_error)
        return {"status": "error" if sync_error else "provider_cooldown", **status_dict(db)}
    attempts = db.execute("SELECT query_index FROM discovery_attempt WHERE day=?", (today(),)).fetchall()
    attempted = {int(row[0]) for row in attempts}
    used = len(attempted)
    max_queries = max(0, min(3, max_queries, max(0, daily_limit - used)))
    if max_queries == 0:
        sync_error = flush_outbox(db)
        heartbeat("error" if sync_error else "ready", sync_error)
        return {"status": "error" if sync_error else "rate_budget_exhausted", **status_dict(db)}
    start = int(meta(db, "query_cursor", "0")) % len(SEARCH_QUERIES)
    candidates = [(start + i) % len(SEARCH_QUERIES) for i in range(len(SEARCH_QUERIES))]
    planned = [i for i in candidates if i not in attempted][:max_queries]
    added = duplicates = 0
    error = ""
    heartbeat("busy")
    for index in planned:
        source, query = SEARCH_QUERIES[index]
        # Journal the search *before* the provider call. No hidden unlimited
        # retry loops after a crash/429.
        db.execute("INSERT OR IGNORE INTO discovery_attempt (day,query_index,run_at) VALUES(?,?,?)",
                   (today(), index, now_iso()))
        db.commit()
        try:
            items = parse_marketplace_results(source, search.search(query), query, provider)
            for start_at in range(0, len(items), MAX_BATCH):
                created, repeated = ingest(db, items[start_at:start_at + MAX_BATCH])
                added += created
                duplicates += repeated
            save_meta(db, query_cursor=(index + 1) % len(SEARCH_QUERIES),
                      last_success_at=now_iso(), last_error="", **{cooldown_key: ""})
        except (RuntimeError, requests.RequestException, ValueError) as exc:
            error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            # Never include search-provider result contents or secrets in error.
            save_meta(db, last_error=error[:100])
            if error == cooldown_error or (
                provider == TAVILY_PROVIDER and error == "tavily_keyless_denied"
            ):
                save_meta(db, **{cooldown_key: (datetime.now(timezone.utc)
                          + timedelta(hours=6)).isoformat()})
            break
    save_meta(db, last_run_at=now_iso(), added_last_run=added, duplicates_last_run=duplicates)
    sync_error = flush_outbox(db)
    if sync_error and not error:
        error = sync_error
    heartbeat("error" if error else "ready", error)
    return {"status": "error" if error else "ok", **status_dict(db)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("doctor", "once", "status"))
    parser.add_argument("--database", type=Path, default=DB_DEFAULT)
    parser.add_argument("--max-queries", type=int, default=2)
    parser.add_argument("--daily-limit", type=int, default=20)
    args = parser.parse_args()
    with exclusive(args.database.parent / "auto-discover.lock"):
        with init_db(args.database) as db:
            if args.command == "status":
                outcome = status_dict(db)
            elif args.command == "doctor":
                outcome = {"enabled": os.getenv("CAM_RESEARCH_AUTO_DISCOVER_ENABLED", "0") == "1",
                           "paired": bool(os.getenv("CAM_RESEARCH_SCOUT_AGENT_ID") and
                                          os.getenv("CAM_RESEARCH_SCOUT_TOKEN")),
                           "brave_configured": bool(os.getenv("CAM_RESEARCH_BRAVE_API_KEY")),
                           "exa_key_configured": bool(os.getenv("CAM_RESEARCH_EXA_API_KEY")),
                           "mode": os.getenv("CAM_RESEARCH_SEARCH_PROVIDER", "auto"),
                           **status_dict(db)}
            elif os.getenv("CAM_RESEARCH_AUTO_DISCOVER_ENABLED", "0") != "1":
                outcome = {"status": "disabled", **status_dict(db)}
            else:
                outcome = run_once(db, max_queries=args.max_queries,
                                   daily_limit=args.daily_limit)
    print(json.dumps(outcome, ensure_ascii=False))
    return 0 if outcome.get("status") not in {"error"} else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, sqlite3.DatabaseError, RuntimeError) as exc:
        print("Auto Discover:", type(exc).__name__, file=sys.stderr)
        sys.exit(2)
