#!/usr/bin/env python3
"""Summarize authorized, PII-free Etsy Spy app telemetry from journal lines.

Read-only. Example:
  journalctl -u creative-asset-manager-api --since '24 hours ago' -o cat |
    python3 scripts/cam-etsy-perf-report.py --hours 24

Only *API-observed* 5xx are counted. An Nginx-generated 502 does not reach
the API and must be checked from reverse-proxy access/error telemetry.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
import sys
import time

BOUNDS_MS = (50, 100, 250, 500, 1000, 2000, 5000, 10000, 30000)
KNOWN_ROUTES = {
    "summary", "listings", "observed_days", "observed_day_groups",
    "image_overview", "design_thumbnail",
}


@dataclass
class Totals:
    requests: int = 0
    errors_5xx: int = 0
    errors_502: int = 0
    cache_hit: int = 0
    cache_miss: int = 0
    coalesced: int = 0
    histogram: list[int] = field(default_factory=lambda: [0] * 10)


def parse(lines, *, earliest: int) -> dict[str, Totals]:
    totals: dict[str, Totals] = defaultdict(Totals)
    for line in lines:
        if "etsy_perf_window " not in line:
            continue
        fields = dict(
            token.split("=", 1) for token in
            line.split("etsy_perf_window ", 1)[1].split()
            if "=" in token
        )
        route = fields.get("route")
        if route not in KNOWN_ROUTES:
            continue
        try:
            start = int(fields["window_start"])
            if start < earliest:
                continue
            histogram = [int(value) for value in fields["histogram"].split(",")]
            if len(histogram) != len(BOUNDS_MS) + 1 or any(value < 0 for value in histogram):
                continue
            counters = {
                field: int(fields[field])
                for field in ("requests", "errors_5xx", "errors_502",
                              "cache_hit", "cache_miss", "coalesced")
            }
            if any(value < 0 for value in counters.values()):
                continue
            if sum(histogram) != counters["requests"]:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        row = totals[route]
        for field, value in counters.items():
            setattr(row, field, getattr(row, field) + value)
        for index, value in enumerate(histogram):
            row.histogram[index] += value
    return dict(totals)


def p95_upper(stats: Totals) -> str:
    if not stats.requests:
        return "-"
    rank = (stats.requests * 95 + 99) // 100
    count = 0
    for index, amount in enumerate(stats.histogram):
        count += amount
        if count >= rank:
            return str(BOUNDS_MS[index]) if index < len(BOUNDS_MS) else ">30000"
    return "unknown"


def report(totals: dict[str, Totals]) -> str:
    if not totals:
        return "No completed five-minute Etsy Spy telemetry windows in this range."
    lines = [
        "route             requests  5xx  api502  p95_le_ms  hit  miss  joined",
    ]
    for route, row in sorted(totals.items()):
        lines.append(
            f"{route:17} {row.requests:8} {row.errors_5xx:4} "
            f"{row.errors_502:7} {p95_upper(row):>10} "
            f"{row.cache_hit:4} {row.cache_miss:5} {row.coalesced:7}"
        )
    lines.append("Note: api502 excludes Nginx/proxy-generated 502; p95 is a histogram upper bound.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    if not 1 <= args.hours <= 168:
        parser.error("--hours must be 1..168")
    earliest = int(time.time() - args.hours * 3600)
    print(report(parse(sys.stdin, earliest=earliest)))


if __name__ == "__main__":
    main()
