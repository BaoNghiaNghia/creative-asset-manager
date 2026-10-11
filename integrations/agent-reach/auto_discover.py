#!/usr/bin/env python3
"""Independent, single-worker public marketplace discovery for RRUGC Stage 0.

Never starts/controls Windows Scouts. The only outbound research target is the
fixed Exa MCP endpoint; product pages are NOT fetched. Durable SQLite outbox
and CAM's existing tenant-scoped Scout Agent import make replays idempotent.
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
PROVIDER = "exa-mcp"
SCOUT_NAME = "Agent-Reach Auto Discover"
MAX_RESPONSE_BYTES = 524288
SEARCH_QUERIES = (
    ("etsy", "site:etsy.com/listing embroidered trucker hat funny saying"),
    ("amazon", "site:amazon.com/dp embroidered trucker hat quote"),
    ("pinterest", "site:pinterest.com/pin embroidered cap quote"),
    ("etsy", "site:etsy.com/listing retro slogan baseball cap"),
    ("amazon", "site:amazon.com/dp embroidered baseball cap saying"),
    ("pinterest", "site:pinterest.com/pin trucker hat words embroidery"),
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
            if response.status_code not in (200, 202, 204):
                raise RuntimeError("exa_notify_http_" + str(response.status_code))
            return {}
        self.session_id = response.headers.get("mcp-session-id", self.session_id)
        data = decode_rpc(response)
        if data.get("error"):
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
        if result.get("isError"):
            raise RuntimeError("exa_tool_error")
        if (result.get("_meta") or {}).get("ai.exa/rateLimited"):
            raise RuntimeError("exa_rate_limited")
        blocks = result.get("content") or []
        if any("hit Exa" in str(block.get("text", "")) and "limit" in str(block.get("text", "")).lower()
               for block in blocks if isinstance(block, dict)):
            raise RuntimeError("exa_rate_limited")
        return blocks


def parse_marketplace_results(source: str, content: list[dict], query: str) -> list[dict]:
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
                 "excerpt": normalize_text("Public Exa search result; unverified. " + query, 480)}
    return list(found.values())[:20]


def status_dict(db: sqlite3.Connection) -> dict:
    prepare_state(db)
    day = today()
    attempts = db.execute("SELECT COUNT(*) FROM discovery_attempt WHERE day=?", (day,)).fetchone()[0]
    pending = db.execute("SELECT COUNT(*) FROM research_signal WHERE status='pending'").fetchone()[0]
    return {"provider": PROVIDER, "last_run_at": meta(db, "last_run_at") or None,
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
    except requests.RequestException:
        pass  # Outbox is authoritative; short network outages must not lose it.


def run_once(db: sqlite3.Connection, *, max_queries: int = 2, daily_limit: int = 20,
             search_provider=None) -> dict:
    prepare_state(db)
    if not service_gate():
        # The server owns the paused state; do not overwrite it via heartbeat.
        save_meta(db, last_run_at=now_iso(), last_error="")
        return {"status": "paused", **status_dict(db)}
    cooldown = meta(db, "cooldown_until")
    if cooldown:
        try:
            if datetime.fromisoformat(cooldown) > datetime.now(timezone.utc):
                return {"status": "provider_cooldown", **status_dict(db)}
        except ValueError:
            pass
    attempts = db.execute("SELECT query_index FROM discovery_attempt WHERE day=?", (today(),)).fetchall()
    attempted = {int(row[0]) for row in attempts}
    used = len(attempted)
    max_queries = max(0, min(3, max_queries, max(0, daily_limit - used)))
    if max_queries == 0:
        heartbeat("ready")
        return {"status": "rate_budget_exhausted", **status_dict(db)}
    start = int(meta(db, "query_cursor", "0")) % len(SEARCH_QUERIES)
    candidates = [(start + i) % len(SEARCH_QUERIES) for i in range(len(SEARCH_QUERIES))]
    planned = [i for i in candidates if i not in attempted][:max_queries]
    search = search_provider or ExaMcpSearch()
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
            items = parse_marketplace_results(source, search.search(query), query)
            for start_at in range(0, len(items), MAX_BATCH):
                created, repeated = ingest(db, items[start_at:start_at + MAX_BATCH])
                added += created
                duplicates += repeated
            save_meta(db, query_cursor=(index + 1) % len(SEARCH_QUERIES),
                      last_success_at=now_iso(), last_error="", cooldown_until="")
        except (RuntimeError, requests.RequestException, ValueError) as exc:
            error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            # Never include search-provider result contents or secrets in error.
            save_meta(db, last_error=error[:100])
            if error == "exa_rate_limited":
                save_meta(db, cooldown_until=(datetime.now(timezone.utc)
                          + timedelta(hours=6)).isoformat())
            break
    save_meta(db, last_run_at=now_iso(), added_last_run=added, duplicates_last_run=duplicates)
    if os.getenv("CAM_RESEARCH_SCOUT_AGENT_ID") and os.getenv("CAM_RESEARCH_SCOUT_TOKEN"):
        try:
            for _ in range(4):
                sent = sync_to_cam(db)
                if sent["status"] == "idle":
                    break
        except (ValueError, requests.RequestException) as exc:
            error = "cam_sync_retryable"
            save_meta(db, last_error=error)
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
