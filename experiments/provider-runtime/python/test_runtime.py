"""Exercise the experimental executable; source fallback uses the same entrypoint."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_host import PLUGIN, REQUEST


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        binary = os.getenv("DECISION_PROTOTYPE_EXECUTABLE")
        self.command = [binary] if binary else [sys.executable, str(Path(__file__).with_name("host.py"))]
        self.environment = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG") if key in os.environ}
        self.environment["DECISION_API_KEY"] = "fixture-key"
        if binary:
            self.environment.update(PATH="/nonexistent", PYTHONHOME="/nonexistent", PYTHONPATH="/nonexistent")
        self.package = self.root / "package"
        self.package.mkdir()
        (self.package / "provider.py").write_text(PLUGIN)
        (self.package / "decision-provider.json").write_text(json.dumps({
            "protocol_version": 1, "name": "fixture", "version": "1.0.0",
            "command": [sys.executable, "provider.py"], "default_base_url": "https://example.test"}))

    def invoke(self, *args, data=None):
        return subprocess.run([*self.command, *args], input=json.dumps(data) if data else None,
                              capture_output=True, text=True, env=self.environment, timeout=20, cwd=self.root)

    def test_version_dry_run_and_external_evaluation_without_host_python(self):
        version = self.invoke("--version")
        self.assertEqual((version.returncode, version.stdout), (0, "decision prototype 1\n"))
        preview = self.invoke("run", "-", "--dry-run", "--json", data=REQUEST)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(json.loads(preview.stdout), REQUEST)
        install = self.invoke("providers", "install", str(self.package), "--plugin-home", str(self.home))
        self.assertEqual(install.returncode, 0, install.stderr)
        result = self.invoke("run", "-", "--json", "--provider", "fixture", "--plugin-home", str(self.home), data=REQUEST)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["answers"]["q"]["noul"], .75)
        disabled = self.invoke("run", "-", "--provider", "fixture", "--disable-provider", "fixture", "--plugin-home", str(self.home), data=REQUEST)
        self.assertEqual(disabled.returncode, 2)
        self.assertEqual(disabled.stdout, "")

    def test_default_and_management_mcp_are_separate(self):
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        async def check(management):
            command = [*self.command[1:], "mcp", "--plugin-home", str(self.home)]
            if management:
                command.append("--management")
            parameters = StdioServerParameters(command=self.command[0], args=command, env=self.environment, cwd=self.root)
            with tempfile.TemporaryFile(mode="w+") as diagnostics:
                async with stdio_client(parameters, errlog=diagnostics) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=20) as session:
                        await session.initialize()
                        names = {tool.name for tool in (await session.list_tools()).tools}
                        self.assertEqual(names, {"provider_install", "provider_list", "provider_remove"} if management else {"evaluate"})
                        if management:
                            installed = await session.call_tool("provider_install", {"source": str(self.package)})
                            self.assertFalse(installed.is_error)
                            listing = await session.call_tool("provider_list", {})
                            self.assertEqual(json.loads(listing.content[0].text), [{"name": "fixture", "version": "1.0.0"}])
                        else:
                            invalid = await session.call_tool("evaluate", dict(REQUEST, base_url="https://unselected.test"))
                            self.assertTrue(invalid.is_error)
                diagnostics.seek(0)
                self.assertNotIn("fixture-key", diagnostics.read())
        asyncio.run(check(False))
        asyncio.run(check(True))


if __name__ == "__main__":
    unittest.main()
