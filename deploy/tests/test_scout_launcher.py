from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BATCH = ROOT / "START_SCOUT.bat"
KEYWORD_CMD = ROOT / "START_KEYWORD_SCOUT.cmd"
UPDATER = ROOT / "scripts" / "start_scout_auto_update.ps1"


class ScoutLauncherContractTests(unittest.TestCase):
    def test_batch_can_bootstrap_missing_updater_from_origin_main(self) -> None:
        source = BATCH.read_text()
        self.assertIn('if not exist "%SCOUT_BOOTSTRAP%"', source)
        self.assertIn(
            "git cat-file -e origin/main:scripts/start_scout_auto_update.ps1",
            source,
        )
        self.assertIn(
            'git show origin/main:scripts/start_scout_auto_update.ps1 > "%SCOUT_BOOTSTRAP%.tmp"',
            source,
        )
        self.assertIn(
            'move /y "%SCOUT_BOOTSTRAP%.tmp" "%SCOUT_BOOTSTRAP%"',
            source,
        )

    def test_updater_recovers_itself_after_fast_forward_before_relaunch(self) -> None:
        source = UPDATER.read_text()
        self.assertIn("function Ensure-ScoutBootstrap", source)
        self.assertIn(
            'Invoke-Git @("show", "origin/main:scripts/start_scout_auto_update.ps1")',
            source,
        )
        self.assertIn(
            '$temporaryPath = $Path + ".restore-" + [Guid]::NewGuid().ToString("N") + ".tmp"',
            source,
        )
        self.assertIn(
            "Move-Item -LiteralPath $temporaryPath -Destination $Path -Force",
            source,
        )
        self.assertIn(
            '$updatedBootstrap = Join-Path $RepoRoot "scripts\\start_scout_auto_update.ps1"',
            source,
        )
        ensure_index = source.index("Ensure-ScoutBootstrap $updatedBootstrap")
        relaunch_index = source.index(
            "& powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass "
            "-File $updatedBootstrap -SkipUpdate"
        )
        self.assertLess(ensure_index, relaunch_index)

    def test_updater_waits_for_windows_filesystem_before_restoring(self) -> None:
        source = UPDATER.read_text()
        function_start = source.index("function Ensure-ScoutBootstrap")
        function_end = source.index("Set-Location -LiteralPath $RepoRoot", function_start)
        recovery = source[function_start:function_end]
        self.assertIn("for ($attempt = 1; $attempt -le 10; $attempt++)", recovery)
        self.assertIn("Start-Sleep -Milliseconds 100", recovery)
        self.assertIn("Test-Path -LiteralPath $Path -PathType Leaf", recovery)

    def test_keyword_cmd_uses_shared_self_updating_launcher(self) -> None:
        source = KEYWORD_CMD.read_text()
        self.assertIn("scripts\\start_scout_auto_update.ps1", source)
        self.assertIn("-KeywordMode", source)
        self.assertIn("Stage 0 Keyword Scout", source)

    def test_keyword_mode_survives_fast_forward_relaunch(self) -> None:
        source = UPDATER.read_text()
        self.assertIn("if ($KeywordMode)", source)
        self.assertIn(
            "-File $updatedBootstrap -SkipUpdate -KeywordMode",
            source,
        )

    def test_keyword_mode_reuses_agent_credentials_without_stage1_claims(self) -> None:
        source = UPDATER.read_text()
        self.assertIn("$KeywordScoutPath", source)
        self.assertIn('"--agent-id", $agentId', source)
        self.assertIn("$env:RRUGC_SCOUT_TOKEN = $token", source)
        self.assertIn("Stage 1 claim lane  : not used", source)


if __name__ == "__main__":
    unittest.main()
