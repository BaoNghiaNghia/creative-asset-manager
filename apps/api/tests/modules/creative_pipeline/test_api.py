from app.main import create_app

def test_creative_pipeline_routes_are_registered():
    paths = create_app().openapi()["paths"]
    assert "/api/v1/creative-pipeline/groups" in paths
    assert "/api/v1/creative-pipeline/listings/{listing_id}" in paths
    assert "/api/v1/creative-pipeline/runs/{run_id}/cancel" in paths
    assert "/api/v1/creative-pipeline/nodes/{node_id}/retry" in paths

def test_unsafe_capabilities_are_disabled():
    from app.modules.creative_pipeline.api_service import CreativePipelineApiService
    values = CreativePipelineApiService.capabilities()
    assert values["scan_group"] is True
    assert values["retry_wait_node"] is True
    assert values["cancel_run"] is True
    assert values["terminal_failed_retry"] is False
    assert values["regenerate_idea"] is False
    assert values["regenerate_prompt"] is False
    assert values["generate_another_video"] is False
