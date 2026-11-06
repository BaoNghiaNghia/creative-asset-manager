"""Read-only diagnosis of 4 Scout profiles; prints no URLs, cookies or tokens."""
from __future__ import annotations
import json
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILES = [
    ("review", "pinterest-profile/logs/pinterest-scout.jsonl"),
    ("keyword", "pinterest-profile-keyword/logs/keyword-scout.jsonl"),
    ("cap_stack", "pinterest-profile-cap-stack/logs/pinterest-scout.jsonl"),
    ("tiktokshop", "pinterest-profile-tiktokshop/logs/pinterest-scout.jsonl"),
]
def quant(values, percent):
    values = sorted(values)
    if not values: return None
    return round(values[min(len(values) - 1, int((len(values) - 1) * percent))], 1)

now = datetime.now(timezone.utc)
cutoff = now - timedelta(hours=24)
for mode, rel in PROFILES:
    file = ROOT / rel
    counts = Counter()
    values = {}
    first = latest = None
    recent = 0
    try:
        with file.open(encoding="utf-8", errors="replace") as inp:
            for line in inp:
                try: event = json.loads(line)
                except (ValueError, TypeError): continue
                stamp = event.get("ts") or event.get("timestamp")
                try: when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                except (ValueError, TypeError): continue
                if when.tzinfo is None: when = when.replace(tzinfo=timezone.utc)
                first = min(first or when, when)
                latest = max(latest or when, when)
                if when < cutoff: continue
                recent += 1
                name = str(event.get("event") or "")
                counts[name] += 1
                for key in ("wait_ms", "duration_ms", "timeout_ms", "images_loaded", "images_visible"):
                    if key in event and isinstance(event[key], (int, float)):
                        values.setdefault((name, key), []).append(event[key])
    except FileNotFoundError:
        print(mode, "MISSING_LOG", rel)
        continue
    print("\nPROFILE", mode, "recent_events", recent,
          "last_seen_utc", latest.isoformat() if latest else None)
    selected = [(name, count) for name, count in counts.most_common()
                if any(term in name for term in
                       ("network", "timeout", "retry", "gate", "ready", "cam_request", "pin_detail"))]
    print("COUNTS", json.dumps(selected[:28], ensure_ascii=True))
    for (name, key), samples in sorted(values.items()):
        if name.startswith("scout_network_") or name in {
            "cam_request", "pinterest_pin_detail_retry", "review_browser_step_timeout",
        }:
            print("METRIC", name, key, "n", len(samples),
                  "p50", quant(samples, .50), "p95", quant(samples, .95),
                  "max", round(max(samples), 1))
print("\nUTC_NOW", now.isoformat())
