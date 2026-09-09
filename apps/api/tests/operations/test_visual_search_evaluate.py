from app.operations.visual_search_evaluate import evaluate

def test_evaluate_quality_metrics_and_deduplicates():
    result = evaluate({"q1": {"a": 3, "b": 1}, "q2": {"c": 2}}, {"q1": ["a", "x", "a"], "q2": []}, k=2)
    assert result == {"queries": 2, "precision_at_2": 0.25, "recall_at_2": 0.25, "ndcg_at_2": 0.45866, "empty_result_rate": 0.5}

def test_evaluate_rejects_unknown_result_query():
    try:
        evaluate({"q": {"asset": 1}}, {"other": []})
    except ValueError as error:
        assert "unknown" in str(error)
    else:
        raise AssertionError()
