import threading
from collections import Counter, defaultdict

class AiMetrics:
    def __init__(self):
        self._lock = threading.Lock(); self._counters = Counter(); self._latencies = defaultdict(list)
    def increment(self, metric: str, *, provider: str = "unknown", outcome: str = "unknown", value: int = 1):
        with self._lock: self._counters[(metric, provider, outcome)] += value
    def latency(self, provider: str, outcome: str, milliseconds: int):
        with self._lock: self._latencies[(provider, outcome)].append(max(0, milliseconds))
    def snapshot(self):
        with self._lock:
            return {
                "counters": [{"metric": k[0], "provider": k[1], "outcome": k[2], "value": v} for k,v in sorted(self._counters.items())],
                "latency": [{"provider": k[0], "outcome": k[1], "count": len(v), "sum_ms": sum(v)} for k,v in sorted(self._latencies.items())],
            }
AI_METRICS = AiMetrics()
