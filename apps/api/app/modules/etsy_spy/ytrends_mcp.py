"""Read-only YTuong YTrends Etsy keyword context for manually queued Picked drafts.

YTrends indexes a subset of Etsy listings, NOT TikTok Shop search. Never
represent the market snapshot or seller counts as TikTok Shop metrics.
No per-listing images, tenant IDs or credentials are sent to the MCP server.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import date
from typing import Any

import httpx

MCP_URL = "https://mcp.trends.ytuong.ai/mcp"
SOURCE = "trends.ytuong.ai"
MAX_RESPONSE_BYTES = 96_000
CACHE_LIMIT = 256
CACHE_VALID_SECONDS = 6 * 60 * 60
CACHE_NO_DATA_SECONDS = 20 * 60
CACHE_FAILURE_SECONDS = 90
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()
_GARMENT_SUFFIX = re.compile(
    r"\s+(?:(?:t[ -]?shirt|shirt|tee|sweatshirt|sweater|hoodie|pullover|"
    r"long sleeve|crewneck)s?)\s*$", re.IGNORECASE
)
_SAFE_KEYWORD = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 '&/\-]{0,100}$")


_SELLER_BRANDS = re.compile(r"\b(?:comfort\s+colors?|gildan)\b", re.IGNORECASE)
_FIRST_GARMENT = re.compile(
    r"\b(?:t[ -]?shirts?|shirts?|tees?|sweatshirts?|sweaters?|hoodies?|"
    r"pullovers?|crewnecks?)\b", re.IGNORECASE
)


def research_query(title: str) -> str:
    """Safe, short artwork phrase; never search long Etsy titles or blank brands."""
    if (not isinstance(title, str) or re.search(r"[\x00-\x1f\x7f]", title)
            or chr(92) in title):
        return ""
    # Seller punctuation and registered-trademark marks are not artwork keywords.
    phrase = title.replace("\u00ae", " ").replace("\u2122", " ").replace("\u2019", "'")
    phrase = re.split(r"[,|;:\n\r]", phrase, maxsplit=1)[0]
    phrase = _SELLER_BRANDS.sub(" ", phrase)
    phrase = re.sub(r"[^A-Za-z0-9 '&/\-]", " ", phrase)
    phrase = re.sub(r"\s+", " ", phrase).strip(" -")
    match = _FIRST_GARMENT.search(phrase)
    if match and len(phrase[:match.start()].strip(" -")) >= 3:
        phrase = phrase[:match.start()].strip(" -")
    for _ in range(2):
        shortened = _GARMENT_SUFFIX.sub("", phrase).strip(" -")
        if shortened == phrase or len(shortened) < 3:
            break
        phrase = shortened
    if len(phrase) > 70:
        phrase = " ".join(phrase.split()[:8])[:70].strip(" -")
    if not _SAFE_KEYWORD.fullmatch(phrase) or not 3 <= len(phrase) <= 100:
        return ""
    return phrase.casefold()


def empty_evidence(query: str, status: str = "unavailable") -> dict:
    return {
        "status": status,
        "source": SOURCE,
        "marketplace": "etsy",
        "query": query,
        "as_of_date": None,
        "median_data_age_days": None,
        "total_listings": None,
        "total_sellers": None,
        "competition_level": None,
        "opportunity_score": None,
        "dataset_note": "Indexed Etsy subset; not TikTok Shop search data.",
    }


def _number(value: Any, *, integer: bool = False, ceiling: float = 100_000_000):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > ceiling:
        return None
    return int(number) if integer else round(number, 2)


def normalize_research(payload: Any, keyword: str) -> dict:
    """Only a small, typed, provenance-aware portion of provider data is stored."""
    if not isinstance(payload, dict) or payload.get("source") != SOURCE:
        return empty_evidence(keyword)
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    stamp = snapshot.get("as_of_date")
    if not isinstance(stamp, str):
        stamp = None
    else:
        try:
            stamp = date.fromisoformat(stamp).isoformat()
        except ValueError:
            stamp = None
    if not stamp:
        # A real source without a usable snapshot is not verified market evidence.
        return empty_evidence(keyword)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else None
    evidence = empty_evidence(keyword, "connected" if stats else "no_data")
    evidence["as_of_date"] = stamp
    evidence["median_data_age_days"] = _number(
        snapshot.get("median_data_age_days"), integer=True, ceiling=36500)
    if not stats:
        return evidence
    evidence["total_listings"] = _number(stats.get("total_listings"), integer=True)
    evidence["total_sellers"] = _number(stats.get("total_sellers"), integer=True)
    level = stats.get("competition_level")
    if isinstance(level, str) and re.fullmatch(r"[a-zA-Z _-]{2,35}", level):
        evidence["competition_level"] = level
    evidence["opportunity_score"] = _number(stats.get("opportunity_score"), ceiling=100)
    return evidence


def _mcp_body(response: httpx.Response) -> dict:
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ValueError("oversized_mcp_response")
    raw = response.text
    if raw.lstrip().startswith("event:") or raw.lstrip().startswith("data:"):
        events = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
        raw = next((event for event in events if event.startswith("{")), "{}")
    message = json.loads(raw)
    if not isinstance(message, dict) or message.get("error"):
        raise ValueError("invalid_mcp_envelope")
    result = message.get("result")
    if not isinstance(result, dict) or result.get("isError"):
        raise ValueError("mcp_tool_failed")
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for part in result.get("content") or []:
        if isinstance(part, dict) and part.get("type") == "text":
            document = json.loads(part.get("text") or "")
            if isinstance(document, dict):
                return document
    raise ValueError("missing_mcp_tool_result")


async def research_etsy_keyword(
    title: str, *, enabled: bool = True, bearer_token: str = "",
    timeout_seconds: float = 8.0, transport: httpx.AsyncBaseTransport | None = None,
    raw_query: bool = False,
) -> dict:
    """One bounded MCP call, with short failure caching; failures never block Gemini."""
    query = title.casefold().strip() if raw_query and _SAFE_KEYWORD.fullmatch(title) else research_query(title)
    if not enabled or not query:
        return empty_evidence(query, "disabled" if not enabled else "no_data")
    # Cached data is public, but auth-backed and anonymous views never share keys.
    cache_key = ("authenticated:" if bearer_token else "public:") + query
    if transport is None:
        with _CACHE_LOCK:
            entry = _CACHE.get(cache_key)
            if entry and entry[0] > time.monotonic():
                return dict(entry[1])
    outcome = empty_evidence(query)
    try:
        headers = {"Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        if bearer_token:
            headers["Authorization"] = "Bearer " + bearer_token
        request_body = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "ytrends_research_keyword", "arguments": {
                "keyword": query, "ltype": "physical", "response_format": "concise",
            }},
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 3.0)),
            transport=transport, trust_env=False, follow_redirects=False,
        ) as client:
            response = await client.post(MCP_URL, headers=headers, json=request_body)
        response.raise_for_status()
        outcome = normalize_research(_mcp_body(response), query)
    except (httpx.HTTPError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        # No raw remote text, provider credentials or error bodies are logged.
        outcome = empty_evidence(query)
    if transport is None:
        ttl = (CACHE_VALID_SECONDS if outcome["status"] == "connected" else
               CACHE_NO_DATA_SECONDS if outcome["status"] == "no_data" else
               CACHE_FAILURE_SECONDS)
        with _CACHE_LOCK:
            if len(_CACHE) >= CACHE_LIMIT:
                oldest = min(_CACHE, key=lambda key: _CACHE[key][0])
                _CACHE.pop(oldest, None)
            _CACHE[cache_key] = (time.monotonic() + ttl, dict(outcome))
    return outcome


def keyword_variants(title: str) -> list[str]:
    """Bounded related garment queries; candidate terms are NOT market facts."""
    core = research_query(title)
    if not core:
        return []
    lower_title = title.casefold()
    # Professional apparel queries need buyer-intent synonyms, not five
    # near-duplicates of the seller's first generic noun phrase.
    if ("occupational therapy" in lower_title or
            "occupational therapist" in lower_title or
            re.search(r"\bota\b", lower_title)):
        return [
            "ota shirt",
            "occupational therapy shirt",
            "occupational therapist shirt",
            "occupational therapy gift",
            "occupational therapy sweatshirt",
        ]
    variants = [core]
    garment = next((g for g in ("sweatshirt", "crewneck", "hoodie", "shirt")
                    if re.search(r"\b" + g + r"\b", title, re.IGNORECASE)), "sweatshirt")
    variants.append(f"{core} {garment}")
    if garment == "sweatshirt":
        variants.append(f"{core} crewneck")
    else:
        variants.append(f"{core} sweatshirt")
    if "santa" in lower_title:
        variants.extend([f"santa {garment}", "christmas sweatshirt"])
    elif "christmas" in lower_title or "xmas" in lower_title or "nutcracker" in lower_title:
        variants.extend([f"christmas {garment}", "christmas crewneck"])
    elif "valentine" in lower_title or "heart" in lower_title:
        variants.extend([f"valentine {garment}", "valentine sweatshirt"])
    elif "birthday" in lower_title:
        variants.extend([f"birthday {garment}", "birthday shirt"])
    else:
        # Shorter grounded synonym searches are preferable to invented
        # marketing prefixes ("vintage ...", "retro ...") on every product.
        tokens = core.split()
        core_short = " ".join(tokens[:3]) if len(tokens) > 3 else core
        variants.extend([f"{core_short} {garment}", f"{core_short} sweatshirt"])
    return list(dict.fromkeys(q for q in variants
                              if 3 <= len(q) <= 100 and _SAFE_KEYWORD.fullmatch(q)))[:5]


async def research_etsy_keyword_set(title: str, **options: Any) -> dict:
    """Query up to five bounded variants; preserve single-result API fields."""
    queries = keyword_variants(title)
    if not queries:
        return empty_evidence("", "no_data")
    # Sequential and cached: do not saturate the public MCP endpoint.
    results = [await research_etsy_keyword(q, raw_query=True, **options) for q in queries]
    primary = dict(results[0])
    primary["keywords"] = [
        {"keyword": query, "status": row["status"],
         "listings": row.get("total_listings"),
         "competition": row.get("competition_level"),
         "as_of_date": row.get("as_of_date")}
        for query, row in zip(queries, results)
    ]
    if primary["status"] != "connected":
        successful = next((row for row in results if row["status"] == "connected"), None)
        if successful:
            primary.update({field: successful.get(field) for field in (
                "as_of_date", "median_data_age_days", "total_listings",
                "total_sellers", "competition_level")})
            primary["status"] = "connected"
    return primary


def prompt_context(evidence: dict) -> str:
    """Only bounded typed data enters the model prompt; no provider free text."""
    if evidence.get("status") != "connected":
        return "\nYTrends Etsy research: no verified market evidence for this keyword."
    rows = {
        "source": SOURCE, "marketplace": "Etsy (not TikTok Shop)",
        "keyword": evidence.get("query"), "snapshot_as_of": evidence.get("as_of_date"),
        "median_data_age_days": evidence.get("median_data_age_days"),
        "indexed_listings": evidence.get("total_listings"),
        "indexed_sellers": evidence.get("total_sellers"),
        "keyword_candidates": [
            {"query": row.get("keyword"), "indexed_listings": row.get("listings"),
             "competition": row.get("competition")}
            for row in (evidence.get("keywords") or [])[:5]
            if isinstance(row, dict)
        ],
    }
    return (
        "\nYTrends EVIDENCE (read-only Etsy snapshot, NOT TikTok Shop search): "
        + json.dumps(rows, ensure_ascii=True)
        + "\nUse it only to understand audience/occasion relevance. "
        "Do not claim TikTok keyword volume, ranking, demand, conversion, "
        "sales or marketplace endorsements. The photo remains authoritative."
    )
