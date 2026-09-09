from app.modules.visual_search.metrics import VisualSearchMetrics

def test_snapshot_uses_only_bounded_labels():
    metrics = VisualSearchMetrics()
    metrics.observe_request(kind="asset", outcome="success", total_ms=12, result_count=0)
    metrics.observe_indexing("success")
    snapshot = metrics.snapshot()
    assert snapshot["visual_search_requests_total"] == [
        {"kind": "asset", "outcome": "empty", "value": 1},
        {"kind": "asset", "outcome": "success", "value": 1},
    ]
    assert snapshot["visual_index_jobs_total"] == [{"outcome": "success", "value": 1}]
