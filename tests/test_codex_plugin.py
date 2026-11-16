"""Install both native export modes in real Codex, without inference or user config."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from codex_client import CodexClient
from native import command, environment, invoke


@unittest.skipUnless(os.getenv("DECISION_TEST_CODEX") == "1",
                     "Native Codex integration requires external CLI; set DECISION_TEST_CODEX=1")
class NativeCodexPluginTests(unittest.TestCase):
    def test_codex_installs_discovers_skill_and_loads_only_selected_mcp_integration(self):
        codex = shutil.which("codex")
        self.assertIsNotNone(codex, "Install the Codex CLI before running test_codex_plugin.py")
        executable = command()[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for mode in ("cli", "mcp"):
                with self.subTest(integration=mode):
                    host = root / mode
                    workspace, home, output = host / "workspace", host / "home", host / "export"
                    workspace.mkdir(parents=True)
                    (home / ".codex").mkdir(parents=True)
                    settings = {"HOME": str(home), "USERPROFILE": str(home),
                                "LOCALAPPDATA": str(home / "AppData/Local"), "APPDATA": str(home / "AppData/Roaming"),
                                "CODEX_HOME": str(home / ".codex"), "AGENTS_HOME": str(home / ".agents"),
                                "DECISION_PROVIDER": "cloudflare", "DECISION_MODEL": "clef-flash"}
                    isolated = environment(settings)

                    def run(*args):
                        return subprocess.run(list(map(str, args)), cwd=workspace, env=isolated, capture_output=True,
                                              text=True, encoding="utf-8", check=True, timeout=30).stdout

                    args = ["plugin", "--output", output, "--integration", mode]
                    if mode == "mcp":
                        args += ["--mcp-command", executable]
                    exported = invoke(*args, env=settings, cwd=workspace)
                    self.assertEqual(exported.returncode, 0, exported.stderr)
                    run(codex, "plugin", "marketplace", "add", output, "--json")
                    installed = json.loads(run(codex, "plugin", "add", "decision@decision-local", "--json"))
                    cache = Path(installed["installedPath"])
                    with CodexClient(codex, workspace, isolated) as server:
                        detail = server.call("plugin/read", {"pluginName": "decision",
                                                              "marketplacePath": str(output / ".agents/plugins/marketplace.json")})["plugin"]
                        self.assertEqual(detail["mcpServers"], ["decision"] if mode == "mcp" else [])
                        skills = server.call("skills/list", {"cwds": [str(workspace)], "forceReload": True})
                        self.assertFalse(skills["data"][0]["errors"])
                        loaded = [s for item in skills["data"] for s in item["skills"] if s.get("pluginId") == "decision@decision-local"]
                        self.assertEqual(len(loaded), 1)
                        self.assertTrue(loaded[0]["enabled"])
                        self.assertEqual(Path(loaded[0]["path"]).resolve(), (cache / "skills/decision/SKILL.md").resolve())
                        self.assertTrue((cache / "skills/decision/references/cloudflare-dog-request.json").is_file())
                        status = server.call("mcpServerStatus/list", {})
                        servers = [s for s in status["data"] if s["name"] == "decision"]
                        self.assertEqual(len(servers), int(mode == "mcp"))
                        if servers:
                            constraints = servers[0]["tools"]["evaluate"]["inputSchema"]["allOf"][0]["properties"]
                            self.assertEqual(constraints["questions"]["maxProperties"], 64)
                            self.assertEqual(constraints["model"]["enum"],
                                             ["clef", "clef-flash", "@cf/cloudflare/clef", "@cf/cloudflare/clef-flash"])


if __name__ == "__main__":
    unittest.main()
