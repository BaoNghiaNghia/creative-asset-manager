import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESKTOP_PACKAGE = ROOT / "apps" / "desktop" / "package.json"
UPDATER = ROOT / "apps" / "desktop" / "src" / "main" / "updater.ts"
NGINX = ROOT / "infrastructure" / "nginx" / "creative-asset-manager.conf"
PUBLISH = ROOT / "scripts" / "publish-cam-desktop-update.sh"


class DesktopUpdateDeploymentTests(unittest.TestCase):
    def test_desktop_package_has_generic_https_update_feed(self) -> None:
        package = json.loads(DESKTOP_PACKAGE.read_text())
        self.assertEqual(package["dependencies"]["electron-updater"], "6.6.2")
        publish = package["build"]["publish"]
        self.assertEqual(publish[0]["provider"], "generic")
        self.assertEqual(
            publish[0]["url"],
            "https://creative-assets.ddns.net/desktop-updates/windows/",
        )

    def test_packaged_app_checks_downloads_and_installs_updates(self) -> None:
        source = UPDATER.read_text()
        self.assertIn("if (!app.isPackaged)", source)
        self.assertIn("autoUpdater.autoDownload = true", source)
        self.assertIn("autoUpdater.autoInstallOnAppQuit = true", source)
        self.assertIn("autoUpdater.checkForUpdates()", source)
        self.assertIn("autoUpdater.quitAndInstall(true, true)", source)

    def test_nginx_serves_update_feed_from_dedicated_root(self) -> None:
        config = NGINX.read_text()
        self.assertIn("location ^~ /desktop-updates/windows/", config)
        self.assertIn(
            "alias /var/www/creative-asset-manager-desktop-updates/windows/current/;",
            config,
        )
        self.assertIn('Cache-Control "no-store, no-cache, must-revalidate"', config)

    def test_publish_script_activates_versioned_release_atomically(self) -> None:
        source = PUBLISH.read_text()
        self.assertIn('[[ -s "$SOURCE/latest.yml" ]]', source)
        self.assertIn('[[ -s "$SOURCE/$ARTIFACT.blockmap" ]]', source)
        self.assertLess(
            source.index('rsync -a --chmod=D755,F644'),
            source.index('install -o root -g root -m 0644 "$SOURCE/latest.yml"'),
        )
        self.assertIn('mv -Tf -- "$ROOT/current.new" "$ROOT/current"', source)


if __name__ == "__main__":
    unittest.main()
