"""Native Codex-plugin generation and declared MCP credential forwarding."""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from native import command, environment, invoke, response_server
from transport import working_directory

QUESTIONS = {
    "present": {"type": "noul", "instructions": "Is a dog visible?"},
    "breed": {"type": "choice", "instructions": "Which breed?", "criteria": {"french": "French bulldog", "english": "English bulldog"}},
    "visible": {"type": "score", "instructions": "How visible?", "criteria": ["Hidden", "Visible"]},
}
ANSWERS = {
    "present": {"type": "noul", "noul": 0.9},
    "breed": {"type": "choice", "choice": "french", "confidence": 0.9,
              "probabilities": {"french": 0.9, "english": 0.1}},
    "visible": {"type": "score", "score": 1, "confidence": 0.9,
                "legend": {"0": "Hidden", "1": "Visible"}, "probabilities": {"0": 0.1, "1": 0.9}},
}
RESPONSE = {"success": True, "errors": [], "messages": [],
            "result": {"model": "clef", "usage": {"input_tokens": 10, "output_tokens": 0}, "answers": ANSWERS}}


class PluginTransportTests(unittest.TestCase):
    def test_export_modes_and_mcp_credential_forwarding_use_installed_executable(self):
        executable = command()[0]
        with tempfile.TemporaryDirectory() as directory, response_server(RESPONSE) as (url, requests):
            root = Path(directory)
            settings = {"DECISION_PROVIDER": "cloudflare", "DECISION_BASE_URL": url + "/custom/run",
                        "DECISION_API_KEY_ENV": "CF_FIXTURE_KEY", "CF_FIXTURE_KEY": "cf-fixture-secret",
                        "CLOUDFLARE_API_TOKEN": "do-not-forward", "OPENAI_API_KEY": "do-not-forward",
                        "DECISION_MODEL": "clef", "DECISION_ACCOUNT_ID": "", "CLOUDFLARE_ACCOUNT_ID": ""}
            for mode in ("cli", "mcp"):
                output = root / mode
                args = ["plugin", "--output", output, "--integration", mode]
                if mode == "mcp":
                    args += ["--mcp-command", executable, "--env-var", "CF_FIXTURE_KEY"]
                for suffix in ([], ["--check"]):
                    result = invoke(*args, *suffix, env=settings)
                    self.assertEqual(result.returncode, 0, result.stderr)
                package = output / "plugins/decision"
                manifest = json.loads((package / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
                self.assertEqual("mcpServers" in manifest, mode == "mcp")
                self.assertEqual(requests, [], "Export performed inference")
                if mode == "mcp":
                    server = json.loads((package / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["decision"]
                    self.assertEqual(server["command"], executable)
                    self.assertEqual(server["args"], ["mcp"])
                    self.assertIn("CF_FIXTURE_KEY", server["env_vars"])
                    self.assertNotIn("env", server)
                    forwarded = environment({name: value for name, value in settings.items() if name in server["env_vars"]})

                    async def check():
                        from mcp import ClientSession
                        from mcp.client.stdio import StdioServerParameters, stdio_client
                        parameters = StdioServerParameters(command=server["command"], args=server["args"],
                                                           env=forwarded, cwd=working_directory())
                        async with stdio_client(parameters) as (read, write):
                            async with ClientSession(read, write, read_timeout_seconds=30) as session:
                                await session.initialize()
                                self.assertEqual([tool.name for tool in (await session.list_tools()).tools], ["evaluate"])
                                result = await session.call_tool("evaluate", {"state": "dog", "questions": QUESTIONS})
                                self.assertFalse(result.is_error, result.content)
                                self.assertEqual(result.structured_content["answers"], ANSWERS)
                                self.assertEqual(result.structured_content["usage"], {"input_tokens": 10, "output_tokens": 0})
                                self.assertGreaterEqual(result.structured_content["duration_ms"], 0)

                    asyncio.run(check())
                    self.assertEqual(len(requests), 1)
                    request = requests[0]
                    self.assertEqual(request["path"], "/custom/run/@cf/cloudflare/clef")
                    headers = {name.lower(): value for name, value in request["headers"].items()}
                    self.assertEqual(headers["authorization"], "Bearer cf-fixture-secret")
                    self.assertEqual(request["body"]["model"], "clef")
                    self.assertEqual(request["body"]["questions"], QUESTIONS)
                    self.assertNotIn("assets", request["body"])
                    self.assertNotIn("duration_ms", request["body"])


if __name__ == "__main__":
    unittest.main()
