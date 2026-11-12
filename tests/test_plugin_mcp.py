"""Installed wheel and standalone plugin generation with a real MCP exchange."""

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_cloudflare_mcp as cloudflare_fixture
from test_openai_provider import QUESTIONS
from transport import command, working_directory


class PluginTransportTests(unittest.TestCase):
    def test_export_modes_and_mcp_credential_forwarding_use_installed_executable(self):
        fixture = cloudflare_fixture.CloudflareTransportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        executable = command()[0] if len(command()) == 1 else str(Path(sys.executable).with_name("decision"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for mode in ("cli", "mcp"):
                output = root / mode
                args = [*command(), "plugin", "--output", str(output), "--integration", mode]
                if mode == "mcp":
                    args += ["--mcp-command", executable, "--env-var", "CF_FIXTURE_KEY"]
                for suffix in ([], ["--check"]):
                    result = subprocess.run([*args, *suffix], env=fixture.environment, cwd=working_directory(),
                                            text=True, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                package = output / "plugins/decision"
                manifest = json.loads((package / ".codex-plugin/plugin.json").read_text())
                self.assertEqual("mcpServers" in manifest, mode == "mcp")
                self.assertEqual(fixture.requests, [])  # export never performs inference
                if mode == "mcp":
                    server = json.loads((package / ".mcp.json").read_text())["mcpServers"]["decision"]
                    forwarded = {name: value for name, value in fixture.environment.items()
                                 if name in server["env_vars"] or name in {"PATH", "PYTHONHOME", "PYTHONPATH"}}

                    async def check():
                        from mcp import ClientSession
                        from mcp.client.stdio import StdioServerParameters, stdio_client

                        parameters = StdioServerParameters(command=server["command"], args=server["args"],
                                                           env=forwarded, cwd=working_directory())
                        async with stdio_client(parameters) as (read, write):
                            async with ClientSession(read, write, read_timeout_seconds=30) as session:
                                await session.initialize()
                                tools = (await session.list_tools()).tools
                                self.assertEqual([tool.name for tool in tools], ["evaluate"])
                                result = await session.call_tool("evaluate", {"state": "dog", "questions": QUESTIONS})
                                self.assertFalse(result.is_error, result.content)
                                fixture.assert_result(result.structured_content)

                    asyncio.run(check())
                    fixture.assert_wire()
                    self.assertEqual(len(fixture.requests), 1)


if __name__ == "__main__":
    unittest.main()
