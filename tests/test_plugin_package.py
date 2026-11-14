"""The Codex plugin package: manifest, MCP wiring and artwork stay self-contained."""
import json
import os
import subprocess
import struct
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class CodexPluginPackage(unittest.TestCase):
    def test_manifest_points_to_the_mcp_server_and_codex_artwork(self):
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())
        self.assertEqual((manifest["name"], manifest["version"]), ("breadcrumbs", "0.4.2"))
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        self.assertEqual(manifest["interface"]["logo"], "./assets/codex.png")
        self.assertEqual(manifest["interface"]["composerIcon"], "./assets/codex.png")

    def test_mcp_server_runs_the_project_scoped_launcher(self):
        config = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["breadcrumbs"]
        self.assertEqual((config["command"], config["cwd"]), ("sh", "."))
        self.assertEqual(config["args"], ["./bin/bc-mcp"])
        self.assertTrue(os.access(ROOT / "bin" / "bc-mcp", os.X_OK))

    def test_source_launcher_finds_shared_layers_without_pythonpath(self):
        env = dict(os.environ)
        for key in ("PYTHONPATH", "BREADCRUMBS_HOME"):
            env.pop(key, None)
        with tempfile.TemporaryDirectory() as directory:
            env["CODEX_HOME"] = directory
            result = subprocess.run(["sh", str(ROOT / "bin" / "bc-ace"), "status"], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ACE is disabled.", result.stdout)

    def test_plugin_artwork_is_a_small_png(self):
        png = (ROOT / "assets" / "codex.png").read_bytes()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", png[16:24]), (96, 96))
        self.assertLess(len(png), 20_000)
        self.assertEqual(png, (ROOT / "breadcrumbs_codex" / "icon.png").read_bytes())


if __name__ == "__main__":
    unittest.main()
