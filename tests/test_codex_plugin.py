"""Install both integration modes in real Codex, without inference or user config."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from codex_client import CodexClient
from decision_cli.plugin import ENV_VARS
from transport import command


@unittest.skipUnless(shutil.which("codex"), "requires Codex CLI; covered by the dedicated plugin CI job")
class NativeCodexPluginTests(unittest.TestCase):
    def test_codex_installs_discovers_skill_and_loads_only_selected_mcp_integration(self):
        codex = shutil.which("codex")
        executable = command()[0] if len(command()) == 1 else str(Path(sys.executable).with_name("decision"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for mode in ("cli", "mcp"):
                with self.subTest(integration=mode):
                    host = root / mode
                    workspace, home, output = host / "workspace", host / "home", host / "export"
                    workspace.mkdir(parents=True)
                    (home / ".codex").mkdir(parents=True)
                    environment = {key: value for key, value in os.environ.items() if key not in ENV_VARS}
                    environment.update(HOME=str(home), CODEX_HOME=str(home / ".codex"), AGENTS_HOME=str(home / ".agents"),
                                       DECISION_PROVIDER="cloudflare", DECISION_MODEL="clef-flash")

                    def run(*args):
                        return subprocess.run(args, cwd=workspace, env=environment, capture_output=True, text=True,
                                              check=True, timeout=30).stdout

                    args = [*command(), "plugin", "--output", str(output), "--integration", mode]
                    if mode == "mcp":
                        args += ["--mcp-command", executable]
                    run(*args)
                    run(codex, "plugin", "marketplace", "add", str(output), "--json")
                    installed = json.loads(run(codex, "plugin", "add", "decision@decision-local", "--json"))
                    cache = Path(installed["installedPath"])
                    with CodexClient(codex, workspace, environment) as server:
                        detail = server.call("plugin/read", {"pluginName": "decision",
                                                              "marketplacePath": str(output / ".agents/plugins/marketplace.json")})["plugin"]
                        self.assertEqual(detail["mcpServers"], ["decision"] if mode == "mcp" else [])
                        skills = server.call("skills/list", {"cwds": [str(workspace)], "forceReload": True})
                        self.assertFalse(skills["data"][0]["errors"])
                        loaded = [s for item in skills["data"] for s in item["skills"] if s.get("pluginId") == "decision@decision-local"]
                        self.assertEqual(len(loaded), 1)
                        self.assertTrue(loaded[0]["enabled"])
                        self.assertEqual(Path(loaded[0]["path"]), cache / "skills/decision/SKILL.md")
                        self.assertTrue((cache / "skills/decision/references/cloudflare-dog-request.json").is_file())
                        status = server.call("mcpServerStatus/list", {})
                        servers = [s for s in status["data"] if s["name"] == "decision"]
                        self.assertEqual(len(servers), int(mode == "mcp"))
                        if servers:
                            tool = servers[0]["tools"]["evaluate"]
                            schema = tool["inputSchema"]
                            constraints = schema["allOf"][0]["properties"]
                            self.assertEqual(constraints["questions"]["maxProperties"], 64)
                            self.assertEqual(constraints["model"]["enum"],
                                             ["clef", "clef-flash", "@cf/cloudflare/clef", "@cf/cloudflare/clef-flash"])


if __name__ == "__main__":
    unittest.main()
