from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import httpx


DEFAULT_BASE_URL = "https://creative-assets.ddns.net"


def _keywords_from_file(path: str | None) -> list[str]:
    if not path:
        return []
    raw = Path(path).read_text(encoding="utf-8").strip()
    if not raw:
        return []
    if raw.startswith("["):
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("Keyword JSON file must contain an array.")
        return [str(value) for value in parsed]
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = " ".join(str(value or "").split()).strip()
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def resolve_keyword_volume(
    *,
    base_url: str,
    agent_id: str,
    token: str,
    keywords: list[str],
    force: bool = False,
) -> dict:
    endpoint = (
        base_url.rstrip("/")
        + "/api/v1/realistic-review-ugc/scout-agents/"
        + agent_id
        + "/keyword-analysis/resolve"
    )
    with httpx.Client(timeout=httpx.Timeout(30.0, connect=8.0)) as client:
        response = client.post(
            endpoint,
            headers={"Authorization": "Bearer " + token},
            json={
                "keywords": keywords,
                "force": force,
            },
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Creative Asset Manager returned an invalid response.")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Submit quote-scout keywords to Creative Asset Manager. "
            "The server resolves Google Ads volume through AEBrowse, caches it, "
            "and stores the results for Stage 0."
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("RRUGC_BASE_URL", DEFAULT_BASE_URL),
    )
    parser.add_argument(
        "--agent-id",
        default=os.getenv("RRUGC_AGENT_ID", ""),
    )
    parser.add_argument(
        "--token",
        default=os.getenv("RRUGC_SCOUT_TOKEN", ""),
    )
    parser.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Keyword to resolve. Repeat this option for multiple keywords.",
    )
    parser.add_argument(
        "--keywords-file",
        help="UTF-8 file containing one keyword per line or a JSON array.",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    if not args.agent_id:
        parser.error("--agent-id or RRUGC_AGENT_ID is required")
    if not args.token:
        parser.error("--token or RRUGC_SCOUT_TOKEN is required")

    keywords = _dedupe(list(args.keyword) + _keywords_from_file(args.keywords_file))
    if not keywords:
        parser.error("At least one --keyword or --keywords-file entry is required")
    if len(keywords) > 50:
        parser.error("At most 50 unique keywords may be submitted per request")

    try:
        payload = resolve_keyword_volume(
            base_url=args.base_url,
            agent_id=args.agent_id,
            token=args.token,
            keywords=keywords,
            force=args.force,
        )
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        print(f"Keyword volume request failed: {exc}", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(
        "Keyword volume:",
        f"requested={payload.get('requested', 0)}",
        f"provider={payload.get('provider_requested', 0)}",
        f"cached={payload.get('cached', 0)}",
    )
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        volume = int(item.get("search_volume") or 0)
        competition = str(item.get("competition") or "-")
        cpc_low = item.get("cpc_low")
        cpc_high = item.get("cpc_high")
        cpc = (
            f"${float(cpc_low):.2f}-${float(cpc_high):.2f}"
            if cpc_low is not None and cpc_high is not None
            else "-"
        )
        print(
            f"{volume:>8,}/mo  {competition:<8}  {cpc:<13}  "
            + str(item.get("keyword") or "")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
