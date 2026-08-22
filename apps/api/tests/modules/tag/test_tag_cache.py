from unittest.mock import Mock, patch

from app.modules.tag.cache import tag_catalog_cache
from app.modules.tag.router import tags
from app.modules.tag.schema import Tag


def test_tag_catalog_is_cached_for_repeated_reads():
    tag_catalog_cache.clear()
    service = Mock()
    service.list_tags.return_value = [
        Tag(id="tag-a", name="Tag A", is_system=True)
    ]
    with patch("app.modules.tag.router.TagService", return_value=service):
        first = tags(session=object())
        second = tags(session=object())
    assert first == second
    service.list_tags.assert_called_once()
    tag_catalog_cache.clear()
