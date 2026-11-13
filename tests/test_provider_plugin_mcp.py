"""External entry-point CLI/MCP conformance, including clean wheel installs."""

import asyncio
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from test_images import png_bytes
from transport import command, isolate, working_directory

ROOT = Path(__file__).resolve().parents[1]
REQUEST = {"state": "context", "questions": {"q": {"type": "noul", "instructions": "True?"}}}
MODE = os.getenv("DECISION_TEST_MODE", "standalone" if os.getenv("DECISION_TEST_EXECUTABLE") else "source")


@unittest.skipIf(MODE == "standalone", "external providers require a Python installation")
class ExternalProviderTransportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="decision-external-provider-")
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name)
        self.requests = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, self.headers.get("Authorization"), body))
                result = {"model": body["model"], "answers": {key: {"type": "noul", "noul": .75} for key in body["questions"]},
                          "usage": {"input_tokens": 0}, "duration_ms": 999999}
                mode = body["state"]
                if mode == "invalid":
                    result["answers"]["q"]["noul"] = "fixture-secret private response"
                self.send_response(302 if mode == "redirect" else 200)
                self.send_header("Content-Type", "application/json")
                if mode == "redirect":
                    self.send_header("Location", "/must-not-follow")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.environment = isolate(dict(os.environ, DECISION_PROVIDER="fixture", DECISION_MODEL="server-model",
                                       DECISION_BASE_URL=f"http://127.0.0.1:{self.server.server_port}/api",
                                       DECISION_API_KEY_ENV="EXTERNAL_KEY", EXTERNAL_KEY="fixture-secret",
                                       DECISION_ACCOUNT_ID="", CLOUDFLARE_ACCOUNT_ID=""))
        if MODE == "source":
            # Source processes use the same real entry-point metadata format;
            # verify_wheel installs the separately built distribution instead.
            metadata = self.workspace / "decision_fixture_provider-1.0.0.dist-info"
            metadata.mkdir()
            (metadata / "METADATA").write_text("Metadata-Version: 2.3\nName: decision-fixture-provider\nVersion: 1.0.0\n")
            (metadata / "entry_points.txt").write_text("[decision.providers.v1]\nfixture = decision_fixture_provider:PROVIDER\nbroken = missing_decision_fixture:PROVIDER\n")
            self.environment["PYTHONPATH"] = os.pathsep.join((str(self.workspace), str(ROOT / "tests/fixtures/provider/src"), str(ROOT / "src")))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def run_cli(self, *args, payload=None):
        return subprocess.run([*command(), *args], input=json.dumps(payload) if payload else None,
                              env=self.environment, cwd=working_directory(), text=True, capture_output=True, timeout=30)

    def test_cli_discovery_defaults_images_errors_and_offline_exports(self):
        self.environment.pop("DECISION_MODEL")
        result = self.run_cli("run", "-", "--dry-run", "--json", payload=REQUEST)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["model"], "fixture-default")
        self.assertEqual(self.requests, [])
        image = self.workspace / "image.png"
        image.write_bytes(png_bytes())
        result = self.run_cli("run", "-", "--json", "--image", f"photo={image}", payload=REQUEST)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["usage"], {"input_tokens": 0, "output_tokens": None})
        self.assertLess(data["duration_ms"], 999999)
        path, key, body = self.requests[0]
        self.assertEqual((path, key, body["model"]), ("/api/evaluate", "Bearer fixture-secret", "fixture-default"))
        self.assertEqual(base64.b64decode(body["images"]["<decision-image:photo>"]), png_bytes())
        self.assertNotIn("assets", body)
        self.assertEqual(len(self.requests), 1)
        for mode in ("invalid", "redirect"):
            before = len(self.requests)
            result = self.run_cli("run", "-", "--json", payload={**REQUEST, "state": mode})
            self.assertEqual((result.returncode, result.stdout), (1, ""), result.stderr)
            self.assertNotIn("fixture-secret", result.stderr)
            self.assertNotIn("private response", result.stderr)
            self.assertEqual(len(self.requests), before + 1)
        result = self.run_cli("noul", "True?", "--state", "x", "--provider", "broken", "--dry-run")
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertNotIn("missing_decision_fixture", result.stderr)
        exported = self.workspace / "skill"
        result = self.run_cli("skill", "--output", str(exported))
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (ROOT / "skills/decision").rglob("*"):
            if path.is_file():
                self.assertEqual((exported / path.relative_to(ROOT / "skills/decision")).read_bytes(), path.read_bytes())

    def test_mcp_fixed_settings_constraints_model_override_and_recovery(self):
        from jsonschema import ValidationError, validate
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        async def check():
            cmd = command()
            parameters = StdioServerParameters(command=cmd[0], args=[*cmd[1:], "mcp", "--disable-provider", "broken"], env=self.environment, cwd=working_directory())
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=30) as session:
                    await session.initialize()
                    tool = (await session.list_tools()).tools[0]
                    validate(REQUEST, tool.input_schema)
                    result = await session.call_tool("evaluate", REQUEST)
                    self.assertFalse(result.is_error, result.content)
                    self.assertEqual(result.structured_content["model"], "server-model")
                    result = await session.call_tool("evaluate", {**REQUEST, "model": "per-call"})
                    self.assertFalse(result.is_error, result.content)
                    self.assertEqual(result.structured_content["model"], "per-call")
                    validate(result.structured_content, tool.output_schema)
                    for field in ("provider", "base_url", "api_key_env", "account_id", "disable_provider"):
                        invalid = {**REQUEST, field: "override"}
                        with self.assertRaises(ValidationError):
                            validate(invalid, tool.input_schema)
                        self.assertTrue((await session.call_tool("evaluate", invalid)).is_error)
                    too_many = {**REQUEST, "questions": {str(i): REQUEST["questions"]["q"] for i in range(3)}}
                    with self.assertRaises(ValidationError):
                        validate(too_many, tool.input_schema)
                    self.assertTrue((await session.call_tool("evaluate", too_many)).is_error)
                    self.assertEqual(len(self.requests), 2)
                    result = await session.call_tool("evaluate", {**REQUEST, "state": "invalid"})
                    self.assertTrue(result.is_error)
                    self.assertIsNone(result.structured_content)
                    self.assertNotIn("fixture-secret", result.content[0].text)
                    result = await session.call_tool("evaluate", REQUEST)
                    self.assertFalse(result.is_error, result.content)
                    self.assertEqual(result.structured_content["model"], "server-model")
                    self.assertTrue(all(path == "/api/evaluate" and key == "Bearer fixture-secret" for path, key, _ in self.requests))

        asyncio.run(check())

    def test_disabled_installed_plugin_rejects_cli_and_mcp_before_network(self):
        for args in (("run", "-", "--json"), ("run", "-", "--dry-run", "--json"), ("mcp",)):
            result = self.run_cli(*args, "--disable-provider", "fixture", payload=REQUEST)
            self.assertEqual((result.returncode, result.stdout), (2, ""))
            self.assertIn("disabled for this invocation", result.stderr)
        self.assertEqual(self.requests, [])
        result = self.run_cli("run", "-", "--disable-provider", "broken", "--disable-provider", "other", "--json", payload=REQUEST)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.requests), 1)


if __name__ == "__main__":
    unittest.main()
