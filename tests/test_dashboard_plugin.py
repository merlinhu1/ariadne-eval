import json
import tempfile
import unittest
from pathlib import Path

from agent_health.dashboard_plugin import install_dashboard_plugin


class DashboardPluginInstallTest(unittest.TestCase):
    def test_install_dashboard_plugin_copies_manifest_api_and_assets_under_hermes_plugins(self):
        with tempfile.TemporaryDirectory() as tmp:
            installed = install_dashboard_plugin(Path(tmp))

            self.assertEqual(installed, Path(tmp) / "plugins" / "ariadne-eval" / "dashboard")
            manifest = json.loads((installed / "manifest.json").read_text())
            self.assertEqual(manifest["name"], "ariadne-eval")
            self.assertEqual(manifest["tab"]["path"], "/ariadne-eval")
            self.assertTrue((installed / "plugin_api.py").exists())
            self.assertTrue((installed / "dist" / "index.js").exists())
            self.assertTrue((installed / "dist" / "style.css").exists())


if __name__ == "__main__":
    unittest.main()
