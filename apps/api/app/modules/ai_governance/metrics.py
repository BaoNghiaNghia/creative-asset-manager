import threading
from collections import Counter, defaultdict

_ALLOWED_PROVIDERS = {"gemini", "openai", "unknown"}
_ALLOWED_MODES = {"single", "batch", "unknown"}
_ALLOWED_OUTCOMES = {
    "completed","failed","reserved","unlimited","open","closed","budget_blocked",
    "missing_cost_rate","rate_limit","invalid_metadata","provider_unavailable",
    "batch_expired","batch_submission_ambiguous","ai_emergency_stop","global_ai_stop",
    "daily_budget_exceeded","monthly_budget_exceeded","budget_currency_mismatch",
    "ai_provider_disabled","ai_provider_paused","ai_provider_mode_disabled",
}
def _bounded(value: str, allowed: set[str]) -> str:
    return value if value in allowed else "other"

class AiMetrics:
    def __init__(self):
        self._lock = threading.Lock(); self._counters = Counter(); self._latencies = defaultdict(list)
    def increment(self, metric: str, *, provider: str = "unknown", mode: str = "unknown",
                  outcome: str = "unknown", value: int = 1):
        key=(metric,_bounded(provider,_ALLOWED_PROVIDERS),_bounded(mode,_ALLOWED_MODES),
             _bounded(outcome,_ALLOWED_OUTCOMES))
        with self._lock: self._counters[key] += value
    def latency(self, provider: str, outcome: str, milliseconds: int, mode: str = "single"):
        key=(_bounded(provider,_ALLOWED_PROVIDERS),_bounded(mode,_ALLOWED_MODES),
             _bounded(outcome,_ALLOWED_OUTCOMES))
        with self._lock: self._latencies[key].append(max(0, milliseconds))
    def snapshot(self):
        with self._lock:
            return {
                "counters": [{"metric": k[0], "provider": k[1], "mode": k[2],
                              "outcome": k[3], "value": v}
                             for k,v in sorted(self._counters.items())],
                "latency": [{"provider": k[0], "mode": k[1], "outcome": k[2],
                             "count": len(v), "sum_ms": sum(v)}
                            for k,v in sorted(self._latencies.items())],
            }
AI_METRICS = AiMetrics()
