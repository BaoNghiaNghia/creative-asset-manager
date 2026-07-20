import unittest

from app.operations.search_cli import parser


class SearchCliTest(unittest.TestCase):
    def test_required_commands_and_filters_parse(self) -> None:
        for command in (
            "search:rebuild-projections",
            "search:reindex-assets",
            "search:rebuild-and-reindex",
        ):
            args = parser().parse_args(
                [
                    command,
                    "--tenant-id",
                    "tenant-a",
                    "--metadata-profile",
                    "default",
                    "--current-projection-version",
                    "projection-v1",
                    "--asset-id",
                    "asset-1",
                    "--only-missing",
                    "--dry-run",
                    "--page-size",
                    "25",
                ]
            )
            self.assertEqual(args.command, command)
            self.assertEqual(args.tenant_id, "tenant-a")
            self.assertEqual(args.page_size, 25)
            self.assertTrue(args.dry_run)

    def test_cancel_command_parses(self) -> None:
        args = parser().parse_args(
            ["search:cancel", "--tenant-id", "tenant-a", "--run-id", "run-1"]
        )
        self.assertEqual(args.run_id, "run-1")


if __name__ == "__main__":
    unittest.main()
