from app.modules.visual_search.metrics import VisualSearchMetrics

from concurrent.futures import ThreadPoolExecutor
import pytest

def test_snapshot_uses_only_bounded_labels():
    metrics = VisualSearchMetrics()
    metrics.observe_request(kind="asset", outcome="success", total_ms=12, result_count=0)
    metrics.observe_indexing("success")
    snapshot = metrics.snapshot()
    assert snapshot["visual_search_requests_total"] == [
        {"kind": "asset", "outcome": "empty", "value": 1},
    ]
    assert snapshot["visual_index_jobs_total"] == [{"outcome": "success", "value": 1}]


def test_latency_window_is_bounded_and_percentiles_are_deterministic():
    metrics = VisualSearchMetrics(window_size=2)
    for value in (1, 9, 20):
        metrics.observe_request(kind="asset", outcome="success", total_ms=value, result_count=1)
    row = metrics.snapshot()["query_latency"][0]
    assert row["sample_count"] == 2
    assert row["p50_ms"] == 9
    assert row["p95_ms"] == 20
    assert row["max_ms"] == 20


@pytest.mark.parametrize(
    ("values", "p50", "p95", "maximum"),
    [
        ([0], 0, 0, 0),
        ([1, 9], 1, 9, 9),
        ([1, 3, 5], 3, 5, 5),
        ([1, 3, 5, 7], 3, 7, 7),
    ],
)
def test_percentiles_cover_zero_one_two_odd_and_even(values, p50, p95, maximum):
    metrics = VisualSearchMetrics()
    for value in values:
        metrics.observe_request(
            kind="upload", outcome="success", total_ms=value, result_count=1
        )
    row = metrics.snapshot()["query_latency"][0]
    assert (row["p50_ms"], row["p95_ms"], row["max_ms"]) == (p50, p95, maximum)


def test_percentile_without_samples_is_none():
    assert VisualSearchMetrics._percentile([], 0.50) is None
    assert VisualSearchMetrics._percentile([], 0.95) is None


def test_default_window_evicts_old_samples_after_512_observations():
    metrics = VisualSearchMetrics()
    for value in range(513):
        metrics.observe_request(
            kind="crop", outcome="success", total_ms=value, result_count=1
        )
    row = metrics.snapshot()["query_latency"][0]
    assert row["sample_count"] == 512
    assert row["p50_ms"] == 256
    assert row["p95_ms"] == 487
    assert row["max_ms"] == 512


def test_snapshot_sorting_and_stage_labels_are_deterministic_and_bounded():
    metrics = VisualSearchMetrics()
    metrics.observe_request(
        kind="upload",
        outcome="success",
        total_ms=10,
        result_count=1,
        stages={"knn_ms": 3, "prepare_image_ms": 2},
    )
    metrics.observe_request(
        kind="asset", outcome="error", total_ms=4, result_count=0
    )
    snapshot = metrics.snapshot()
    assert snapshot["visual_search_requests_total"] == [
        {"kind": "asset", "outcome": "error", "value": 1},
        {"kind": "upload", "outcome": "success", "value": 1},
    ]
    assert [(row["kind"], row["stage"]) for row in snapshot["query_latency"]] == [
        ("asset", "request_total_ms"),
        ("upload", "knn_ms"),
        ("upload", "prepare_image_ms"),
        ("upload", "request_total_ms"),
    ]
    with pytest.raises(ValueError):
        metrics.observe_request(
            kind="tenant-id", outcome="success", total_ms=1, result_count=1
        )
    with pytest.raises(ValueError):
        metrics.observe_request(
            kind="asset", outcome="success", total_ms=1, result_count=1,
            stages={"asset-id": 1},
        )


def test_empty_result_is_exactly_one_terminal_observation():
    metrics = VisualSearchMetrics()
    metrics.observe_request(
        kind="hybrid", outcome="success", total_ms=1, result_count=0
    )
    assert metrics.snapshot()["visual_search_requests_total"] == [
        {"kind": "hybrid", "outcome": "empty", "value": 1}
    ]


def test_concurrent_observations_are_counted_without_unbounded_samples():
    metrics = VisualSearchMetrics(window_size=32)

    def observe(_value):
        metrics.observe_request(
            kind="asset", outcome="success", total_ms=1, result_count=1
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(observe, range(200)))
    snapshot = metrics.snapshot()
    assert snapshot["visual_search_requests_total"] == [
        {"kind": "asset", "outcome": "success", "value": 200}
    ]
    assert snapshot["query_latency"][0]["sample_count"] == 32
