#!/usr/bin/env python3
"""CAM Research Intelligence: bounded, manually approved Stage 0 research inbox.

Agent-Reach supplies the channel capability registry; this adapter owns CAM's
provenance, deduplication, safe public RSS fetching, and explicit approval gate.
No cookies, Chrome profiles, credentials, API writes, or untrusted instructions.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DB_DEFAULT = ROOT / "data" / "research-inbox.sqlite3"
ALLOWED_FEED_HOSTS = frozenset({"news.google.com", "github.com", "www.youtube.com"})
ALLOWED_SOURCE_NAMES = frozenset({"rss", "github", "youtube", "web"})
FEEDS_FILE = ROOT / "feeds.json"
MAX_ITEMS = 30
MAX_BODY = 700_000
MAX_TITLE = 240
MAX_EXCERPT = 480
MAX_URL = 1200
MAX_BATCH = 50


def normalize_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    # Strip arbitrary tags and collapse whitespace; never execute content.
    value = re.sub(r"<[^>]*>", " ", value)
    value = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    return " ".join(value.split())[:limit]


def safe_public_url(raw_url: str, *, hosts: frozenset[str] | None = None) -> str:
    if not isinstance(raw_url, str) or len(raw_url) > MAX_URL:
        raise ValueError("Invalid external URL length")
    try:
        url = urlsplit(raw_url)
    except ValueError as exc:
        raise ValueError("Invalid URL") from exc
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.port not in (None, 443):
        raise ValueError("HTTPS without embedded credentials is required")
    hostname = url.hostname.lower().rstrip(".")
    if hosts is not None and hostname not in hosts:
        raise ValueError("Host is outside the explicit research allowlist")
    try:
        if ipaddress.ip_address(hostname):
            raise ValueError("IP literal is not an allowed research host")
    except ValueError as exc:
        if str(exc).startswith("IP literal"):
            raise
    if hostname.endswith(".local") or hostname in ("localhost",):
        raise ValueError("Localhost is not allowed")
    return raw_url


def _validate_feed_host(hostname: str) -> None:
    # DNS fail closed on private IP ranges, despite the fixed hostname allowlist.
    answers = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    if not answers:
        raise ValueError("DNS returned no addresses")
    for address in answers:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Resolved address is not public")


def fetch_rss(feed_url: str, *, max_bytes: int = MAX_BODY) -> bytes:
    """No HTTP redirects, proxy, credential forwarding, or unlimited response."""
    import requests
    url = safe_public_url(feed_url, hosts=ALLOWED_FEED_HOSTS)
    hostname = urlsplit(url).hostname or ""
    _validate_feed_host(hostname)
    with requests.Session() as session:
        session.trust_env = False
        with session.get(url, allow_redirects=False, timeout=(4, 12),
                         headers={"User-Agent": "CAM-Research-Reader/1.0"},
                         stream=True) as response:
            if response.status_code != 200:
                raise ValueError("Feed fetch returned non-200 or redirect")
            collected = bytearray()
            for chunk in response.iter_content(chunk_size=16384):
                if not chunk:
                    continue
                collected.extend(chunk)
                if len(collected) > max_bytes:
                    raise ValueError("Feed exceeds maximum size")
            return bytes(collected)


def parse_rss(data: bytes, *, feed_name: str) -> list[dict[str, str]]:
    import feedparser
    parsed = feedparser.parse(data)
    if not getattr(parsed, "entries", None):
        return []
    entries = []
    for entry in parsed.entries[:MAX_ITEMS]:
        title = normalize_text(entry.get("title", ""), MAX_TITLE)
        link = entry.get("link", "")
        excerpt = normalize_text(entry.get("summary", ""), MAX_EXCERPT)
        if not title or not isinstance(link, str):
            continue
        try:
            safe_public_url(link)
        except ValueError:
            continue
        entries.append({"source": "rss", "channel": feed_name, "title": title,
                        "url": link, "excerpt": excerpt})
    return entries


def init_db(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(db)
    connection.execute("PRAGMA busy_timeout=3000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS research_signal (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          fingerprint TEXT NOT NULL UNIQUE,
          source TEXT NOT NULL,
          channel TEXT NOT NULL,
          url TEXT NOT NULL,
          title TEXT NOT NULL,
          excerpt TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          keyword TEXT,
          created_at TEXT NOT NULL,
          reviewed_at TEXT
        )
    """)
    connection.commit()
    return connection


def ingest(connection: sqlite3.Connection, signals: list[dict[str, object]]) -> tuple[int, int]:
    added = duplicated = 0
    for signal in signals[:MAX_BATCH]:
        if not isinstance(signal, dict):
            continue
        source = normalize_text(signal.get("source", ""), 24).lower()
        if source not in ALLOWED_SOURCE_NAMES:
            continue
        title = normalize_text(signal.get("title"), MAX_TITLE)
        excerpt = normalize_text(signal.get("excerpt", ""), MAX_EXCERPT)
        channel = normalize_text(signal.get("channel", source), 70)
        try:
            url = safe_public_url(str(signal.get("url", "")))
        except ValueError:
            continue
        if not title:
            continue
        fingerprint = hashlib.sha256((source + "|" + url.lower()).encode()).hexdigest()
        cursor = connection.execute("""
          INSERT OR IGNORE INTO research_signal
            (fingerprint,source,channel,url,title,excerpt,created_at)
          VALUES(?,?,?,?,?,?,?)
        """, (fingerprint,source,channel,url,title,excerpt,
              datetime.now(timezone.utc).isoformat()))
        added += cursor.rowcount
        duplicated += (cursor.rowcount == 0)
    connection.commit()
    return added, duplicated


def validate_keyword(value: str) -> str:
    keyword = normalize_text(value, 150).upper()
    words = keyword.split()
    if not (2 <= len(words) <= 12):
        raise ValueError("A keyword must contain 2–12 words")
    if len(keyword) < 5 or not all(re.fullmatch(r"[\w'&\-]+", word, re.UNICODE) for word in words):
        raise ValueError("Invalid keyword characters")
    return keyword


def review(connection: sqlite3.Connection, *, signal_id: int, decision: str,
           keyword: str | None = None) -> int:
    if decision not in ("approved", "rejected"):
        raise ValueError("Decision must be approved or rejected")
    normalized = validate_keyword(keyword or "") if decision == "approved" else None
    cursor = connection.execute("""
      UPDATE research_signal SET status=?,keyword=?,reviewed_at=?
      WHERE id=? AND status='pending'
    """, (decision,normalized,datetime.now(timezone.utc).isoformat(),signal_id))
    connection.commit()
    return cursor.rowcount


def list_inbox(connection: sqlite3.Connection, *, status: str = "pending",
               limit: int = 25) -> list[dict]:
    if status not in ("pending", "approved", "rejected", "synced", "all"):
        raise ValueError("Invalid status")
    query = "SELECT id,source,channel,url,title,excerpt,status,keyword,created_at FROM research_signal"
    params: tuple = (min(max(limit, 1), 100),)
    if status != "all":
        query += " WHERE status = ?"
        params = (status,) + params
    query += " ORDER BY id DESC LIMIT ?"
    return [dict(zip(("id","source","channel","url","title","excerpt",
                      "status","keyword","created_at"), record))
            for record in connection.execute(query, params).fetchall()]


def approved_stage0_batch(connection: sqlite3.Connection) -> dict:
    # Deliberately no API posting or AEBrowse charges until operator action.
    rows = connection.execute(
        "SELECT DISTINCT keyword FROM research_signal WHERE status='approved' AND keyword IS NOT NULL LIMIT ?",
        (MAX_BATCH,),
    ).fetchall()
    return {"keywords": [row[0] for row in rows], "force": False}


def sync_to_cam(connection: sqlite3.Connection, *,
                base_url: str | None = None, agent_id: str | None = None,
                token: str | None = None) -> dict:
    """Send pending *suggestions only* through the existing paired Scout identity.

    Never reads CAM app sessions, Google OAuth, Chrome profiles or env files.
    No data is marked synced if API/TLS/network checks fail.
    """
    import requests
    base = (base_url or os.environ.get("CAM_RESEARCH_API_URL") or
            "https://creative-assets.ddns.net").rstrip("/")
    agent_id = agent_id or os.environ.get("CAM_RESEARCH_SCOUT_AGENT_ID") or ""
    token = token or os.environ.get("CAM_RESEARCH_SCOUT_TOKEN") or ""
    if base != "https://creative-assets.ddns.net":
        raise ValueError("Production research sync only permits CAM's configured HTTPS origin")
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", agent_id):
        raise ValueError("A valid, explicitly paired Scout Agent ID is required")
    if not 20 <= len(token) <= 512 or "\n" in token or "\r" in token:
        raise ValueError("A dedicated Scout Agent token must be supplied securely")
    rows = connection.execute(
        "SELECT id,source,channel,url,title,excerpt FROM research_signal "
        "WHERE status='pending' ORDER BY id ASC LIMIT ?", (MAX_BATCH,),
    ).fetchall()
    if not rows:
        return {"status": "idle", "sent": 0}
    signals = [{"source": row[1], "channel": row[2], "url": row[3],
                "title": row[4], "excerpt": row[5]} for row in rows]
    endpoint = (base + "/api/v1/realistic-review-ugc/research/scout-agents/" +
                agent_id + "/signals/import")
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            endpoint, json={"signals":signals},
            headers={"Authorization":"Bearer " + token},
            timeout=(5, 25), allow_redirects=False,
        )
        if response.status_code != 200:
            raise ValueError("CAM research sync failed with HTTP " + str(response.status_code))
        receipt = response.json()
    if not isinstance(receipt, dict) or not all(k in receipt for k in ("added","duplicates")):
        raise ValueError("CAM research sync returned an invalid receipt")
    # Server is idempotent by tenant+fingerprint; avoid retrying already received items.
    connection.executemany(
        "UPDATE research_signal SET status='synced' WHERE id=? AND status='pending'",
        [(row[0],) for row in rows],
    )
    connection.commit()
    return {"status": "ok", "sent": len(rows),
            "added": int(receipt["added"]), "duplicates": int(receipt["duplicates"])}


def public_channel_doctor() -> dict:
    """Run only four preselected Agent-Reach channel checks in a fresh HOME."""
    venv = ROOT / ".venv" / "bin" / "python"
    source = ROOT / "upstream"
    if not venv.exists() or not source.exists():
        return {"status": "unavailable", "reason": "Isolated Agent-Reach runtime is not installed"}
    # Never call "agent-reach doctor": fork installs skills as a side effect.
    code = """
import json
from agent_reach.channels import get_channel
from agent_reach.config import Config
config = Config()
result = {}
for name in ('rss', 'youtube', 'github', 'web'):
    channel = get_channel(name)
    if channel is None:
        result[name] = {'status':'unavailable'}
    else:
        status, message = channel.check(config)
        result[name] = {'status':status,'message':str(message)[:160]}
print(json.dumps(result))
"""
    isolated_home = ROOT / "runtime-home"
    isolated_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = {"HOME": str(isolated_home), "PYTHONPATH": str(source), "PATH":
           str(ROOT / ".venv" / "bin") + ":/usr/bin:/bin", "LANG": "C.UTF-8",
           "PYTHONNOUSERSITE": "1"}
    try:
        result = subprocess.run([str(venv), "-c", code], cwd=ROOT,
                                env=env, capture_output=True, text=True,
                                timeout=25, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "error", "reason": "Isolated channel doctor unavailable"}
    if result.returncode != 0:
        return {"status": "error", "reason": "Isolated doctor failed (no secrets logged)"}
    try:
        return {"status": "ok", "channels": json.loads(result.stdout)}
    except json.JSONDecodeError:
        return {"status": "error", "reason": "Malformed channel status"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB_DEFAULT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("collect")
    p_import = sub.add_parser("ingest-json")
    p_import.add_argument("--input", required=True, type=Path)
    p_list = sub.add_parser("list")
    p_list.add_argument("--status", choices=["all","pending","approved","rejected","synced"], default="pending")
    p_review = sub.add_parser("review")
    p_review.add_argument("--id", type=int, required=True)
    p_review.add_argument("--decision", choices=["approved","rejected"], required=True)
    p_review.add_argument("--keyword", default=None)
    sub.add_parser("export-stage0")
    sub.add_parser("sync-cam")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        print(json.dumps(public_channel_doctor(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "collect":
        feeds = json.loads(FEEDS_FILE.read_text(encoding="utf-8"))
        pending = []
        for item in feeds.get("feeds", [])[:6]:
            try:
                url = safe_public_url(item["url"], hosts=ALLOWED_FEED_HOSTS)
                pending += parse_rss(fetch_rss(url), feed_name=str(item["name"])[:60])
            except (OSError, ValueError, KeyError, TimeoutError) as exc:
                print("Research source skipped:", type(exc).__name__, file=sys.stderr)
        with init_db(args.database) as db:
            added, duplicated = ingest(db, pending)
        print(json.dumps({"status":"ok", "received":len(pending),
                          "added":added, "duplicates":duplicated}))
        return 0
    with init_db(args.database) as db:
        if args.command == "ingest-json":
            data = json.loads(args.input.read_text(encoding="utf-8"))
            if not isinstance(data, list) or len(data) > 100:
                raise ValueError("Research imports must be a list with at most 100 records")
            added, duplicated = ingest(db, data)
            result = {"added": added, "duplicates": duplicated}
        elif args.command == "list":
            result = list_inbox(db,status=args.status)
        elif args.command == "sync-cam":
            result = sync_to_cam(db)
        elif args.command == "review":
            result = {"changed":review(db, signal_id=args.id, decision=args.decision,
                                       keyword=args.keyword)}
        else:
            result = approved_stage0_batch(db)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, sqlite3.DatabaseError, json.JSONDecodeError) as exc:
        print("Research Intelligence:",type(exc).__name__,file=sys.stderr)
        sys.exit(2)
