from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config
from app.modules.visual_search.elasticsearch import VisualSearchElasticsearchIndex
from app.modules.visual_search.index_handler import VisualIndexSyncJobHandler
from app.modules.visual_search.model_spec import VISUAL_SEARCH_V2_DESCRIPTOR


def _context(provider):
    return SimpleNamespace(
        job=SimpleNamespace(
            tenant_id="tenant-a",
            entity_id="asset-1",
            payload={"operation": "reconcile_retired_source"},
        ),
        dependencies=SimpleNamespace(
            resources={"visual_index_provider": provider},
            session_factory=Mock(),
        ),
        cancellation_requested=SimpleNamespace(is_set=lambda: False),
        shutdown_requested=SimpleNamespace(is_set=lambda: False),
    )


def test_retirement_does_not_delete_asset_with_active_source():
    provider = VisualSearchElasticsearchIndex(
        ElasticsearchV3Config("http://elasticsearch.test"),
        VISUAL_SEARCH_V2_DESCRIPTOR,
    )
    handler = VisualIndexSyncJobHandler()
    context = _context(provider)
    with patch("app.modules.visual_search.index_handler.visual_index_job_enabled", return_value=True), \
         patch.object(handler, "_has_active_source", return_value=True), \
         patch.object(provider, "delete_asset") as delete, \
         patch.object(handler, "_run") as run:
        result = handler._handle(context, {})
    assert result.outcome.value == "completed"
    delete.assert_not_called()
    run.assert_not_called()


def test_retirement_deletes_only_when_no_live_source_remains():
    provider = VisualSearchElasticsearchIndex(
        ElasticsearchV3Config("http://elasticsearch.test"),
        VISUAL_SEARCH_V2_DESCRIPTOR,
    )
    handler = VisualIndexSyncJobHandler()
    context = _context(provider)
    with patch("app.modules.visual_search.index_handler.visual_index_job_enabled", return_value=True), \
         patch.object(handler, "_has_active_source", return_value=False), \
         patch.object(provider, "delete_asset") as delete, \
         patch.object(handler, "_run", side_effect=lambda _context, coro: coro.close()) as run:
        result = handler._handle(context, {})
    assert result.outcome.value == "completed"
    delete.assert_called_once_with(tenant_id="tenant-a", asset_id="asset-1")
    assert run.call_count == 1
    assert run.call_args.args[0] is context


def test_retirement_source_check_is_tenant_scoped():
    session = Mock()
    session.scalar.return_value = "linked-source"
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    context = _context(Mock())
    context.dependencies.session_factory.return_value = session
    assert VisualIndexSyncJobHandler._has_active_source(context) is True
    statement = session.scalar.call_args.args[0]
    query = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "tenant-a" in query
    assert "asset-1" in query
    assert "deleted_at IS NULL" in query
    assert "external_sources.status = 'active'" in query
