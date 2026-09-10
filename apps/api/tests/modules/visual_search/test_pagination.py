from app.core.config import Settings
from app.modules.visual_search.elasticsearch import VisualSearchHit
from app.modules.visual_search.router import _cursor_offset, _next_cursor, _rank_hits


def _settings() -> Settings:
    return Settings(VISUAL_SEARCH_RANKING_MAX_PER_SOURCE=10)


def _hits(*asset_ids: str):
    return [VisualSearchHit(asset_id, "tenant-a", asset_id, 1 - index / 100, asset_id * 64, f"source-{index}") for index, asset_id in enumerate(asset_ids)]


def _page(hits, offset: int, limit: int):
    page, has_more = _rank_hits(hits, offset=offset, limit=limit, settings=_settings())
    return page, _next_cursor(offset, len(page), has_more, fingerprint="f")


def test_cursor_advances_by_consumed_candidates_without_skipping():
    hits = _hits("a", "b", "c")
    first, cursor = _page(hits, 0, 1)
    second, cursor2 = _page(hits, _cursor_offset(cursor, fingerprint="f"), 1)
    third, cursor3 = _page(hits, _cursor_offset(cursor2, fingerprint="f"), 1)
    assert [hit.asset_id for hit in [*first, *second, *third]] == ["a", "b", "c"]
    assert cursor3 is None


def test_limit_two_cursor_consumes_two_post_diversification_candidates():
    hits = _hits("a", "b", "c", "d")
    first, cursor = _page(hits, 0, 2)
    second, cursor2 = _page(hits, _cursor_offset(cursor, fingerprint="f"), 2)
    assert [hit.asset_id for hit in first] == ["a", "b"]
    assert [hit.asset_id for hit in second] == ["c", "d"]
    assert cursor2 is None


def test_diversification_happens_before_cursor_pagination():
    hits = [
        VisualSearchHit("1", "tenant-a", "a", 1.0, "a" * 64, "source-a"),
        VisualSearchHit("2", "tenant-a", "b", 0.9, "a" * 64, "source-b"),
        VisualSearchHit("3", "tenant-a", "c", 0.8, "c" * 64, "source-c"),
    ]
    first, cursor = _page(hits, 0, 1)
    second, cursor2 = _page(hits, _cursor_offset(cursor, fingerprint="f"), 1)
    assert [hit.asset_id for hit in [*first, *second]] == ["a", "c"]
    assert cursor2 is None
