import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.core.config import Settings
from app.operations import video_search_index_cli as cli


class _Index:
    created = []
    ensured = []
    switched = []
    mappings = {}
    aliases = {}

    def __init__(self, config):
        self.config = config
        self.read_alias = f"{config.index_prefix}-video-v3-read"
        self.write_alias = f"{config.index_prefix}-video-v3-write"

    async def aclose(self):
        return None

    def physical_index_name(self, version):
        return f"{self.config.index_prefix}-video-v3-{version}"

    async def ensure_index(self, version):
        self.ensured.append(version)
        return self.physical_index_name(version)

    async def index_mapping(self, name):
        return self.mappings[name]

    async def switch_aliases(self, target):
        self.switched.append(target)

    async def alias_indices(self):
        target = self.switched[-1] if self.switched else ""
        return self.aliases.get(target, {"read": {target}, "write": {target}})


class VideoSearchIndexCliTest(unittest.TestCase):
    def setUp(self):
        _Index.created = []
        _Index.ensured = []
        _Index.switched = []
        _Index.mappings = {}
        _Index.aliases = {}
        self.settings = Settings(ELASTICSEARCH_URL="http://elasticsearch.test")
        self.args = dict(version="20260819", index_prefix="creative-assets", elasticsearch_url=None)

    def execute(self, **values):
        return asyncio.run(cli.execute(SimpleNamespace(**self.args, **values)))

    def test_dry_run_is_video_v3_only_and_does_not_mutate(self):
        with (
            patch.object(cli, "get_settings", return_value=self.settings),
            patch.object(cli, "VideoSearchElasticsearchIndex", _Index),
        ):
            result = self.execute(dry_run=True, apply=False, confirmed=False)
        self.assertEqual(result["target_index"], "creative-assets-video-v3-20260819")
        self.assertEqual(result["read_alias"], "creative-assets-video-v3-read")
        self.assertEqual(result["write_alias"], "creative-assets-video-v3-write")
        self.assertFalse(result["aliases_switched"])
        self.assertEqual(_Index.ensured, [])
        self.assertEqual(_Index.switched, [])

    def test_apply_requires_explicit_confirmation(self):
        with self.assertRaisesRegex(ValueError, "--apply requires --confirmed"):
            self.execute(dry_run=False, apply=True, confirmed=False)

    def test_apply_ensures_nested_mapping_and_switches_video_aliases_only(self):
        target = "creative-assets-video-v3-20260819"
        _Index.mappings[target] = {target: {"mappings": {"properties": {"segments": {"type": "nested"}}}}}
        with (
            patch.object(cli, "get_settings", return_value=self.settings),
            patch.object(cli, "VideoSearchElasticsearchIndex", _Index),
        ):
            result = self.execute(dry_run=False, apply=True, confirmed=True)
        self.assertEqual(_Index.ensured, ["20260819"])
        self.assertEqual(_Index.switched, [target])
        self.assertTrue(result["segments_nested"])
        self.assertTrue(result["aliases_switched"])
        self.assertNotIn("creative-assets-v3-read", (result["read_alias"], result["write_alias"]))

    def test_incompatible_mapping_refuses_alias_switch(self):
        target = "creative-assets-video-v3-20260819"
        _Index.mappings[target] = {target: {"mappings": {"properties": {"segments": {"type": "object"}}}}}
        with (
            patch.object(cli, "get_settings", return_value=self.settings),
            patch.object(cli, "VideoSearchElasticsearchIndex", _Index),
        ):
            with self.assertRaisesRegex(ValueError, "incompatible segments mapping"):
                self.execute(dry_run=False, apply=True, confirmed=True)
        self.assertEqual(_Index.switched, [])


if __name__ == "__main__":
    unittest.main()
